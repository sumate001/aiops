import { useEffect, useState } from "react";
import Link from "next/link";

type StageCfg = {
  override?: boolean;
  enabled?: boolean;
  provider: string;
  base_url: string;
  model: string;
  api_key?: string;
  has_api_key?: boolean;
};

type ConfigData = {
  godeye: { callback_url: string | null; enabled: boolean };
  log_ml: { base_url: string; enabled: boolean };
  perplexica: { base_url: string; enabled: boolean; chat_model: string; embedding_model: string };
  aiops_ml: { base_url: string; enabled: boolean };
  llm: {
    enabled: boolean;
    provider: string;
    base_url: string;
    model: string;
    has_api_key: boolean;
    mirofish: StageCfg;
    synthesizer: StageCfg;
    perplexica: StageCfg;
  };
};

type LlmProvider = {
  id: string;
  label: string;
  base_url: string;
  default_model: string;
  openai_compatible: boolean;
  api_key_env: string | null;
  rate_limit: string;
  notes: string;
};

type LlmForm = {
  enabled: boolean;
  provider: string;
  base_url: string;
  model: string;
  api_key: string;
  has_api_key: boolean;
  mirofish: StageForm;
  synthesizer: StageForm;
  perplexica: StageForm;
};
type StageForm = {
  override: boolean;
  provider: string;
  base_url: string;
  model: string;
  api_key: string;
  has_api_key: boolean;
};

const emptyStage = (): StageForm => ({
  override: false,
  provider: "ollama",
  base_url: "http://localhost:11434",
  model: "gemma4:e4b",
  api_key: "",
  has_api_key: false,
});

