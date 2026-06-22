"""
A3 — MiroFish multi-perspective analysis
5 expert frames วิ่ง parallel asyncio.gather()
แต่ละ frame ฉีด POS context + lens ต่างกัน
Critic synthesize → score + cluster frames

Frames:
  1. Security    — auth failures, privilege abuse, intrusion patterns
  2. Database    — slow queries, lock contention, connection pool, replication lag
  3. Network     — latency, packet loss, firewall drops, routing issues
  4. Hardware    — disk I/O, memory pressure, CPU spike, NIC errors
  5. Software    — app crashes, memory leaks, restart loops, dependency failures
"""

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ─── Frame definitions ────────────────────────────────────────────────────────

@dataclass
class Frame:
    name: str
    lens: str                       # expert perspective label
    signal_keys: list[str]          # signal types this frame cares about
    keywords: list[str]             # keywords to scan in log messages
    pos_context: str                # domain context injected into LLM prompt


FRAMES: list[Frame] = [
    Frame(
        name="Security",
        lens="security_analyst",
        signal_keys=["auth_fail", "crash"],
        keywords=[
            "login failed", "authentication", "unauthorized", "forbidden",
            "access denied", "privilege", "sql injection", "brute force",
            "invalid password", "account locked", "401", "403",
        ],
        pos_context=(
            "You are a POS security analyst. Focus on unauthorized access, "
            "credential attacks, and privilege escalation in retail payment systems."
        ),
    ),
    Frame(
        name="Database",
        lens="dba",
        signal_keys=["db_err", "crash"],
        keywords=[
            "slow query", "lock wait", "deadlock", "connection pool",
            "pool exhausted", "timeout", "innodb", "replication",
            "disk full", "ibdata", "gone away", "too many connections",
        ],
        pos_context=(
            "You are a DBA specializing in POS transaction databases (MySQL/MSSQL). "
            "Focus on query performance, locking, and data integrity issues."
        ),
    ),
    Frame(
        name="Network",
        lens="network_engineer",
        signal_keys=["network_err", "payment_fail"],
        keywords=[
            "timeout", "connection refused", "upstream", "502", "503",
            "gateway", "dns", "packet loss", "firewall", "mpls",
            "payment gateway", "network unreachable", "rst",
        ],
        pos_context=(
            "You are a network engineer for a retail POS chain. "
            "Focus on WAN connectivity, payment gateway reachability, and latency spikes."
        ),
    ),
    Frame(
        name="Hardware",
        lens="systems_engineer",
        signal_keys=["hardware_err", "crash"],
        keywords=[
            "disk error", "oom", "out of memory", "kill process",
            "ext4-fs error", "i/o error", "hardware failure", "ecc",
            "temperature", "fan", "nvme", "raid", "no space left",
        ],
        pos_context=(
            "You are a systems engineer. Focus on physical hardware degradation, "
            "storage failures, and OS-level resource exhaustion in POS servers."
        ),
    ),
    Frame(
        name="Software",
        lens="senior_sre",
        signal_keys=["app_crash", "crash"],
        keywords=[
            "segfault", "exception", "stack overflow", "heap", "oom",
            "restart", "crash", "panic", "fatal error", "unhandled",
            "systemd", "service failed", "exit code",
        ],
        pos_context=(
            "You are a senior SRE. Focus on application crashes, memory leaks, "
            "restart loops, and software dependency failures in POS middleware."
        ),
    ),
]


# ─── Frame scoring ────────────────────────────────────────────────────────────

@dataclass
class FrameScore:
    frame: str
    lens: str
    relevance: float            # 0–1: how relevant this frame is
    signal_hits: int            # count of matching signal_keys
    keyword_hits: int           # count of matching keywords in messages
    top_keywords: list[str] = field(default_factory=list)
    llm_insight: str | None = None   # filled by LLM if relevance high enough


