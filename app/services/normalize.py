"""Turn raw log lines into stable, comparable text for embedding.

Two incidents of the same problem almost never produce byte-identical logs —
the ids, ports, timestamps and paths differ every time. Left alone, every
occurrence embeds to a slightly different point and memory never matches
anything. So the varying parts get replaced with placeholders.

The trap is that some numbers *are* the signal. `ORA-01555`, `errno 111` and
`HTTP 503` are the most searchable, most diagnostic tokens in the whole line;
blanking them to `<NUM>` throws away exactly what makes the case identifiable.
Those are protected before normalisation and restored afterwards.
"""
from __future__ import annotations

import re

# ── Error codes that must survive normalisation ─────────────────────────────
# Add to this list rather than loosening the rules below — a bare 5-digit code
# is indistinguishable from a port or a row count, so only codes with a
# recognisable prefix or shape are safe to protect.
_PROTECTED = re.compile(
    r"""(
          ORA-\d{4,5}                    # Oracle
        | MY-\d{6}                       # MySQL 8 error-log code
        | PG-\d+                         # (rare, but seen in wrappers)
        | errno[\s=:]*\d+                # errno 111, errno=28
        | \bHTTP[\s/]?\d{3}\b            # HTTP 503, HTTP/500
        | \bERROR\s\d{4}\s\(\w{5}\)      # MySQL client: ERROR 1213 (40001)
        | \bSQLSTATE[\s:\[]*\w{5}\]?     # SQLSTATE[40001]
        | \bE\d{2,5}\b                   # E11000 (MongoDB duplicate key)
        | \b(?:ENOSPC|ECONNREFUSED|ETIMEDOUT|EACCES|EPIPE|ENOENT|EAGAIN)\b
        | \bexit\s(?:code\s)?\d{1,3}\b   # exit code 137 (OOM), exit 1
        | \bsignal\s\d{1,2}\b            # signal 9
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Order matters: the specific patterns have to run before the generic number
# sweep, or they get eaten by it.
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?", re.I)
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TIME = re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b")
_IP_PORT = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}\b")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-f]{0,4}:){3,7}[0-9a-f]{0,4}\b", re.I)
_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{12,}\b", re.I)
_SIZE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:[KMGT]i?B|ms|us|ns|sec|s)\b", re.I)
_PATH = re.compile(r"(?:/[\w.\-]+){2,}/?")
_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")

_PLACEHOLDER = "\x00P{}\x00"   # NUL can't appear in a log line we care about


def normalize_message(msg: str) -> str:
    """Replace the parts that change between occurrences of the same incident.

    `normalize_message("Deadlock on order 88123 from 10.0.0.5:5432")` and the
    same line with different numbers collapse to one string, so they embed to
    the same point and memory can actually match them.
    """
    if not msg:
        return ""

    # Park the diagnostic codes out of reach of the sweeps below.
    protected: list[str] = []

    def _park(m: re.Match) -> str:
        protected.append(m.group(0))
        return _PLACEHOLDER.format(len(protected) - 1)

    text = _PROTECTED.sub(_park, msg)

    text = _UUID.sub("<UUID>", text)
    text = _ISO_TS.sub("<TS>", text)
    text = _DATE.sub("<DATE>", text)
    text = _TIME.sub("<TIME>", text)
    text = _IP_PORT.sub("<IP>:<PORT>", text)
    text = _IP.sub("<IP>", text)
    text = _IPV6.sub("<IP>", text)
    text = _HEX.sub("<HEX>", text)
    text = _SIZE.sub("<SIZE>", text)
    # Paths: keep the shape, drop the varying numeric parts inside it.
    text = _PATH.sub(lambda m: _NUM.sub("<NUM>", m.group(0)), text)
    text = _NUM.sub("<NUM>", text)

    # Put the codes back.
    for i, original in enumerate(protected):
        text = text.replace(_PLACEHOLDER.format(i), original)

    return re.sub(r"\s+", " ", text).strip()


def extract_error_codes(msg: str) -> list[str]:
    """Pull out the diagnostic codes in a line, in the order they appear.

    These are the highest-value tokens a line contains: `ORA-01555` names one
    specific failure, while the surrounding prose ("snapshot too old on
    tablespace") describes a whole family of them. Useful both as a search term
    and as a sparse-index signal.
    """
    if not msg:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _PROTECTED.finditer(msg):
        code = " ".join(m.group(0).split())
        if code.lower() not in seen:
            seen.add(code.lower())
            out.append(code)
    return out


def normalize_messages(msgs: list[str], limit: int | None = None) -> list[str]:
    """Normalise many lines, dropping duplicates that collapse onto each other.

    Deduping is the point: twenty variations of one error become one line, which
    is what makes the sample in `build_symptom_text` representative instead of
    twenty copies of the noisiest message.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in msgs:
        norm = normalize_message(m)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
            if limit and len(out) >= limit:
                break
    return out


def build_symptom_text(
    host: str,
    service: str | None,
    status: str,
    health_score: float,
    top_keywords: list[str] | None = None,
    frames: list[dict] | None = None,
    anomaly_score: float | None = None,
    error_msgs: list[str] | None = None,
    sample_errors: int = 3,
) -> str:
    """Compose the text A4 embeds and searches on.

    This is the single thing that decides whether memory retrieval works, so it
    carries both the structured facts (service, status, frames) and the raw
    error wording — queries arrive in both shapes.
    """
    lines = [f"[{service or 'unknown'}@{host}] status={status} health={health_score:.0f}"]

    if top_keywords:
        # dedupe, keep order — frames often repeat the same keyword
        seen: set[str] = set()
        kws = [k for k in top_keywords if not (k in seen or seen.add(k))]
        lines.append(f"top_keywords: {', '.join(kws[:12])}")

    relevant = [f for f in (frames or []) if f.get("relevance", 0) > 0.5]
    if relevant:
        lines.append("frames: " + ", ".join(
            f"{f['frame']} {f['relevance']:.2f}" for f in relevant
        ))

    if anomaly_score is not None:
        lines.append(f"anomaly_score: {anomaly_score:.2f}")

    samples = normalize_messages(error_msgs or [], limit=sample_errors)
    if samples:
        lines.append("sample_errors:")
        lines.extend(f"  {s}" for s in samples)

    return "\n".join(lines)
