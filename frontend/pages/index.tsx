import { useEffect, useState } from "react";
import Link from "next/link";

// ─── types ───────────────────────────────────────────────────────────────────
type AgentStatus = { status: string; note?: string; enabled?: boolean };
type StatusData  = { agents: Record<string, AgentStatus>; integrations: Record<string, any> };
type ResultRow   = { id: number; tenant_id: string; analyzed_at: string; health_score: number; status: string; host_count: number; critical_count: number; summary: string };

type AnomalyScore  = { metric: string; score: number; severity: string; current_value?: number; baseline_mean?: number };
type MiroFishFrame = { frame: string; lens: string; relevance: number; signal_hits: number; top_keywords: string[]; insight?: string };
type Synthesis     = { root_cause_chain: string[]; confidence: number; fix_steps: string[]; method: string; top_frame?: string };
type HostAnalysis  = { host: string; entry_count: number; error_count: number; warn_count: number; health_score: number; status: string; anomalies: AnomalyScore[]; mirofish: MiroFishFrame[]; synthesis?: Synthesis; enrichment?: { query: string; answer: string } };
type FullResult    = { tenant_id: string; analyzed_at: string; health_score: number; status: string; hosts: HostAnalysis[]; sources: { ollama_used: boolean; ollama_model: string } };

// ─── helpers ─────────────────────────────────────────────────────────────────
const healthColor = (s: number) => s >= 80 ? "text-green-400" : s >= 50 ? "text-yellow-400" : "text-red-400";
const pct = (n: number) => `${(n * 100).toFixed(0)}%`;
const f2  = (n: number) => n.toFixed(2);

const STATUS_DOT: Record<string, string> = { up: "bg-green-500", down: "bg-red-500", disabled: "bg-gray-600" };
const STATUS_BADGE: Record<string, string> = {
  up:       "bg-green-900/60 text-green-400",
  down:     "bg-red-900/60 text-red-400",
  disabled: "bg-gray-800 text-gray-500",
};

// ─── sub-components ──────────────────────────────────────────────────────────
function Val({ label, value, color = "text-white" }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[10px] text-gray-500 shrink-0">{label}</span>
      <span className={`text-xs font-mono font-semibold ${color} truncate text-right`}>{value}</span>
    </div>
  );
}