def _score_frame(
    frame: Frame,
    signal_counts: dict[str, int],
    top_error_msgs: list[str],
) -> FrameScore:
    signal_hits = sum(signal_counts.get(k, 0) for k in frame.signal_keys)

    msgs_combined = " ".join(top_error_msgs).lower()
    matched_kws = [kw for kw in frame.keywords if kw.lower() in msgs_combined]
    keyword_hits = len(matched_kws)

    # relevance: weighted combination (signals count more than keyword matches)
    relevance = min(1.0, signal_hits * 0.25 + keyword_hits * 0.10)

    return FrameScore(
        frame=frame.name,
        lens=frame.lens,
        relevance=round(relevance, 3),
        signal_hits=signal_hits,
        keyword_hits=keyword_hits,
        top_keywords=matched_kws[:5],
    )


# ─── LLM enrichment (optional) ───────────────────────────────────────────────

def _build_frame_prompt(
    frame: Frame,
    host: str,
    health_score: float,
    top_error_msgs: list[str],
    frame_score: FrameScore,
) -> str:
    msgs_block = "\n".join(f"  - {m}" for m in top_error_msgs[:10])
    return (
        f"{frame.pos_context}\n\n"
        f"Host: {host}  Health: {health_score:.0f}/100\n"
        f"Top errors:\n{msgs_block}\n\n"
        f"Matched signals: {frame_score.signal_hits} | "
        f"Keywords: {', '.join(frame_score.top_keywords)}\n\n"
        "In 1–2 sentences: what is the most likely root cause from your expert perspective? "
        "Be specific. If not relevant to your domain, say 'No relevant signals for this frame.'"
    )


async def _enrich_frame(
    frame: Frame,
    frame_score: FrameScore,
    host: str,
    health_score: float,
    top_error_msgs: list[str],
    ollama_generate,           # callable (prompt, model, base_url, timeout, temperature) → str
    model: str,
    base_url: str,
    timeout: float,
    temperature: float,
    min_relevance: float = 0.1,
) -> FrameScore:
    if frame_score.relevance < min_relevance:
        return frame_score

    prompt = _build_frame_prompt(frame, host, health_score, top_error_msgs, frame_score)
    try:
        raw = await ollama_generate(
            prompt=prompt,
            model=model,
            base_url=base_url,
            timeout=timeout,
            temperature=temperature,
        )
        frame_score.llm_insight = raw.strip()[:500] if raw else None
    except Exception as exc:
        logger.debug("MiroFish LLM failed for frame %s: %s", frame.name, exc)
    return frame_score


# ─── Main entry point ─────────────────────────────────────────────────────────

async def analyze(
    host: str,
    health_score: float,
    signal_counts: dict[str, int],
    top_error_msgs: list[str],
    ollama_generate=None,
    model: str = "",
    base_url: str = "",
    timeout: float = 30.0,
    temperature: float = 0.1,
    use_llm: bool = False,
) -> list[dict]:
    """
    Run all 5 frames in parallel.
    Returns list of frame dicts sorted by relevance desc.
    """
    # Score all frames (pure Python, instant)
    frame_scores = [
        _score_frame(f, signal_counts, top_error_msgs) for f in FRAMES
    ]

    # Enrich with LLM in parallel (only relevant frames)
    if use_llm and ollama_generate and model:
        tasks = [
            _enrich_frame(
                frame=FRAMES[i],
                frame_score=frame_scores[i],
                host=host,
                health_score=health_score,
                top_error_msgs=top_error_msgs,
                ollama_generate=ollama_generate,
                model=model,
                base_url=base_url,
                timeout=timeout,
                temperature=temperature,
            )
            for i in range(len(FRAMES))
        ]
        frame_scores = list(await asyncio.gather(*tasks))

    result = sorted(
        [
            {
                "frame":        fs.frame,
                "lens":         fs.lens,
                "relevance":    fs.relevance,
                "signal_hits":  fs.signal_hits,
                "keyword_hits": fs.keyword_hits,
                "top_keywords": fs.top_keywords,
                "insight":      fs.llm_insight,
            }
            for fs in frame_scores
        ],
        key=lambda x: x["relevance"],
        reverse=True,
    )
    return result


def top_frame(frames: list[dict]) -> dict | None:
    relevant = [f for f in frames if f["relevance"] > 0]
    return relevant[0] if relevant else None
