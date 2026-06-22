import { useEffect, useState } from "react";
import Link from "next/link";

type AgentStatus = { status: string; note?: string; enabled?: boolean; error?: string };
type StatusData = {
  agents: Record<string, AgentStatus>;
  integrations: Record<string, any>;
};

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

const STATUS_COLOR: Record<string, string> = {
  up: "bg-green-500",
  down: "bg-red-500",
  disabled: "bg-gray-600",
};

const healthColor = (s: number) =>
  s >= 80 ? "text-green-400" : s >= 50 ? "text-yellow-400" : "text-red-400";

const AGENTS = [
  { key: "A1_rule", label: "A1 Rule", desc: "Rule-based anomaly detection" },
  { key: "A1_isolation_forest", label: "A1 IF", desc: "Isolation Forest (9 features)" },
  { key: "A2_perplexica", label: "A2 Perplexica", desc: "External knowledge enrichment" },
  { key: "A3_mirofish", label: "A3 MiroFish", desc: "5-frame multi-perspective" },
  { key: "AA_synthesizer", label: "AA Synth", desc: "LLM-as-Judge root cause" },
];

export default function Dashboard() {
  const [pipeStatus, setPipeStatus] = useState<StatusData | null>(null);
  const [results, setResults] = useState<ResultRow[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = async () => {
    const [s, r] = await Promise.all([
      fetch("/api/status").then((res) => res.json()).catch(() => null),
      fetch("/api/results?limit=10").then((res) => res.json()).catch(() => ({ results: [] })),
    ]);
    setPipeStatus(s);
    setResults(r.results || []);
    setLoading(false);
  };

  useEffect(() => {
    fetchAll();
    const t = setInterval(fetchAll, 15000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">GodEyes Log Analyzer</h1>
          <p className="text-gray-400 text-sm mt-1">MoA Pipeline Monitor</p>
        </div>
        <nav className="flex gap-4 text-sm">
          <Link href="/" className="text-blue-400 font-medium">Dashboard</Link>
          <Link href="/results" className="text-gray-400 hover:text-white">Results</Link>
          <Link href="/settings" className="text-gray-400 hover:text-white">Settings</Link>
        </nav>
      </div>

      {/* Pipeline Flow */}
      <div className="bg-gray-900 rounded-xl p-6 mb-6">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-5">MoA Pipeline</h2>
        <div className="flex items-center gap-2 flex-wrap">
          {AGENTS.map((agent, i) => {
            const st = pipeStatus?.agents[agent.key];
            const color = STATUS_COLOR[st?.status ?? "down"] ?? "bg-gray-600";
            return (
              <div key={agent.key} className="flex items-center gap-2">
                <div className="bg-gray-800 rounded-lg p-4 w-40">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`w-2 h-2 rounded-full ${color}`} />
                    <span className="text-xs font-bold text-white">{agent.label}</span>
                  </div>
                  <p className="text-xs text-gray-500">{agent.desc}</p>
                  <p className="text-xs mt-1 capitalize text-gray-400">{st?.status ?? "—"}</p>
                </div>
                {i < AGENTS.length - 1 && <span className="text-gray-600 text-lg">→</span>}
              </div>
            );
          })}
        </div>

        {pipeStatus?.integrations && (
          <div className="flex gap-4 mt-5 flex-wrap">
            {Object.entries(pipeStatus.integrations).map(([key, val]) => (
              <div key={key} className="bg-gray-800 rounded-lg px-4 py-2 flex items-center gap-3">
                <span className={`w-2 h-2 rounded-full ${
                  val?.status === "up" ? "bg-green-500" :
                  val?.url || val?.model ? "bg-blue-500" : "bg-gray-600"
                }`} />
                <div>
                  <p className="text-xs font-medium text-white capitalize">{key.replace(/_/g, " ")}</p>
                  <p className="text-xs text-gray-500">{val?.model ?? val?.url ?? (val?.enabled ? "enabled" : "disabled")}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent Results */}
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
                  <span className={`text-lg font-bold w-12 ${healthColor(r.health_score)}`}>
                    {Math.round(r.health_score)}
                  </span>
                  <span className={`text-xs font-bold uppercase px-2 py-0.5 rounded ${
                    r.status === "critical" ? "bg-red-900 text-red-300" :
                    r.status === "warning" ? "bg-yellow-900 text-yellow-300" :
                    "bg-green-900 text-green-300"
                  }`}>{r.status}</span>
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