export default function Settings() {
  const [cfg, setCfg] = useState<ConfigData | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [llmProviders, setLlmProviders] = useState<LlmProvider[]>([]);

  const [form, setForm] = useState({
    godeye_callback_url: "",
    godeye_enabled: true,
    log_ml_enabled: true,
    log_ml_base_url: "http://localhost:3050",
    perplexica_enabled: false,
    perplexica_base_url: "http://localhost:3001",
    perplexica_chat_model: "gemma4:e4b",
    perplexica_embedding_model: "nomic-embed-text:latest",
  });

  const [llm, setLlm] = useState<LlmForm>({
    enabled: false,
    provider: "ollama",
    base_url: "http://localhost:11434",
    model: "gemma4:e4b",
    api_key: "",
    has_api_key: false,
    mirofish: emptyStage(),
    synthesizer: emptyStage(),
    perplexica: emptyStage(),
  });

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
          perplexica_chat_model: data.perplexica.chat_model || "gemma4:e4b",
          perplexica_embedding_model: data.perplexica.embedding_model || "nomic-embed-text:latest",
        });
        const l = data.llm;
        const stage = (s: StageCfg): StageForm => ({
          override: !!s.override,
          provider: s.provider,
          base_url: s.base_url,
          model: s.model,
          api_key: "",
          has_api_key: !!s.has_api_key,
        });
        setLlm({
          enabled: l.enabled,
          provider: l.provider,
          base_url: l.base_url,
          model: l.model,
          api_key: "",
          has_api_key: l.has_api_key,
          mirofish: stage(l.mirofish),
          synthesizer: stage(l.synthesizer),
          perplexica: stage(l.perplexica),
        });
      })
      .catch(() => setMsg({ ok: false, text: "Failed to load config from backend" }));

    fetch("/api/llm/providers")
      .then((r) => r.json())
      .then((d) => setLlmProviders(d.providers ?? []))
      .catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const stageOut = (s: StageForm) => ({
        override: s.override,
        provider: s.provider,
        base_url: s.base_url,
        model: s.model,
        api_key: s.api_key || null, // empty ⇒ backend keeps stored key
      });
      const body = {
        ...form,
        godeye_callback_url: form.godeye_callback_url || null,
        llm: {
          enabled: llm.enabled,
          provider: llm.provider,
          base_url: llm.base_url,
          model: llm.model,
          api_key: llm.api_key || null,
          mirofish: stageOut(llm.mirofish),
          synthesizer: stageOut(llm.synthesizer),
          perplexica: stageOut(llm.perplexica),
        },
      };
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      setMsg(r.ok ? { ok: true, text: data.message ?? "Saved" } : { ok: false, text: data.detail ?? "Save failed" });
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
          <Toggle label="Enabled" checked={form.godeye_enabled} onChange={(v) => setForm({ ...form, godeye_enabled: v })} />
          <Field
            label="Callback URL"
            placeholder="http://your-godeye-host/callback"
            value={form.godeye_callback_url}
            onChange={(v) => setForm({ ...form, godeye_callback_url: v })}
          />
          <p className="text-xs text-gray-500 mt-1">After each /ingest analysis, results will be POSTed as JSON to this URL.</p>
        </Section>

        {/* Isolation Forest */}
        <Section title="A1 Isolation Forest (log-ml)">
          <Toggle label="Enabled" checked={form.log_ml_enabled} onChange={(v) => setForm({ ...form, log_ml_enabled: v })} />
          <Field label="Base URL" value={form.log_ml_base_url} onChange={(v) => setForm({ ...form, log_ml_base_url: v })} />
        </Section>

        {/* AI — default + per-stage */}
        <Section title="AI (LLM) — used by MiroFish · Synthesizer · Perplexica">
          <Toggle
            label="Enable LLM enrichment"
            checked={llm.enabled}
            onChange={(v) => setLlm({ ...llm, enabled: v })}
          />
          <p className="text-xs text-gray-500 -mt-1">
            Set one <b>Default AI</b> below; every AI stage uses it unless you turn on its Override.
          </p>

          <div className="mt-2 border-t border-gray-700 pt-4">
            <p className="text-xs font-semibold text-gray-300 uppercase tracking-wide mb-3">Default AI</p>
            <LlmPicker
              providers={llmProviders}
              value={llm}
              hasKey={llm.has_api_key}
              onChange={(patch) => setLlm({ ...llm, ...patch })}
            />
          </div>

          <StageOverride
            title="MiroFish (A3)"
            providers={llmProviders}
            stage={llm.mirofish}
            fallback={llm}
            onChange={(s) => setLlm({ ...llm, mirofish: s })}
          />
          <StageOverride
            title="Synthesizer (AA)"
            providers={llmProviders}
            stage={llm.synthesizer}
            fallback={llm}
            onChange={(s) => setLlm({ ...llm, synthesizer: s })}
          />
          <StageOverride
            title="Perplexica (A2)"
            providers={llmProviders}
            stage={llm.perplexica}
            fallback={llm}
            onChange={(s) => setLlm({ ...llm, perplexica: s })}
            note="Embeddings always stay on the local Ollama/Transformers model; this only switches the chat model."
          />
        </Section>

        {/* Perplexica service */}
        <Section title="A2 Perplexica (External Knowledge)">
          <Toggle label="Enabled" checked={form.perplexica_enabled} onChange={(v) => setForm({ ...form, perplexica_enabled: v })} />
          <Field label="Perplexica Base URL" value={form.perplexica_base_url} onChange={(v) => setForm({ ...form, perplexica_base_url: v })} />
          <Field
            label="Embedding Model (local)"
            placeholder="nomic-embed-text:latest"
            value={form.perplexica_embedding_model}
            onChange={(v) => setForm({ ...form, perplexica_embedding_model: v })}
          />
          <p className="text-xs text-gray-500">
            Chat model/provider for A2 is controlled in the <b>AI (LLM)</b> section above (Perplexica stage).
          </p>
        </Section>

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

