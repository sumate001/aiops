import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/router";

type ResultRow = {
  id: number;
  tenant_id: string;
  analyzed_at: string;
  health_score: number;
  status: string;
  host_count: number;
  critical_count: number;
  summary: string;
};

const healthColor = (s: number) =>
  s >= 80 ? "text-green-400" : s >= 50 ? "text-yellow-400" : "text-red-400";

const statusBadge = (s: string) =>
  s === "critical"
    ? "bg-red-900 text-red-300"
    : s === "warning"
    ? "bg-yellow-900 text-yellow-300"
    : "bg-green-900 text-green-300";

export default function Results() {
  const router = useRouter();
  const selectedId = router.query.id ? Number(router.query.id) : null;

  const [rows, setRows] = useState<ResultRow[]>([]);
  const [detail, setDetail] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    fetch("/api/results?limit=50")
      .then((r) => r.json())
      .then((d) => { setRows(d.results || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    setDetailLoading(true);
    fetch(`/api/results/${selectedId}`)
      .then((r) => r.json())
      .then((d) => { setDetail(d); setDetailLoading(false); })
      .catch(() => setDetailLoading(false));
  }, [selectedId]);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Analysis Results</h1>
          <p className="text-gray-400 text-sm mt-1">Recent pipeline outputs</p>
        </div>
        <nav className="flex gap-4 text-sm">
          <Link href="/" className="text-gray-400 hover:text-white">Dashboard</Link>
          <Link href="/results" className="text-blue-400 font-medium">Results</Link>
          <Link href="/settings" className="text-gray-400 hover:text-white">Settings</Link>
        </nav>
      </div>

      <div className="flex gap-6 h-[calc(100vh-120px)]">
        {/* Left: result list */}
        <div className="w-80 flex-shrink-0 bg-gray-900 rounded-xl overflow-y-auto">
          {loading ? (
            <p className="text-gray-500 text-sm p-4">Loading...</p>
          ) : rows.length === 0 ? (
            <p className="text-gray-500 text-sm p-4">No results yet</p>
          ) : (
            rows.map((r) => (
              <div
                key={r.id}
                onClick={() => router.push(`/results?id=${r.id}`, undefined, { shallow: true })}
                className={`p-4 border-b border-gray-800 cursor-pointer hover:bg-gray-800 transition-colors ${
                  selectedId === r.id ? "bg-gray-800 border-l-2 border-l-blue-500" : ""
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-base font-bold ${healthColor(r.health_score)}`}>
                    {Math.round(r.health_score)}
                  </span>
                  <span className={`text-xs font-bold uppercase px-1.5 py-0.5 rounded ${statusBadge(r.status)}`}>
                    {r.status}
                  </span>
                  <span className="text-xs text-gray-500 ml-auto">
                    {new Date(r.analyzed_at).toLocaleTimeString()}
                  </span>
                </div>
                <p className="text-xs text-gray-400">{r.tenant_id}</p>
                <p className="text-xs text-gray-500 truncate mt-0.5">{r.summary}</p>
              </div>
            ))
          )}
        </div>

        {/* Right: detail */}
        <div className="flex-1 bg-gray-900 rounded-xl overflow-y-auto p-5">
          {!selectedId ? (
            <p className="text-gray-500 text-sm">Select a result to view details</p>
          ) : detailLoading ? (
            <p className="text-gray-500 text-sm">Loading...</p>
          ) : !detail ? (
            <p className="text-gray-500 text-sm">Not found</p>
          ) : (
            <DetailView data={detail} resultId={selectedId} />
          )}
        </div>
      </div>
    </div>
  );
}