function AgentBox({ label, desc, statusKey, borderColor, agents, children }: {
  label: string; desc: string; statusKey: string; borderColor: string;
  agents: Record<string, AgentStatus> | undefined; children: React.ReactNode;
}) {
  const st = agents?.[statusKey];
  return (
    <div className={`bg-gray-900 border ${borderColor} rounded-xl overflow-hidden flex flex-col`}>
      <div className="flex items-center gap-2 px-4 py-2.5 bg-gray-950/40 border-b border-gray-800">
        <span className={`w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[st?.status ?? "down"] ?? "bg-gray-600"}`} />
        <span className="text-xs font-bold text-white">{label}</span>
        <span className="text-[10px] text-gray-500">{desc}</span>
        <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded font-semibold ${STATUS_BADGE[st?.status ?? "down"] ?? STATUS_BADGE.down}`}>
          {st?.status ?? "—"}
        </span>
      </div>
      <div className="p-4 flex-1">{children}</div>
    </div>
  );
}

function NoData({ msg = "ยังไม่มีข้อมูล" }: { msg?: string }) {
  return <p className="text-[11px] text-gray-600 italic">{msg}</p>;
}

// ─── main ─────────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const [pipeStatus, setPipeStatus] = useState<StatusData | null>(null);
  const [results,    setResults]    = useState<ResultRow[]>([]);
  const [latest,     setLatest]     = useState<FullResult | null>(null);
  const [hostIdx,    setHostIdx]    = useState(0);
  const [loading,    setLoading]    = useState(true);

  const fetchAll = async () => {
    const [s, r] = await Promise.all([
      fetch("/api/status").then((x) => x.json()).catch(() => null),
      fetch("/api/results?limit=10").then((x) => x.json()).catch(() => ({ results: [] })),
    ]);
    setPipeStatus(s);
    const rows: ResultRow[] = r.results || [];
    setResults(rows);

    // fetch full payload of latest result
    if (rows[0]) {
      const full = await fetch(`/api/results/${rows[0].id}`).then((x) => x.json()).catch(() => null);
      setLatest(full);
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchAll();
    const t = setInterval(fetchAll, 15000);
    return () => clearInterval(t);
  }, []);

  const host: HostAnalysis | undefined = latest?.hosts?.[hostIdx];
  const ifAnomaly = host?.anomalies?.find((a) => a.metric === "if_score");
  const ruleAnomalies = host?.anomalies?.filter((a) => a.metric !== "if_score") ?? [];
  const topFrame = host?.mirofish?.reduce((a, b) => b.relevance > a.relevance ? b : a, { relevance: -1 } as MiroFishFrame);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">GodEyes Log Analyzer</h1>
          <p className="text-gray-400 text-sm mt-1">MoA Pipeline Monitor</p>
        </div>
        <nav className="flex gap-4 text-sm">
          <Link href="/" className="text-blue-400 font-medium">Dashboard</Link>
          <Link href="/pipeline" className="text-gray-400 hover:text-white">Pipeline Detail</Link>
          <Link href="/results" className="text-gray-400 hover:text-white">Results</Link>
          <Link href="/settings" className="text-gray-400 hover:text-white">Settings</Link>
        </nav>
      </div>

      {/* ── MoA Pipeline ─────────────────────────────────────────────────────── */}
      <div className="bg-gray-900 rounded-xl p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">MoA Pipeline</h2>
          {latest && (
            <div className="flex items-center gap-3">
              {latest.hosts.length > 1 && latest.hosts.map((h, i) => (
                <button key={h.host} onClick={() => setHostIdx(i)}
                  className={`text-xs px-2.5 py-1 rounded-lg border transition-colors ${
                    i === hostIdx ? "bg-blue-700 border-blue-600 text-white" : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500"
                  }`}>
                  {h.host}
                </button>
              ))}
              <span className="text-[11px] text-gray-600">
                {latest.tenant_id} · {new Date(latest.analyzed_at).toLocaleTimeString("th-TH")}
              </span>
            </div>
          )}
        </div>

        {/* INPUT row */}
        <div className="flex items-center gap-3 mb-4">
          <div className="bg-gray-800 border border-gray-700 rounded-xl px-4 py-3 min-w-[160px]">
            <p className="text-[10px] font-bold text-gray-500 uppercase mb-2">Input</p>
            {host ? (
              <div className="space-y-1">
                <Val label="entries" value={host.entry_count} />
                <Val label="errors"  value={host.error_count}  color="text-red-400" />
                <Val label="warns"   value={host.warn_count}   color="text-yellow-400" />
                <Val label="host"    value={host.host} />
              </div>
            ) : (
              <NoData msg="รอ log entries..." />
            )}
          </div>
          {/* fan-out arrow */}
          <div className="flex flex-col gap-1 text-gray-700 text-lg">
            <span>┐</span><span>│</span><span>├</span><span>│</span><span>┘</span>
          </div>
          <span className="text-gray-600 text-xl shrink-0">→</span>
        </div>

        {/* PARALLEL AGENTS row */}
        <div className="grid grid-cols-3 gap-4 mb-4">

          {/* A1 Block */}
          <div className="space-y-3">
            <AgentBox label="A1 Rule" desc="Rule-based" statusKey="A1_rule" borderColor="border-blue-800" agents={pipeStatus?.agents}>
              <p className="text-[10px] text-gray-600 uppercase mb-2">Output</p>
              {host ? (
                <div className="space-y-1">
                  <Val label="health_score" value={f2(host.health_score)}
                    color={host.health_score >= 80 ? "text-green-400" : host.health_score >= 50 ? "text-yellow-400" : "text-red-400"} />
                  <Val label="status"       value={host.status.toUpperCase()}
                    color={host.status === "critical" ? "text-red-400" : host.status === "warning" ? "text-yellow-400" : "text-green-400"} />
                  <Val label="error_rate"   value={`${host.entry_count > 0 ? ((host.error_count / host.entry_count) * 100).toFixed(1) : 0}%`} />
                  {ruleAnomalies.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-gray-800">
                      {ruleAnomalies.slice(0, 2).map((a) => (
                        <div key={a.metric} className="text-[10px] text-orange-300 truncate">⚠ {a.metric}: {f2(a.score)}</div>
                      ))}
                    </div>
                  )}
                </div>
              ) : <NoData />}
            </AgentBox>

            <AgentBox label="A1 IF" desc="Isolation Forest" statusKey="A1_isolation_forest" borderColor="border-cyan-800" agents={pipeStatus?.agents}>
              <p className="text-[10px] text-gray-600 uppercase mb-2">Output</p>
              {ifAnomaly ? (
                <div className="space-y-1">
                  <Val label="if_score"   value={f2(ifAnomaly.score)}
                    color={ifAnomaly.score > 0.6 ? "text-red-400" : ifAnomaly.score > 0.3 ? "text-yellow-400" : "text-green-400"} />
                  <Val label="is_anomaly" value={ifAnomaly.score > 0.5 ? "TRUE" : "FALSE"}
                    color={ifAnomaly.score > 0.5 ? "text-red-400" : "text-green-400"} />
                  <Val label="severity"   value={ifAnomaly.severity}
                    color={ifAnomaly.severity === "critical" ? "text-red-400" : ifAnomaly.severity === "high" ? "text-orange-400" : "text-gray-300"} />
                  {ifAnomaly.baseline_mean != null && (
                    <Val label="vs baseline" value={`${f2(ifAnomaly.current_value ?? 0)} / ${f2(ifAnomaly.baseline_mean)}`} color="text-gray-400" />
                  )}
                </div>
              ) : host ? (
                <NoData msg="ยังไม่มีข้อมูล IF (ต้องการ ≥30 windows)" />
              ) : <NoData />}
            </AgentBox>
          </div>

          {/* A2 Perplexica */}
          <AgentBox label="A2 Perplexica" desc="External Knowledge" statusKey="A2_perplexica" borderColor="border-violet-800" agents={pipeStatus?.agents}>
            <p className="text-[10px] text-gray-600 uppercase mb-2">Output</p>
            {host?.enrichment ? (
              <div className="space-y-2">
                <div>
                  <p className="text-[9px] text-gray-600 mb-0.5">Query</p>
                  <p className="text-[10px] text-gray-400 font-mono line-clamp-2">{host.enrichment.query}</p>
                </div>
                <div>
                  <p className="text-[9px] text-gray-600 mb-0.5">Context</p>
                  <p className="text-[10px] text-violet-300 line-clamp-6 leading-relaxed">{host.enrichment.answer}</p>
                </div>
              </div>
            ) : (
              <NoData msg={pipeStatus?.agents?.A2_perplexica?.status === "disabled"
                ? "Disabled — เปิดใน Settings"
                : host ? "ไม่มี enrichment data" : "รอข้อมูล..."} />
            )}
          </AgentBox>

          {/* A3 MiroFish */}
          <AgentBox label="A3 MiroFish" desc="5-Frame Analysis" statusKey="A3_mirofish" borderColor="border-amber-800" agents={pipeStatus?.agents}>
            <p className="text-[10px] text-gray-600 uppercase mb-2">Output — frames (relevance)</p>
            {host?.mirofish && host.mirofish.length > 0 ? (
              <div className="space-y-1.5">
                {host.mirofish.map((f) => (
                  <div key={f.frame} className={`rounded px-2 py-1.5 ${f === topFrame ? "bg-amber-950/50 border border-amber-800/50" : "bg-gray-800"}`}>
                    <div className="flex items-center justify-between mb-0.5">
                      <span className={`text-[11px] font-semibold ${f === topFrame ? "text-amber-300" : "text-gray-300"}`}>{f.frame}</span>
                      <span className={`text-[11px] font-bold font-mono ${f.relevance > 0.5 ? "text-amber-400" : "text-gray-500"}`}>{pct(f.relevance)}</span>
                    </div>
                    <p className="text-[9px] text-gray-600 truncate">{f.top_keywords.slice(0, 4).join(" · ")}</p>
                    {f.insight && <p className="text-[9px] text-amber-300/60 line-clamp-1 mt-0.5">{f.insight}</p>}
                  </div>
                ))}
              </div>
            ) : (
              <NoData msg={host ? "ไม่มี MiroFish frames" : "รอข้อมูล..."} />
            )}
          </AgentBox>
        </div>

        {/* fan-in arrows */}
        <div className="flex justify-center mb-2 text-gray-700 text-sm tracking-widest">
          ↘&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;↓&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;↙
        </div>

        {/* AA Synthesizer */}
        <AgentBox label="AA Synthesizer" desc="LLM-as-Judge — รับ output จาก A1+A2+A3 ทั้งหมด" statusKey="AA_synthesizer" borderColor="border-green-800" agents={pipeStatus?.agents}>
          <div className="grid grid-cols-2 gap-4">
            {/* left: root cause */}
            <div>
              <p className="text-[10px] text-gray-600 uppercase mb-2">root_cause_chain</p>
              {host?.synthesis?.root_cause_chain?.length ? (
                <ol className="space-y-1 list-decimal list-inside">
                  {host.synthesis.root_cause_chain.map((c, i) => (
                    <li key={i} className="text-xs text-emerald-300 leading-relaxed">{c}</li>
                  ))}
                </ol>
              ) : <NoData msg={host ? "ไม่มี root cause chain" : "รอข้อมูล..."} />}
            </div>
            {/* right: metrics + fix */}
            <div className="space-y-3">
              {host?.synthesis && (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="bg-gray-800 rounded-lg p-2 text-center">
                      <p className="text-[9px] text-gray-600 uppercase">confidence</p>
                      <p className={`text-lg font-bold font-mono ${
                        host.synthesis.confidence > 0.7 ? "text-green-400" :
                        host.synthesis.confidence > 0.4 ? "text-yellow-400" : "text-red-400"}`}>
                        {pct(host.synthesis.confidence)}
                      </p>
                    </div>
                    <div className="bg-gray-800 rounded-lg p-2 text-center">
                      <p className="text-[9px] text-gray-600 uppercase">top frame</p>
                      <p className="text-sm font-bold text-amber-400">{host.synthesis.top_frame ?? "—"}</p>
                      <p className="text-[9px] text-gray-600">{host.synthesis.method}</p>
                    </div>
                  </div>
                  <div>
                    <p className="text-[10px] text-gray-600 uppercase mb-1">fix_steps</p>
                    <ol className="space-y-0.5 list-decimal list-inside">
                      {host.synthesis.fix_steps.map((s, i) => (
                        <li key={i} className="text-[11px] text-emerald-300 leading-relaxed">{s}</li>
                      ))}
                    </ol>
                  </div>
                </>
              )}
              {!host?.synthesis && <NoData msg={host ? "ไม่มี synthesis" : "รอข้อมูล..."} />}
            </div>
          </div>
        </AgentBox>

        {/* integrations footer */}
        {pipeStatus?.integrations && (
          <div className="flex gap-3 mt-4 flex-wrap border-t border-gray-800 pt-4">
            {Object.entries(pipeStatus.integrations).map(([key, val]) => (
              <div key={key} className="bg-gray-800 rounded-lg px-3 py-2 flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full shrink-0 ${
                  val?.status === "up" ? "bg-green-500" : val?.url || val?.model ? "bg-blue-500" : "bg-gray-600"}`} />
                <div>
                  <p className="text-xs font-medium text-white capitalize">{key.replace(/_/g, " ")}</p>
                  <p className="text-[10px] text-gray-500">{val?.model ?? val?.url ?? (val?.enabled ? "enabled" : "disabled")}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Recent Results ────────────────────────────────────────────────────── */}
      <div className="bg-gray-900 rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Recent Analyses</h2>
          <Link href="/results" className="text-xs text-blue-400 hover:underline">View all →</Link>
        </div>
        {loading ? (
          <p className="text-gray-500 text-sm">Loading...</p>
        ) : results.length === 0 ? (
          <p className="text-gray-500 text-sm">No analyses yet — send logs to /ingest or /analyze</p>
        ) : (
          <div className="space-y-2">
            {results.map((r) => (
              <Link key={r.id} href={`/results?id=${r.id}`} className="block">
                <div className="flex items-center gap-4 bg-gray-800 hover:bg-gray-700 rounded-lg px-4 py-3 transition-colors">
                  <span className={`text-lg font-bold w-12 ${healthColor(r.health_score)}`}>{Math.round(r.health_score)}</span>
                  <span className={`text-xs font-bold uppercase px-2 py-0.5 rounded ${
                    r.status === "critical" ? "bg-red-900 text-red-300" :
                    r.status === "warning"  ? "bg-yellow-900 text-yellow-300" : "bg-green-900 text-green-300"}`}>
                    {r.status}
                  </span>
                  <span className="text-xs text-gray-400 w-24">{r.tenant_id}</span>
                  <span className="text-xs text-gray-500 flex-1 truncate">{r.summary}</span>
                  <span className="text-xs text-gray-600">{r.critical_count}/{r.host_count} critical</span>
                  <span className="text-xs text-gray-600">{new Date(r.analyzed_at).toLocaleTimeString()}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