// ── Reusable LLM provider/model picker ─────────────────────────────────────
function LlmPicker({
  providers,
  value,
  hasKey,
  onChange,
}: {
  providers: LlmProvider[];
  value: { provider: string; base_url: string; model: string; api_key: string };
  hasKey: boolean;
  onChange: (patch: Partial<{ provider: string; base_url: string; model: string; api_key: string }>) => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [modelsMsg, setModelsMsg] = useState<string | null>(null);

  const fetchModels = async () => {
    if (!value.base_url) return;
    setModelsMsg("Loading models…");
    try {
      const q = new URLSearchParams({ provider: value.provider, base_url: value.base_url });
      if (value.api_key) q.set("api_key", value.api_key);
      const r = await fetch(`/api/llm/models?${q.toString()}`);
      const d = await r.json();
      const m: string[] = d.models ?? [];
      setModels(m);
      setModelsMsg(m.length ? null : `No models${d.error ? ` (${d.error})` : ""}`);
    } catch {
      setModels([]);
      setModelsMsg("Failed to reach endpoint");
    }
  };

  const selectProvider = (id: string) => {
    const p = providers.find((x) => x.id === id);
    onChange({ provider: id, base_url: p ? p.base_url : value.base_url, model: p ? p.default_model : value.model });
    setModels([]);
    setModelsMsg(null);
  };

  const info = providers.find((x) => x.id === value.provider);

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs text-gray-400 mb-1">Provider</label>
        <select
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
          value={value.provider}
          onChange={(e) => selectProvider(e.target.value)}
        >
          {providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        {info && (
          <p className="text-xs text-gray-500 mt-1">{info.rate_limit && <>Free tier: {info.rate_limit}. </>}{info.notes}</p>
        )}
      </div>
      <Field label="Base URL" value={value.base_url} onChange={(v) => onChange({ base_url: v })} />
      {value.provider !== "ollama" && (
        <div>
          <label className="block text-xs text-gray-400 mb-1">
            API Key {hasKey && <span className="text-green-500">(saved — leave blank to keep)</span>}
            {info?.api_key_env && <span className="text-gray-500"> · env: {info.api_key_env}</span>}
          </label>
          <input
            type="password"
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            value={value.api_key}
            onChange={(e) => onChange({ api_key: e.target.value })}
            placeholder={hasKey ? "••••••••" : "sk-..."}
          />
        </div>
      )}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="block text-xs text-gray-400">Model</label>
          <button type="button" onClick={() => fetchModels()} className="text-xs text-blue-400 hover:text-blue-300">↻ Refresh models</button>
        </div>
        {modelsMsg && <p className="text-xs text-yellow-500 mb-1">{modelsMsg}</p>}
        {models.length > 0 ? (
          <select
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            value={value.model}
            onChange={(e) => onChange({ model: e.target.value })}
          >
            {(value.model && !models.includes(value.model) ? [value.model, ...models] : models).map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        ) : (
          <input
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            value={value.model}
            onChange={(e) => onChange({ model: e.target.value })}
            placeholder="e.g. llama-3.3-70b-versatile"
          />
        )}
      </div>
    </div>
  );
}

// ── Per-stage override panel ───────────────────────────────────────────────
function StageOverride({
  title,
  providers,
  stage,
  fallback,
  onChange,
  note,
}: {
  title: string;
  providers: LlmProvider[];
  stage: StageForm;
  fallback: { provider: string; model: string };
  onChange: (s: StageForm) => void;
  note?: string;
}) {
  return (
    <div className="mt-2 border-t border-gray-700 pt-4">
      <Toggle label={`${title} — override`} checked={stage.override} onChange={(v) => onChange({ ...stage, override: v })} />
      {note && <p className="text-xs text-gray-500 mt-1">{note}</p>}
      {!stage.override ? (
        <p className="text-xs text-gray-500 mt-1">Using Default AI ({fallback.provider} · {fallback.model})</p>
      ) : (
        <div className="mt-3">
          <LlmPicker
            providers={providers}
            value={stage}
            hasKey={stage.has_api_key}
            onChange={(patch) => onChange({ ...stage, ...patch })}
          />
        </div>
      )}
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
  onBlur,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onBlur?: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      <input
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={(e) => onBlur?.(e.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
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
