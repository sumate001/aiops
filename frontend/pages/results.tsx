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
            <DetailView data={detail} />
          )}
        </div>
      </div>
    </div>
  );
}

function DetailView({ data }: { data: any }) {
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
      {tab === "hosts" && <HostsTab hosts={data.hosts} />}
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

function HostsTab({ hosts }: { hosts: any[] }) {
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