function DetailView({ data, resultId }: { data: any; resultId: number | null }) {
  const [tab, setTab] = useState<"overview" | "hosts" | "raw">("overview");

  return (
    <div>
      {/* Summary */}
      <div className="flex items-center gap-4 mb-4">
        <span className={`text-3xl font-bold ${healthColor(data.health_score)}`}>
          {Math.round(data.health_score)}
        </span>
        <div>
          <span className={`text-sm font-bold uppercase px-2 py-1 rounded ${statusBadge(data.status)}`}>
            {data.status}
          </span>
          <p className="text-xs text-gray-400 mt-1">{data.tenant_id} · {new Date(data.analyzed_at).toLocaleString()}</p>
        </div>
      </div>
      <p className="text-sm text-gray-300 mb-4">{data.summary}</p>

      {/* Tabs */}
      <div className="flex gap-2 mb-4 border-b border-gray-800 pb-2">
        {(["overview", "hosts", "raw"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`text-xs px-3 py-1.5 rounded-lg capitalize transition-colors ${
              tab === t ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab data={data} />}
      {tab === "hosts" && <HostsTab hosts={data.hosts} resultId={resultId} />}
      {tab === "raw" && (
        <pre className="text-xs text-gray-400 bg-gray-800 rounded-lg p-4 overflow-auto max-h-[60vh] whitespace-pre-wrap">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

function OverviewTab({ data }: { data: any }) {
  const hosts: any[] = data.hosts ?? [];
  const topHost = [...hosts].sort((a, b) => a.health_score - b.health_score)[0];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Hosts" value={String(hosts.length)} />
        <Stat label="Critical" value={String(hosts.filter((h) => h.status === "critical").length)} />
        <Stat label="Ollama used" value={data.sources?.ollama_used ? "yes" : "no"} />
      </div>
      {topHost && (
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-xs text-gray-400 mb-2">Worst Host</p>
          <p className="text-sm font-bold text-white">{topHost.host}</p>
          <p className="text-xs text-gray-400 mt-1">
            Score: <span className={healthColor(topHost.health_score)}>{Math.round(topHost.health_score)}</span>
            {" · "}{topHost.error_count} errors · {topHost.warn_count} warns
          </p>
          {topHost.synthesis && (
            <div className="mt-3">
              <p className="text-xs text-gray-500 mb-1">AA Synthesis · {topHost.synthesis.top_frame} · confidence {Math.round((topHost.synthesis.confidence ?? 0) * 100)}%</p>
              <ul className="text-xs text-gray-300 list-disc list-inside space-y-0.5">
                {topHost.synthesis.root_cause_chain?.map((c: string, i: number) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
              {topHost.synthesis.fix_steps?.length > 0 && (
                <div className="mt-2">
                  <p className="text-xs text-gray-500 mb-1">Fix steps</p>
                  <ol className="text-xs text-gray-300 list-decimal list-inside space-y-0.5">
                    {topHost.synthesis.fix_steps.map((s: string, i: number) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function HostsTab({ hosts, resultId }: { hosts: any[]; resultId: number | null }) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div className="space-y-2">
      {(hosts ?? []).map((h) => (
        <div key={h.host} className="bg-gray-800 rounded-lg overflow-hidden">
          <button
            className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-700 transition-colors"
            onClick={() => setOpen(open === h.host ? null : h.host)}
          >
            <span className={`text-base font-bold w-10 ${healthColor(h.health_score)}`}>
              {Math.round(h.health_score)}
            </span>
            <span className={`text-xs font-bold uppercase px-1.5 py-0.5 rounded ${statusBadge(h.status)}`}>
              {h.status}
            </span>
            <span className="text-sm text-white font-medium flex-1">{h.host}</span>
            <span className="text-xs text-gray-500">{h.error_count}e {h.warn_count}w</span>
            <span className="text-gray-500 text-xs">{open === h.host ? "▲" : "▼"}</span>
          </button>
          {open === h.host && (
            <div className="px-4 pb-4 border-t border-gray-700">
              {h.top_errors?.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs text-gray-400 mb-1">Top errors</p>
                  <ul className="text-xs text-gray-300 space-y-0.5">
                    {h.top_errors.map((e: any, i: number) => (
                      <li key={i}><span className="text-red-400 mr-1">×{e.count}</span>{e.msg}</li>
                    ))}
                  </ul>
                </div>
              )}
              {h.mirofish?.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs text-gray-400 mb-1">MiroFish frames</p>
                  <div className="grid grid-cols-5 gap-1">
                    {h.mirofish.map((f: any) => (
                      <div key={f.frame} className="bg-gray-900 rounded p-2 text-center">
                        <p className="text-xs text-gray-300 font-medium">{f.frame}</p>
                        <p className="text-xs text-blue-400">{f.relevance.toFixed(2)}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {h.synthesis && (
                <div className="mt-3">
                  <p className="text-xs text-gray-400 mb-1">AA Synthesis</p>
                  <ul className="text-xs text-gray-300 list-disc list-inside space-y-0.5">
                    {h.synthesis.root_cause_chain?.map((c: string, i: number) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}
              <ForecastPanel bands={h.forecast} />
              <MemoryPanel hits={h.memory_hits} />
              {/* Per host, not once per page: an analysis covers several hosts
                  and each has its own memory point, so one verdict can't stand
                  for all of them. */}
              <FeedbackBox resultId={resultId} host={h.host} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-800 rounded-lg p-3 text-center">
      <p className="text-xl font-bold text-white">{value}</p>
      <p className="text-xs text-gray-400 mt-0.5">{label}</p>
    </div>
  );
}

// ─── A4 memory ──────────────────────────────────────────────────────────────

type ForecastBand = {
  metric: string;
  p10: number;
  p50: number;
  p90: number;
  actual: number | null;
  breach: boolean;
  breach_magnitude: number;
};

function ForecastPanel({ bands }: { bands?: ForecastBand[] }) {
  if (!bands?.length) return null;

  return (
    <div className="mt-4">
      <p className="text-xs text-gray-400 mb-1">
        📈 เทียบกับพฤติกรรมของ host นี้เองในอดีต ณ เวลาเดียวกัน (A1b)
      </p>
      <div className="space-y-1.5">
        {bands.map((b) => {
          // The band is the whole point: without seeing where the value sits
          // inside it, "breach: true" is just another opaque alert. Clamp the
          // marker so a value far outside still renders at the edge.
          const lo = Math.min(b.p10, b.actual ?? b.p10);
          const hi = Math.max(b.p90, b.actual ?? b.p90);
          const span = hi - lo;
          // A metric that never varies gives p10 == p90 == actual. Mapping that
          // to 0% pins the marker at the far left, which reads as "far below the
          // band" when it is in fact exactly on target — so centre it instead.
          const pct = (v: number) =>
            span < 1e-9 ? "50%"
                        : `${Math.min(100, Math.max(0, ((v - lo) / span) * 100))}%`;
          return (
            <div key={b.metric} className="bg-gray-900 rounded p-2 border border-gray-800">
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-gray-300 w-28 shrink-0">{b.metric}</span>
                <span className={b.breach ? "text-amber-400" : "text-green-400"}>
                  {b.breach ? "หลุดกรอบ" : "อยู่ในกรอบ"}
                </span>
                {b.breach && (
                  <span className="text-gray-500">
                    ห่าง {b.breach_magnitude.toFixed(1)}× ความกว้างกรอบ
                  </span>
                )}
                <span className="text-gray-500 ml-auto">
                  คาด {b.p10.toFixed(1)}–{b.p90.toFixed(1)} · จริง {b.actual?.toFixed(1) ?? "-"}
                </span>
              </div>
              <div className="relative h-2 mt-1.5 bg-gray-800 rounded">
                <div
                  className="absolute h-2 bg-green-900/60 rounded"
                  style={{ left: pct(b.p10), width: `calc(${pct(b.p90)} - ${pct(b.p10)})` }}
                />
                <div className="absolute h-2 w-px bg-gray-500" style={{ left: pct(b.p50) }} />
                {b.actual != null && (
                  <div
                    className={`absolute -top-0.5 h-3 w-0.5 ${b.breach ? "bg-amber-400" : "bg-green-400"}`}
                    style={{ left: pct(b.actual) }}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-gray-600 mt-1.5">
        ค่าที่อยู่ในกรอบคือปกติสำหรับ host นี้ตามเวลานี้ ต่อให้ตัวเลขดูสูง —
        เช่น batch job ที่ทำ error พุ่งทุกคืนเวลาเดิม
      </p>
    </div>
  );
}

type MemoryHit = {
  point_id: string;
  kind: string;
  similarity: number;
  final_score: number;
  symptom_text: string;
  root_cause_chain: string[];
  fix_steps: string[];
  verified: boolean;
  actual_fix: string | null;
  occurrence_count: number;
  days_ago: number;
  title: string | null;
  verify_steps: string[];
  docs_url: string | null;
};

function MemoryPanel({ hits }: { hits?: MemoryHit[] }) {
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  if (!hits?.length) return null;

  const visible = hits.filter((h) => !hidden[h.point_id]);
  if (!visible.length) return null;

  const cases = visible.filter((h) => h.kind !== "playbook");
  const playbooks = visible.filter((h) => h.kind === "playbook");

  const deprecate = async (h: MemoryHit) => {
    setHidden((s) => ({ ...s, [h.point_id]: true }));
    await fetch(`/api/memory/${h.point_id}/deprecate`, { method: "POST" }).catch(() => {});
  };

  return (
    <div className="mt-4 space-y-3">
      {cases.length > 0 && (
        <div>
          <p className="text-xs text-gray-400 mb-1">🧠 เคยเจอมาก่อน</p>
          <div className="space-y-2">
            {cases.map((h) => (
              <div key={h.point_id} className="bg-gray-900 rounded-lg p-3 border border-gray-700">
                <div className="flex items-center gap-2 flex-wrap">
                  {/* Green tick only for a human-confirmed case — an unverified
                      entry is this system's own earlier guess and must not read
                      as settled fact. */}
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                    h.verified
                      ? "text-green-300 bg-green-950/40 border-green-800"
                      : "text-gray-400 bg-gray-800 border-gray-600"
                  }`}>
                    {h.verified ? "✓ ยืนยันแล้ว" : "ยังไม่ยืนยัน"}
                  </span>
                  <span className="text-[11px] text-gray-400">
                    {h.days_ago === 0 ? "วันนี้" : `${h.days_ago} วันก่อน`}
                    {" · "}
                    {/* similarity, not final_score: final_score is weighted for
                        ranking and can exceed 1.0, so showing it as a match
                        percentage would be a number the reader can't trust. */}
                    ตรงกัน {Math.round(h.similarity * 100)}%
                    {h.occurrence_count > 1 && ` · เจอซ้ำ ${h.occurrence_count} ครั้ง`}
                  </span>
                </div>
                <p className="text-[11px] text-gray-300 mt-1.5 whitespace-pre-wrap line-clamp-3">
                  {h.symptom_text}
                </p>
                {h.verified && h.actual_fix ? (
                  <p className="text-[11px] text-green-300 mt-1.5">
                    วิธีที่แก้ได้จริง: {h.actual_fix}
                  </p>
                ) : h.root_cause_chain?.length > 0 ? (
                  <p className="text-[11px] text-gray-400 mt-1.5">
                    สรุปตอนนั้น: {h.root_cause_chain[0]}
                  </p>
                ) : null}
                <div className="flex gap-3 mt-2">
                  <button
                    onClick={() => deprecate(h)}
                    className="text-[10px] text-gray-500 hover:text-red-400 transition-colors"
                  >
                    เคสนี้ล้าสมัยแล้ว
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {playbooks.length > 0 && (
        <div>
          {/* Kept visually separate from recalled cases: one is what happened
              here before, the other is general engine documentation. */}
          <p className="text-xs text-gray-400 mb-1">📘 ความรู้อ้างอิงของ engine</p>
          <div className="space-y-2">
            {playbooks.map((h) => (
              <div key={h.point_id} className="bg-gray-900 rounded-lg p-3 border border-gray-800">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] px-1.5 py-0.5 rounded border text-blue-300 bg-blue-950/30 border-blue-900">
                    playbook
                  </span>
                  <span className="text-[11px] text-white font-medium">{h.title}</span>
                  <span className="text-[11px] text-gray-500">ตรงกัน {Math.round(h.similarity * 100)}%</span>
                </div>
                {h.verify_steps?.length > 0 && (
                  <p className="text-[11px] text-amber-300/90 mt-1.5">
                    ตรวจก่อนลงมือ: {h.verify_steps[0]}
                  </p>
                )}
                {h.fix_steps?.length > 0 && (
                  <p className="text-[11px] text-gray-400 mt-1">วิธีแก้: {h.fix_steps[0]}</p>
                )}
                <div className="flex gap-3 mt-2">
                  {h.docs_url && (
                    <a href={h.docs_url} target="_blank" rel="noreferrer"
                       className="text-[10px] text-blue-400 hover:text-blue-300">เอกสารอ้างอิง</a>
                  )}
                  <button
                    onClick={() => deprecate(h)}
                    className="text-[10px] text-gray-500 hover:text-red-400 transition-colors"
                  >
                    ไม่เกี่ยวกับระบบเรา
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Feedback ───────────────────────────────────────────────────────────────

type Verdict = "correct" | "partial" | "wrong";

function FeedbackBox({ resultId, host }: { resultId: number | null; host: string }) {
  const [existing, setExisting] = useState<any | null>(null);
  const [pending, setPending] = useState<Verdict | null>(null);
  const [cause, setCause] = useState("");
  const [fix, setFix] = useState("");
  const [by, setBy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!resultId) return;
    fetch(`/api/results/${resultId}/hosts/${encodeURIComponent(host)}/feedback`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d?.verified) setExisting(d); })
      .catch(() => {});
  }, [resultId, host]);

  if (!resultId) return null;

  if (existing) {
    return (
      <div className="mt-4 border-t border-gray-700 pt-3">
        <p className="text-[11px] text-green-300">
          ✓ ยืนยันแล้ว{existing.resolved_by ? ` โดย ${existing.resolved_by}` : ""}
          {existing.verified_at ? ` เมื่อ ${new Date(existing.verified_at).toLocaleString()}` : ""}
          {existing.verdict ? ` · ${existing.verdict}` : ""}
        </p>
        {existing.actual_fix && (
          <p className="text-[11px] text-gray-400 mt-1">วิธีที่แก้ได้จริง: {existing.actual_fix}</p>
        )}
      </div>
    );
  }

  const submit = async (verdict: Verdict) => {
    // "wrong" without the correction is refused by the API on purpose — saying
    // only that we were wrong teaches the system nothing.
    if (verdict === "wrong" && !cause.trim() && !fix.trim()) {
      setError("กรุณาระบุว่าสาเหตุ/วิธีแก้ที่ถูกต้องคืออะไร");
      return;
    }
    setBusy(true);
    setError(null);
    const res = await fetch(
      `/api/results/${resultId}/hosts/${encodeURIComponent(host)}/feedback`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          verdict,
          actual_root_cause: cause.trim() || null,
          actual_fix: fix.trim() || null,
          resolved_by: by.trim() || null,
        }),
      },
    ).catch(() => null);
    setBusy(false);

    if (!res || !res.ok) {
      const detail = res ? await res.json().catch(() => null) : null;
      setError(detail?.detail?.error || "ส่งไม่สำเร็จ");
      return;
    }
    setExisting({
      verified: true, verdict, resolved_by: by.trim() || null,
      verified_at: new Date().toISOString(), actual_fix: fix.trim() || null,
    });
  };

  return (
    <div className="mt-4 border-t border-gray-700 pt-3">
      <p className="text-xs text-gray-400 mb-2">ผลนี้ถูกต้องไหม?</p>
      <div className="flex gap-2 flex-wrap">
        <button
          disabled={busy}
          onClick={() => submit("correct")}
          className="text-[11px] px-2.5 py-1 rounded border border-green-800 bg-green-950/40 text-green-300 hover:bg-green-900/50 disabled:opacity-50"
        >✓ ถูก</button>
        <button
          disabled={busy}
          onClick={() => setPending(pending === "partial" ? null : "partial")}
          className={`text-[11px] px-2.5 py-1 rounded border border-yellow-800 bg-yellow-950/40 text-yellow-300 hover:bg-yellow-900/50 disabled:opacity-50 ${pending === "partial" ? "ring-1 ring-yellow-600" : ""}`}
        >~ ถูกบางส่วน</button>
        <button
          disabled={busy}
          onClick={() => setPending(pending === "wrong" ? null : "wrong")}
          className={`text-[11px] px-2.5 py-1 rounded border border-red-800 bg-red-950/40 text-red-300 hover:bg-red-900/50 disabled:opacity-50 ${pending === "wrong" ? "ring-1 ring-red-600" : ""}`}
        >✗ ผิด</button>
      </div>

      {pending && (
        <div className="mt-3 space-y-2">
          <p className="text-[11px] text-gray-500">
            {pending === "wrong"
              ? "ต้องระบุอย่างน้อยหนึ่งช่อง — เคสที่ระบบตอบผิดแล้วคนแก้ให้ คือข้อมูลที่มีค่าที่สุด"
              : "เพิ่มรายละเอียดที่ขาดไป (ไม่บังคับ)"}
          </p>
          <textarea
            value={cause} onChange={(e) => setCause(e.target.value)}
            placeholder="สาเหตุที่แท้จริง"
            className="w-full text-[11px] bg-gray-900 border border-gray-700 rounded p-2 text-gray-200"
            rows={2}
          />
          <textarea
            value={fix} onChange={(e) => setFix(e.target.value)}
            placeholder="วิธีที่แก้ได้จริง"
            className="w-full text-[11px] bg-gray-900 border border-gray-700 rounded p-2 text-gray-200"
            rows={2}
          />
          <input
            value={by} onChange={(e) => setBy(e.target.value)}
            placeholder="ชื่อผู้ยืนยัน (ไม่บังคับ)"
            className="w-full text-[11px] bg-gray-900 border border-gray-700 rounded p-2 text-gray-200"
          />
          <button
            disabled={busy}
            onClick={() => submit(pending)}
            className="text-[11px] px-3 py-1 rounded bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50"
          >{busy ? "กำลังส่ง…" : "ส่ง"}</button>
        </div>
      )}

      {error && <p className="text-[11px] text-red-400 mt-2">{error}</p>}
    </div>
  );
}
