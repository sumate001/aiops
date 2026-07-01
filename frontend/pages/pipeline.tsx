import { useEffect, useState } from "react";
import Link from "next/link";

// ─── types (subset of AnalyzeResponse) ───────────────────────────────────────
type AnomalyScore = {
  metric: string; score: number; severity: string;
  current_value?: number; baseline_mean?: number;
};
type MiroFishFrame = {
  frame: string; lens: string; relevance: number;
  signal_hits: number; top_keywords: string[]; insight?: string;
};
type Synthesis = {
  root_cause_chain: string[]; confidence: number;
  fix_steps: string[]; method: string; top_frame?: string;
};
type Trend = {
  direction: string; slope_per_hour: number;
  windows_analyzed: number; z_score?: number;
  baseline_comparison?: string; anomaly_types: string[];
};
type Prediction = {
  risk_level: string; self_confidence: number;
  estimated_incident_in?: string; contributing_signals: string[];
  recommendation: string; matched_fingerprint?: string;
};
type Enrichment = { query: string; answer: string; sources: { title: string; url: string }[] };
type TopError = { msg: string; count: number };
type HostAnalysis = {
  host: string; entry_count: number; error_count: number; warn_count: number;
  health_score: number; status: string;
  anomalies: AnomalyScore[]; top_errors: TopError[];
  mirofish: MiroFishFrame[]; synthesis?: Synthesis;
  trend?: Trend; prediction?: Prediction; enrichment?: Enrichment;
};
type Result = {
  tenant_id: string; analyzed_at: string; health_score: number; status: string;
  window: { from: string; to: string };
  hosts: HostAnalysis[];
  sources: { ollama_used: boolean; ollama_model: string; aiops_ml_used: boolean };
};

// ─── helpers ─────────────────────────────────────────────────────────────────
const pct = (n: number) => `${(n * 100).toFixed(0)}%`;
const fix2 = (n: number) => n.toFixed(2);

const riskColor = (r: string) => ({
  low: "text-green-400 bg-green-900/40 border-green-800",
  medium: "text-yellow-400 bg-yellow-900/40 border-yellow-800",
  high: "text-orange-400 bg-orange-900/40 border-orange-800",
  critical: "text-red-400 bg-red-900/40 border-red-800",
}[r?.toLowerCase()] ?? "text-gray-400 bg-gray-800 border-gray-700");

const sevColor = (s: string) => ({
  low: "text-green-400", medium: "text-yellow-400",
  high: "text-orange-400", critical: "text-red-400",
}[s?.toLowerCase()] ?? "text-gray-400");

const dirIcon = (d: string) => ({
  rising: "↑", falling: "↓", stable: "→", unknown: "?",
}[d] ?? "?");

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span className={`inline-block border rounded px-1.5 py-0.5 text-[10px] font-semibold ${color}`}>
      {label}
    </span>
  );
}

