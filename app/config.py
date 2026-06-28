import os
import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class HttpConfig(BaseModel):
    addr: str = "0.0.0.0:8200"


class LoggerConfig(BaseModel):
    level: str = "info"


class AiopsMlConfig(BaseModel):
    base_url: str = "http://aiops-ml:8100"
    timeout: str = "60s"
    enabled: bool = True


class LogMlConfig(BaseModel):
    base_url: str = "http://localhost:3050"
    timeout: str = "10s"
    enabled: bool = True


class GodEyeConfig(BaseModel):
    callback_url: str | None = None   # POST AnalyzeResponse กลับหา GodEye หลัง /ingest
    callback_timeout: str = "10s"
    enabled: bool = True


class PerplexicaConfig(BaseModel):
    base_url: str = "http://localhost:3001"
    timeout: str = "90s"  # web-search enrichment; "speed" mode answers in ~20s
    chat_model: str = "gemma4:e4b"
    # Embeddings run on Vane's local Transformers provider (no Ollama). This is
    # passed as the *preferred* key; if it isn't found, _get_embedding falls back
    # to the first Transformers model anyway — so A2 never touches Ollama.
    embedding_model: str = "Xenova/all-MiniLM-L6-v2"
    # Vane optimizationMode. "speed" is the reliable choice on free Groq models
    # (returns web sources, no tool-use hangs); "balanced"/"quality" can stall.
    mode: str = "speed"
    enabled: bool = False


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "gemma4:e4b"
    timeout: str = "120s"
    temperature: float = 0.1


class StageLLMConfig(BaseModel):
    """Per-stage LLM override. When `override` is False the stage inherits the
    global `LLMConfig` defaults; when True it uses its own provider/model/key."""

    override: bool = False
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "gemma4:e4b"
    api_key: str | None = None


class LLMConfig(BaseModel):
    """Provider-agnostic LLM gateway used by every AI stage (MiroFish,
    AA Synthesizer, Perplexica). The top-level fields are the *global default*;
    each stage can override it via the nested StageLLMConfig blocks. `provider`
    is one of the ids in app.services.llm_providers; for OpenAI-compatible
    providers `api_key` is sent as a Bearer token (provider == "ollama" → native
    /api/generate)."""

    enabled: bool = False
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "gemma4:e4b"
    api_key: str | None = None
    timeout: str = "120s"
    temperature: float = 0.1
    # per-stage overrides
    mirofish: StageLLMConfig = StageLLMConfig()
    synthesizer: StageLLMConfig = StageLLMConfig()
    perplexica: StageLLMConfig = StageLLMConfig()

    def resolve(self, stage: str) -> "ResolvedLLM":
        """Effective provider/model/base_url/api_key for a given stage."""
        s: StageLLMConfig = getattr(self, stage)
        if s.override:
            return ResolvedLLM(s.provider, s.base_url, s.model, s.api_key)
        return ResolvedLLM(self.provider, self.base_url, self.model, self.api_key)


class ResolvedLLM:
    __slots__ = ("provider", "base_url", "model", "api_key")

    def __init__(self, provider: str, base_url: str, model: str, api_key: str | None):
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.api_key = api_key


class HealthScoreConfig(BaseModel):
    critical_weight: float = 2.0
    warn_weight: float = 1.0
    score_floor: float = 0.0
    score_ceiling: float = 100.0


class AnalysisConfig(BaseModel):
    max_log_entries: int = 500
    severity_filter: str = "warn"
    health_score: HealthScoreConfig = HealthScoreConfig()


class AppConfig(BaseModel):
    http: HttpConfig = HttpConfig()
    logger: LoggerConfig = LoggerConfig()
    aiops_ml: AiopsMlConfig = AiopsMlConfig()
    log_ml: LogMlConfig = LogMlConfig()
    godeye: GodEyeConfig = GodEyeConfig()
    perplexica: PerplexicaConfig = PerplexicaConfig()
    ollama: OllamaConfig = OllamaConfig()
    llm: LLMConfig = LLMConfig()
    analysis: AnalysisConfig = AnalysisConfig()


def _parse_timeout(value: str) -> float:
    """Convert '60s' or '120s' to float seconds."""
    value = value.strip()
    if value.endswith("s"):
        return float(value[:-1])
    if value.endswith("m"):
        return float(value[:-1]) * 60
    return float(value)


def _apply_env_overrides(cfg: AppConfig) -> AppConfig:
    """Allow key settings to be overridden via environment variables."""
    env = os.environ
    if v := env.get("OLLAMA_BASE_URL"):
        cfg.ollama.base_url = v
    if v := env.get("OLLAMA_MODEL"):
        cfg.ollama.model = v
    if v := env.get("LOG_ML_BASE_URL"):
        cfg.log_ml.base_url = v
    if v := env.get("LOG_ML_ENABLED"):
        cfg.log_ml.enabled = v.lower() not in ("0", "false", "no")
    if v := env.get("PERPLEXICA_BASE_URL"):
        cfg.perplexica.base_url = v
    if v := env.get("PERPLEXICA_ENABLED"):
        cfg.perplexica.enabled = v.lower() not in ("0", "false", "no")
    if v := env.get("PERPLEXICA_CHAT_MODEL"):
        cfg.perplexica.chat_model = v
    if v := env.get("LLM_ENABLED"):
        cfg.llm.enabled = v.lower() not in ("0", "false", "no")
    if v := env.get("LLM_PROVIDER"):
        cfg.llm.provider = v
    if v := env.get("LLM_BASE_URL"):
        cfg.llm.base_url = v
    if v := env.get("LLM_MODEL"):
        cfg.llm.model = v
    if v := env.get("LLM_API_KEY"):
        cfg.llm.api_key = v
    if v := env.get("CALLBACK_URL"):
        cfg.godeye.callback_url = v
    if v := env.get("CALLBACK_ENABLED"):
        cfg.godeye.enabled = v.lower() not in ("0", "false", "no")
    return cfg


def load_config() -> AppConfig:
    for path in ("config.yaml", "config.yaml.example"):
        if os.path.exists(path):
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return _apply_env_overrides(AppConfig.model_validate(data))
    return _apply_env_overrides(AppConfig())


config = load_config()

OLLAMA_TIMEOUT = _parse_timeout(config.ollama.timeout)
LLM_TIMEOUT = _parse_timeout(config.llm.timeout)
AIOPS_ML_TIMEOUT = _parse_timeout(config.aiops_ml.timeout)
LOG_ML_TIMEOUT = _parse_timeout(config.log_ml.timeout)
PERPLEXICA_TIMEOUT = _parse_timeout(config.perplexica.timeout)

SEVERITY_THRESHOLD: dict[str, int] = {
    "trace": 1,
    "debug": 5,
    "info": 9,
    "warn": 13,
    "error": 17,
    "fatal": 21,
}
