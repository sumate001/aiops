import { useEffect, useState } from "react";
import Link from "next/link";

type ConfigData = {
  godeye: { callback_url: string | null; enabled: boolean };
  log_ml: { base_url: string; enabled: boolean };
  perplexica: { base_url: string; enabled: boolean; chat_model: string; embedding_model: string };
  ollama: { base_url: string; model: string; timeout: string; temperature: number };
  aiops_ml: { base_url: string; enabled: boolean };
};

export default function Settings() {
  const [cfg, setCfg] = useState<ConfigData | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [form, setForm] = useState({
    godeye_callback_url: "",
    godeye_enabled: true,
    log_ml_enabled: true,
    log_ml_base_url: "http://localhost:3050",
    perplexica_enabled: false,
    perplexica_base_url: "http://localhost:3001",
    perplexica_chat_model: "qwen3.6:27b",
    perplexica_embedding_model: "nomic-embed-text:latest",
    ollama_base_url: "http://localhost:11434",
    ollama_model: "qwen2.5:14b",
  });

  const [ollamaModels, setOllamaModels] = useState<string[]>([]);

  const fetchOllamaModels = async (baseUrl: string) => {
    try {
      const r = await fetch(`/ollama-proxy/api/tags`);
      const d = await r.json();
      const models = (d.models ?? []).map((m: any) => m.name as string);
      setOllamaModels(models);
    } catch {}
  };

  const [perplexicaCfg, setPerplexicaCfg] = useState({
    ollama_url: "http://host.docker.internal:11434",
    chat_model: "",
    embedding_model: "",
  });
  const [perplexicaModels, setPerplexicaModels] = useState<string[]>([]);
  const [perplexicaSaving, setPerplexicaSaving] = useState(false);
  const [perplexicaMsg, setPerplexicaMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const fetchPerplexicaConfig = async (_baseUrl: string) => {
    try {
      // ดึง Ollama URL จาก /api/config
      const cfgR = await fetch(`/perplexica/api/config`);
      const cfg = await cfgR.json();
      const ollamaCfg = cfg.values?.modelProviders?.find((p: any) => p.type === "ollama" || p.id === "ollama");
      if (ollamaCfg?.config?.baseURL) {
        setPerplexicaCfg((prev) => ({ ...prev, ollama_url: ollamaCfg.config.baseURL }));
      }

      // ดึง models จาก /api/providers (มี chatModels จริง)
      const provR = await fetch(`/perplexica/api/providers`);
      const prov = await provR.json();
      const ollama = prov.providers?.find((p: any) => p.type === "ollama");
      if (ollama) {
        const models = ollama.chatModels
          ?.filter((m: any) => m.key !== "error")
          .map((m: any) => m.key) ?? [];
        setPerplexicaModels(models);
        if (models.length > 0) {
          setPerplexicaCfg((prev) => ({ ...prev, chat_model: prev.chat_model || models[0] }));
          setForm((prev) => ({ ...prev, perplexica_chat_model: prev.perplexica_chat_model || models[0] }));
        }
      }
    } catch {}
  };

  const savePerplexicaConfig = async () => {
    setPerplexicaSaving(true);
    setPerplexicaMsg(null);
    try {
      await fetch(`/perplexica/api/providers/ollama`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: { baseURL: perplexicaCfg.ollama_url } }),
      });
      await fetchPerplexicaConfig(form.perplexica_base_url);
      setPerplexicaMsg({ ok: true, text: "Perplexica Ollama URL updated. Models refreshed." });
    } catch (e) {
      setPerplexicaMsg({ ok: false, text: "Failed to update Perplexica — is it running at port 3001?" });
    }
    setPerplexicaSaving(false);
  };

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((data: ConfigData) => {
        setCfg(data);
        setForm({
          godeye_callback_url: data.godeye.callback_url ?? "",
          godeye_enabled: data.godeye.enabled,
          log_ml_enabled: data.log_ml.enabled,
          log_ml_base_url: data.log_ml.base_url,
          perplexica_enabled: data.perplexica.enabled,
          perplexica_base_url: data.perplexica.base_url,
          perplexica_chat_model: data.perplexica.chat_model || "qwen3.6:27b",
          perplexica_embedding_model: data.perplexica.embedding_model || "nomic-embed-text:latest",
          ollama_base_url: data.ollama.base_url,
          ollama_model: data.ollama.model,
        });
        fetchPerplexicaConfig(data.perplexica.base_url).catch(() => {});
        fetchOllamaModels(data.ollama.base_url).catch(() => {});
      })
      .catch(() => setMsg({ ok: false, text: "Failed to load config from backend" }));
  }, []);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...form,
          godeye_callback_url: form.godeye_callback_url || null,
        }),
      });
      const data = await r.json();
      if (r.ok) {
        setMsg({ ok: true, text: data.message ?? "Saved" });
      } else {
        setMsg({ ok: false, text: data.detail ?? "Save failed" });
      }
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    }
    setSaving(false);
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Settings</h1>
          <p className="text-gray-400 text-sm mt-1">Pipeline configuration</p>
        </div>
        <nav className="flex gap-4 text-sm">
          <Link href="/" className="text-gray-400 hover:text-white">Dashboard</Link>
          <Link href="/results" className="text-gray-400 hover:text-white">Results</Link>
          <Link href="/settings" className="text-blue-400 font-medium">Settings</Link>
        </nav>
      </div>

      <div className="max-w-2xl space-y-6">
        {/* GodEye Callback */}
        <Section title="GodEye Callback">
          <Toggle
            label="Enabled"
            checked={form.godeye_enabled}
            onChange={(v) => setForm({ ...form, godeye_enabled: v })}
          />
          <Field
            label="Callback URL"
            placeholder="http://your-godeye-host/callback"
            value={form.godeye_callback_url}
            onChange={(v) => setForm({ ...form, godeye_callback_url: v })}
          />
          <p className="text-xs text-gray-500 mt-1">
            After each /ingest analysis, results will be POSTed as JSON to this URL.
          </p>
        </Section>

        {/* Isolation Forest */}
        <Section title="A1 Isolation Forest (log-ml)">
          <Toggle
            label="Enabled"
            checked={form.log_ml_enabled}
            onChange={(v) => setForm({ ...form, log_ml_enabled: v })}
          />
          <Field
            label="Base URL"
            value={form.log_ml_base_url}
            onChange={(v) => setForm({ ...form, log_ml_base_url: v })}
          />
        </Section>

        {/* Perplexica */}
        <Section title="A2 Perplexica (External Knowledge)">
          <Toggle
            label="Enabled"
            checked={form.perplexica_enabled}
            onChange={(v) => setForm({ ...form, perplexica_enabled: v })}
          />
          <Field
            label="Perplexica Base URL"
            value={form.perplexica_base_url}
            onChange={(v) => setForm({ ...form, perplexica_base_url: v })}
          />
          <p className="text-xs text-gray-500">
            Perplexica UI: <a href={form.perplexica_base_url} target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">{form.perplexica_base_url}</a>
          </p>

          {/* Perplexica Ollama Model Config */}
          <div className="mt-4 border-t border-gray-700 pt-4 space-y-3">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Perplexica → Ollama Model</p>
            <Field
              label="Ollama Base URL"
              placeholder="http://localhost:11434"
              value={perplexicaCfg.ollama_url}
              onChange={(v) => setPerplexicaCfg({ ...perplexicaCfg, ollama_url: v })}
            />
            {(() => {
              const models = perplexicaModels.length > 0 ? perplexicaModels : ollamaModels;
              return models.length > 0 ? (
                <div>
                  <label className="block text-xs text-gray-400 mb-1">
                    Chat Model {perplexicaModels.length === 0 && <span className="text-yellow-500">(from Ollama direct)</span>}
                  </label>
                  <select
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                    value={form.perplexica_chat_model}
                    onChange={(e) => setForm({ ...form, perplexica_chat_model: e.target.value })}
                  >
                    {models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
              ) : (
                <p className="text-xs text-gray-500">No models loaded — save Ollama URL then click Refresh</p>
              );
            })()}
            {perplexicaMsg && (
              <div className={`rounded-lg px-3 py-2 text-xs ${perplexicaMsg.ok ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"}`}>
                {perplexicaMsg.text}
              </div>
            )}
            <div className="flex gap-2">
              <button
                onClick={savePerplexicaConfig}
                disabled={perplexicaSaving}
                className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-medium px-4 py-1.5 rounded-lg transition-colors"
              >
                {perplexicaSaving ? "Saving..." : "Save & Refresh Models"}
              </button>
              <button
                onClick={() => fetchPerplexicaConfig(form.perplexica_base_url).catch(() => {})}
                className="bg-gray-700 hover:bg-gray-600 text-white text-xs font-medium px-4 py-1.5 rounded-lg transition-colors"
              >
                Refresh Models
              </button>
            </div>
          </div>
        </Section>

        {/* Ollama — log-analyzer (native process) */}
        <Section title="Ollama LLM">
          <Field
            label="Base URL"
            placeholder="http://localhost:11434"
            value={form.ollama_base_url}
            onChange={(v) => setForm({ ...form, ollama_base_url: v })}
          />
          <div>
            <label className="block text-xs text-gray-400 mb-1">Model</label>
            {ollamaModels.length > 0 ? (
              <select
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                value={form.ollama_model}
                onChange={(e) => setForm({ ...form, ollama_model: e.target.value })}
              >
                {ollamaModels.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : (
              <input
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                value={form.ollama_model}
                onChange={(e) => setForm({ ...form, ollama_model: e.target.value })}
                placeholder="e.g. gemma4:12b"
              />
            )}
          </div>
          {cfg && (
            <p className="text-xs text-gray-500 mt-1">
              Temperature: {cfg.ollama.temperature} · Timeout: {cfg.ollama.timeout}
            </p>
          )}
        </Section>

        {/* Read-only info */}
        {cfg && (
          <Section title="aiops-ml (read-only)">
            <div className="text-xs text-gray-400 space-y-1">
              <div>URL: {cfg.aiops_ml.base_url}</div>
              <div>Enabled: {cfg.aiops_ml.enabled ? "yes" : "no"}</div>
            </div>
          </Section>
        )}

        {msg && (
          <div className={`rounded-lg px-4 py-3 text-sm ${msg.ok ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"}`}>
            {msg.text}
          </div>
        )}

        <button
          onClick={save}
          disabled={saving}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium px-6 py-2.5 rounded-lg transition-colors"
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 rounded-xl p-5">
      <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">{title}</h3>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      <input
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={() => onChange(!checked)}
        className={`relative w-10 h-5 rounded-full transition-colors ${checked ? "bg-blue-600" : "bg-gray-700"}`}
      >
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${checked ? "translate-x-5" : ""}`} />
      </button>
      <span className="text-sm text-gray-300">{label}</span>
    </div>
  );
}