function Section({ title, color = "border-gray-700", children }: {
  title: string; color?: string; children: React.ReactNode;
}) {
  return (
    <div className={`border ${color} rounded-xl bg-gray-900 overflow-hidden`}>
      <div className="px-4 py-2 border-b border-gray-800 bg-gray-950/40">
        <p className="text-[11px] font-bold text-gray-400 uppercase tracking-widest">{title}</p>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function Chip({ label, type }: { label: string; type: "in" | "out" }) {
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${
      type === "in"
        ? "border-blue-800 bg-blue-950/40 text-blue-300"
        : "border-emerald-800 bg-emerald-950/40 text-emerald-300"
    }`}>
      {type === "in" ? "↓" : "↑"} {label}
    </span>
  );
}

// ─── main ─────────────────────────────────────────────────────────────────────
export default function Pipeline() {
  const [result, setResult] = useState<Result | null>(null);
  const [resultId, setResultId] = useState<number | null>(null);
  const [hostIdx, setHostIdx] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/results?limit=1")
      .then((r) => r.json())
      .then((d) => {
        const row = d.results?.[0];
        if (!row) { setLoading(false); return; }
        setResultId(row.id);
        return fetch(`/api/results/${row.id}`).then((r) => r.json());
      })
      .then((d) => { if (d) setResult(d); })
      .finally(() => setLoading(false));
  }, []);

  const host: HostAnalysis | undefined = result?.hosts?.[hostIdx];

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Pipeline Data Flow</h1>
          <p className="text-gray-400 text-sm mt-1">
            {result
              ? `Result #${resultId} · ${result.tenant_id} · ${new Date(result.analyzed_at).toLocaleString("th-TH")}`
              : "ยังไม่มีผล — รัน simulation ก่อน"}
          </p>
        </div>
        <nav className="flex gap-4 text-sm">
          <Link href="/" className="text-gray-400 hover:text-white">Dashboard</Link>
          <Link href="/pipeline" className="text-blue-400 font-medium">Pipeline</Link>
          <Link href="/results" className="text-gray-400 hover:text-white">Results</Link>
          <Link href="/settings" className="text-gray-400 hover:text-white">Settings</Link>
        </nav>
      </div>

      {loading && <p className="text-gray-500">Loading...</p>}
      {!loading && !result && (
        <div className="bg-gray-900 rounded-xl p-8 text-center text-gray-500">
          ยังไม่มี analysis result — ส่ง log จาก LogSim → AIOps แล้วกลับมาดู
        </div>
      )}

      {result && (
        <>
          {/* Host selector */}
          {result.hosts.length > 0 && (
            <div className="flex gap-2 mb-6 flex-wrap">
              {result.hosts.map((h, i) => (
                <button
                  key={h.host}
                  onClick={() => setHostIdx(i)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                    i === hostIdx
                      ? "bg-blue-700 border-blue-600 text-white"
                      : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500"
                  }`}
                >
                  {h.host}
                  <span className={`ml-1.5 ${
                    h.status === "critical" ? "text-red-400" :
                    h.status === "warning"  ? "text-yellow-400" : "text-green-400"
                  }`}>● {Math.round(h.health_score)}</span>
                </button>
              ))}
            </div>
          )}

          {/* No hosts */}
          {result.hosts.length === 0 && (
            <div className="bg-gray-900 rounded-xl p-6 mb-6 text-gray-500 text-sm">
              Batch analyzed — 0 hosts (ไม่มี log entries ในช่วงเวลานี้)
            </div>
          )}

          {host && (
            <div className="space-y-3">

              {/* ── INPUT ─────────────────────────────────────────────────── */}
              <Section title="INPUT — Log Entries" color="border-gray-600">
                <div className="grid grid-cols-4 gap-4 mb-3">
                  {[
                    { label: "Total Entries", value: host.entry_count, color: "text-white" },
                    { label: "Errors", value: host.error_count, color: "text-red-400" },
                    { label: "Warnings", value: host.warn_count, color: "text-yellow-400" },
                    { label: "Normal", value: host.entry_count - host.error_count - host.warn_count, color: "text-green-400" },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-gray-800 rounded-lg p-3 text-center">
                      <p className={`text-2xl font-bold ${color}`}>{value}</p>
                      <p className="text-[10px] text-gray-500 mt-0.5">{label}</p>
                    </div>
                  ))}
                </div>
                {host.top_errors.length > 0 && (
                  <div>
                    <p className="text-[10px] text-gray-600 uppercase mb-1">Top Error Messages</p>
                    <div className="space-y-1">
                      {host.top_errors.slice(0, 4).map((e, i) => (
                        <div key={i} className="flex items-center gap-2 bg-gray-800 rounded px-3 py-1.5">
                          <span className="text-red-400 font-bold text-xs w-6">{e.count}×</span>
                          <span className="text-xs text-gray-400 font-mono truncate">{e.msg}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </Section>

              {/* ── A1 Rule ───────────────────────────────────────────────── */}
              <div className="flex gap-1 items-center text-gray-700 pl-4"><span>↓</span></div>
              <Section title="A1 — Rule-Based Anomaly Detection" color="border-blue-800">
                <div className="flex gap-3 mb-3 flex-wrap">
                  <Chip label="entry_count / error_count / warn_count" type="in" />
                  <Chip label="severity_text patterns" type="in" />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-[10px] text-gray-600 uppercase mb-2">Output — Rule Signals</p>
                    <div className="bg-gray-800 rounded-lg p-3">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs text-gray-400">Health Score</span>
                        <span className={`text-lg font-bold ${
                          host.health_score >= 80 ? "text-green-400" :
                          host.health_score >= 50 ? "text-yellow-400" : "text-red-400"
                        }`}>{fix2(host.health_score)}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-400">Status</span>
                        <Badge
                          label={host.status.toUpperCase()}
                          color={host.status === "critical" ? "text-red-400 border-red-800 bg-red-950/40" :
                                 host.status === "warning"  ? "text-yellow-400 border-yellow-800 bg-yellow-950/40" :
                                 "text-green-400 border-green-800 bg-green-950/40"}
                        />
                      </div>
                    </div>
                  </div>
                  <div>
                    <p className="text-[10px] text-gray-600 uppercase mb-2">Output — Anomaly Flags</p>
                    {host.anomalies.filter((a) => a.metric !== "if_score").length === 0 ? (
                      <p className="text-xs text-gray-600 bg-gray-800 rounded-lg p-3">No rule anomalies detected</p>
                    ) : (
                      <div className="space-y-1">
                        {host.anomalies.filter((a) => a.metric !== "if_score").map((a) => (
                          <div key={a.metric} className="bg-gray-800 rounded p-2 flex items-center justify-between">
                            <div>
                              <span className="text-xs font-mono text-gray-300">{a.metric}</span>
                              {a.current_value != null && (
                                <span className="text-[10px] text-gray-500 ml-2">
                                  now: {fix2(a.current_value)} / baseline: {fix2(a.baseline_mean ?? 0)}
                                </span>
                              )}
                            </div>
                            <Badge label={a.severity} color={sevColor(a.severity) + " border-gray-700"} />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </Section>

              {/* ── A1 IF ─────────────────────────────────────────────────── */}
              <div className="flex gap-1 items-center text-gray-700 pl-4"><span>↓</span></div>
              <Section title="A1 — Isolation Forest (ML)" color="border-cyan-800">
                <div className="flex gap-3 mb-3 flex-wrap">
                  <Chip label="window_stats (9 features)" type="in" />
                  <Chip label="error_rate · warn_rate · log_rate" type="in" />
                  <Chip label="unique_services · severity_score · time_span" type="in" />
                </div>
                {(() => {
                  const ifAnomaly = host.anomalies.find((a) => a.metric === "if_score");
                  return (
                    <div className="grid grid-cols-3 gap-3">
                      <div className="bg-gray-800 rounded-lg p-3">
                        <p className="text-[10px] text-gray-600 uppercase mb-1">IF Anomaly Score</p>
                        <p className={`text-2xl font-bold ${
                          ifAnomaly ? (ifAnomaly.score > 0.6 ? "text-red-400" : ifAnomaly.score > 0.3 ? "text-yellow-400" : "text-green-400")
                          : "text-gray-600"
                        }`}>{ifAnomaly ? fix2(ifAnomaly.score) : "n/a"}</p>
                        <p className="text-[10px] text-gray-500 mt-0.5">0 = normal · 1 = anomaly</p>
                      </div>
                      <div className="bg-gray-800 rounded-lg p-3">
                        <p className="text-[10px] text-gray-600 uppercase mb-1">Severity</p>
                        <p className={`text-lg font-bold capitalize ${sevColor(ifAnomaly?.severity ?? "")}`}>
                          {ifAnomaly?.severity ?? "—"}
                        </p>
                      </div>
                      <div className="bg-gray-800 rounded-lg p-3">
                        <p className="text-[10px] text-gray-600 uppercase mb-1">is_anomaly</p>
                        <p className={`text-lg font-bold ${ifAnomaly && ifAnomaly.score > 0.5 ? "text-red-400" : "text-green-400"}`}>
                          {ifAnomaly ? (ifAnomaly.score > 0.5 ? "TRUE" : "FALSE") : "—"}
                        </p>
                      </div>
                    </div>
                  );
                })()}
              </Section>

              {/* ── A2 Perplexica ────────────────────────────────────────── */}
              <div className="flex gap-1 items-center text-gray-700 pl-4"><span>↓</span></div>
              <Section title="A2 — Perplexica External Knowledge" color="border-violet-800">
                <div className="flex gap-3 mb-3 flex-wrap">
                  <Chip label="top_errors[] (query)" type="in" />
                  <Chip label="anomaly patterns" type="in" />
                </div>
                {!host.enrichment ? (
                  <p className="text-xs text-gray-600 bg-gray-800 rounded-lg p-3">
                    Perplexica disabled — เปิดใช้ใน Settings เพื่อรับ external context
                  </p>
                ) : (
                  <div className="space-y-3">
                    <div className="bg-gray-800 rounded-lg p-3">
                      <p className="text-[10px] text-gray-600 uppercase mb-1">Query ที่ส่งไป</p>
                      <p className="text-xs text-gray-300 font-mono">{host.enrichment.query}</p>
                    </div>
                    <div className="bg-gray-800 rounded-lg p-3">
                      <p className="text-[10px] text-gray-600 uppercase mb-1">Answer (External Context)</p>
                      <p className="text-xs text-gray-300 leading-relaxed">{host.enrichment.answer}</p>
                    </div>
                    {host.enrichment.sources.length > 0 && (
                      <div>
                        <p className="text-[10px] text-gray-600 uppercase mb-1">Sources</p>
                        <div className="flex gap-2 flex-wrap">
                          {host.enrichment.sources.map((s, i) => (
                            <span key={i} className="text-[10px] text-blue-400 bg-blue-950/30 border border-blue-900 rounded px-2 py-0.5">{s.title}</span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </Section>

              {/* ── A3 MiroFish ──────────────────────────────────────────── */}
              <div className="flex gap-1 items-center text-gray-700 pl-4"><span>↓</span></div>
              <Section title="A3 — MiroFish 5-Frame Analysis" color="border-amber-800">
                <div className="flex gap-3 mb-3 flex-wrap">
                  <Chip label="log entries" type="in" />
                  <Chip label="rule_hits[]" type="in" />
                  <Chip label="anomaly_score" type="in" />
                </div>
                {host.mirofish.length === 0 ? (
                  <p className="text-xs text-gray-600">No MiroFish frames</p>
                ) : (
                  <div className="grid grid-cols-5 gap-2">
                    {host.mirofish.map((f) => (
                      <div key={f.frame} className={`bg-gray-800 rounded-lg p-3 border-t-2 ${
                        f.relevance > 0.5 ? "border-amber-600" : "border-gray-700"
                      }`}>
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-[10px] font-bold text-white">{f.frame}</span>
                          <span className={`text-xs font-bold ${f.relevance > 0.5 ? "text-amber-400" : "text-gray-500"}`}>
                            {pct(f.relevance)}
                          </span>
                        </div>
                        <p className="text-[10px] text-gray-500 mb-1">lens: {f.lens}</p>
                        <p className="text-[10px] text-gray-500 mb-2">hits: {f.signal_hits}</p>
                        <div className="flex flex-wrap gap-0.5">
                          {f.top_keywords.slice(0, 4).map((kw) => (
                            <span key={kw} className="text-[9px] bg-gray-700 text-gray-300 rounded px-1">{kw}</span>
                          ))}
                        </div>
                        {f.insight && (
                          <p className="text-[9px] text-amber-300/70 mt-2 leading-relaxed">{f.insight}</p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </Section>

              {/* ── AA Synthesizer ────────────────────────────────────────── */}
              <div className="flex gap-1 items-center text-gray-700 pl-4"><span>↓</span></div>
              <Section title="AA — Synthesizer (LLM-as-Judge)" color="border-green-800">
                <div className="flex gap-3 mb-3 flex-wrap">
                  <Chip label="health_score (A1)" type="in" />
                  <Chip label="anomalies[] (A1)" type="in" />
                  <Chip label="enrichment (A2)" type="in" />
                  <Chip label="mirofish frames (A3)" type="in" />
                </div>
                {!host.synthesis ? (
                  <p className="text-xs text-gray-600">No synthesis output</p>
                ) : (
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-3">
                      <div className="bg-gray-800 rounded-lg p-3">
                        <p className="text-[10px] text-gray-600 uppercase mb-2">Root Cause Chain</p>
                        <ol className="space-y-1 list-decimal list-inside">
                          {host.synthesis.root_cause_chain.map((c, i) => (
                            <li key={i} className="text-xs text-gray-300 leading-relaxed">{c}</li>
                          ))}
                        </ol>
                      </div>
                      <div className="flex gap-3">
                        <div className="bg-gray-800 rounded-lg p-3 flex-1">
                          <p className="text-[10px] text-gray-600 uppercase mb-1">AA Overall Confidence</p>
                          <p className={`text-2xl font-bold ${
                            host.synthesis.confidence > 0.7 ? "text-green-400" :
                            host.synthesis.confidence > 0.4 ? "text-yellow-400" : "text-red-400"
                          }`}>{pct(host.synthesis.confidence)}</p>
                        </div>
                        <div className="bg-gray-800 rounded-lg p-3 flex-1">
                          <p className="text-[10px] text-gray-600 uppercase mb-1">Top Frame</p>
                          <p className="text-sm font-bold text-amber-400">{host.synthesis.top_frame ?? "—"}</p>
                          <p className="text-[10px] text-gray-600">{host.synthesis.method}</p>
                        </div>
                      </div>
                    </div>
                    <div className="bg-gray-800 rounded-lg p-3">
                      <p className="text-[10px] text-gray-600 uppercase mb-2">Fix Steps</p>
                      <ol className="space-y-1.5 list-decimal list-inside">
                        {host.synthesis.fix_steps.map((s, i) => (
                          <li key={i} className="text-xs text-emerald-300 leading-relaxed">{s}</li>
                        ))}
                      </ol>
                    </div>
                  </div>
                )}
              </Section>

              {/* ── Trend + Prediction ───────────────────────────────────── */}
              <div className="flex gap-1 items-center text-gray-700 pl-4"><span>↓</span></div>
              <Section title="Trend + Prediction Engine" color="border-purple-800">
                <div className="flex gap-3 mb-3 flex-wrap">
                  <Chip label="window_stats history (SQLite)" type="in" />
                  <Chip label="health_score series" type="in" />
                  <Chip label="anomaly_score series" type="in" />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  {/* Trend */}
                  <div className="space-y-2">
                    <p className="text-[10px] text-gray-600 uppercase">Trend Output</p>
                    {!host.trend ? (
                      <p className="text-xs text-gray-600 bg-gray-800 rounded-lg p-3">
                        ต้องการ ≥30 windows — ส่ง log ต่อเนื่องเพื่อสร้าง baseline
                      </p>
                    ) : (
                      <div className="bg-gray-800 rounded-lg p-3 space-y-2">
                        <div className="flex items-center gap-3">
                          <span className={`text-3xl font-bold ${
                            host.trend.direction === "rising" ? "text-red-400" :
                            host.trend.direction === "falling" ? "text-green-400" : "text-gray-400"
                          }`}>{dirIcon(host.trend.direction)}</span>
                          <div>
                            <p className="text-sm font-bold text-white capitalize">{host.trend.direction}</p>
                            <p className="text-[10px] text-gray-500">{host.trend.windows_analyzed} windows analyzed</p>
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div>
                            <p className="text-gray-600 text-[10px]">slope / hr</p>
                            <p className="text-white font-mono">{fix2(host.trend.slope_per_hour)}</p>
                          </div>
                          {host.trend.z_score != null && (
                            <div>
                              <p className="text-gray-600 text-[10px]">z-score</p>
                              <p className={`font-mono ${Math.abs(host.trend.z_score) > 2 ? "text-red-400" : "text-white"}`}>
                                {fix2(host.trend.z_score)}
                              </p>
                            </div>
                          )}
                          {host.trend.baseline_comparison && (
                            <div className="col-span-2">
                              <p className="text-gray-600 text-[10px]">vs baseline</p>
                              <p className="text-orange-400">{host.trend.baseline_comparison}</p>
                            </div>
                          )}
                        </div>
                        {host.trend.anomaly_types.length > 0 && (
                          <div className="flex gap-1 flex-wrap">
                            {host.trend.anomaly_types.map((t) => (
                              <Badge key={t} label={t} color="text-purple-300 border-purple-800 bg-purple-950/40" />
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Prediction */}
                  <div className="space-y-2">
                    <p className="text-[10px] text-gray-600 uppercase">Prediction Output</p>
                    {!host.prediction ? (
                      <p className="text-xs text-gray-600 bg-gray-800 rounded-lg p-3">
                        ยังไม่มีข้อมูล trend เพียงพอสำหรับ prediction
                      </p>
                    ) : (
                      <div className="space-y-2">
                        <div className={`rounded-lg p-3 border ${riskColor(host.prediction.risk_level)}`}>
                          <div className="flex items-center justify-between mb-1">
                            <p className="text-xs font-bold uppercase tracking-wide">{host.prediction.risk_level} risk</p>
                            <span className="text-xs">{pct(host.prediction.self_confidence)} predictor self-confidence</span>
                          </div>
                          {host.prediction.estimated_incident_in && (
                            <p className="text-sm font-bold">⏱ Incident in ~{host.prediction.estimated_incident_in}</p>
                          )}
                          {host.prediction.matched_fingerprint && (
                            <p className="text-[10px] mt-1">Pattern: {host.prediction.matched_fingerprint}</p>
                          )}
                        </div>
                        {host.prediction.contributing_signals.length > 0 && (
                          <div className="bg-gray-800 rounded-lg p-3">
                            <p className="text-[10px] text-gray-600 uppercase mb-1">Contributing Signals</p>
                            <ul className="space-y-0.5">
                              {host.prediction.contributing_signals.map((s, i) => (
                                <li key={i} className="text-xs text-gray-300">• {s}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {host.prediction.recommendation && (
                          <div className="bg-gray-800 rounded-lg p-3">
                            <p className="text-[10px] text-gray-600 uppercase mb-1">Recommendation</p>
                            <p className="text-xs text-emerald-300">{host.prediction.recommendation}</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </Section>

            </div>
          )}
        </>
      )}
    </div>
  );
}
