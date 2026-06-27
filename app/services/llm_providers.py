"""Registry of free / freemium LLM API providers.

Sourced from https://github.com/mnfst/awesome-free-llm-apis

Every entry except `ollama` speaks the OpenAI chat-completions wire format
(`POST {base_url}/chat/completions`), so a single OpenAI-compatible client in
`llm.py` can drive all of them. `ollama` uses its native `/api/generate` route.

`api_key_env` is the environment variable the unified client reads the key from
when the user hasn't stored one in config.yaml. Providers with
`api_key_env=None` need no key (anonymous access).
"""

from pydantic import BaseModel


class ProviderInfo(BaseModel):
    id: str
    label: str
    base_url: str
    default_model: str
    # OpenAI-compatible chat/completions wire format. False ⇒ handled natively.
    openai_compatible: bool = True
    # Env var the API key is read from. None ⇒ no key required (anonymous).
    api_key_env: str | None = None
    # Free-tier rate limit, for display only.
    rate_limit: str = ""
    notes: str = ""


# Order roughly by "easiest to start with" (no key / generous free tier first).
PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(
        id="ollama",
        label="Ollama (local / self-hosted)",
        base_url="http://localhost:11434",
        default_model="gemma4:e4b",
        openai_compatible=False,
        api_key_env=None,
        rate_limit="unlimited (your hardware)",
        notes="Native /api/generate. Default provider for this project.",
    ),
    ProviderInfo(
        id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        api_key_env="GROQ_API_KEY",
        rate_limit="30 RPM, 1K RPD",
        notes="Very fast. No credit card. Key from console.groq.com.",
    ),
    ProviderInfo(
        id="cerebras",
        label="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        default_model="gpt-oss-120b",
        api_key_env="CEREBRAS_API_KEY",
        rate_limit="30 RPM, 1M TPD",
        notes="Fastest inference. No credit card.",
    ),
    ProviderInfo(
        id="gemini",
        label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        rate_limit="5-30 RPM",
        notes="Use the OpenAI-compat endpoint (/v1beta/openai). Key from aistudio.google.com.",
    ),
    ProviderInfo(
        id="openrouter",
        label="OpenRouter (free models)",
        base_url="https://openrouter.ai/api/v1",
        default_model="deepseek/deepseek-chat-v3.1:free",
        api_key_env="OPENROUTER_API_KEY",
        rate_limit="20 RPM, 200 RPD",
        notes="Use models with the ':free' suffix. Optional key.",
    ),
    ProviderInfo(
        id="mistral",
        label="Mistral AI",
        base_url="https://api.mistral.ai/v1",
        default_model="mistral-small-latest",
        api_key_env="MISTRAL_API_KEY",
        rate_limit="~1 RPS, 500K TPM",
        notes="No credit card.",
    ),
    ProviderInfo(
        id="cohere",
        label="Cohere",
        base_url="https://api.cohere.com/v2",
        default_model="command-r",
        api_key_env="COHERE_API_KEY",
        rate_limit="20 RPM",
        notes="Trial API key.",
    ),
    ProviderInfo(
        id="sambanova",
        label="SambaNova",
        base_url="https://api.sambanova.ai/v1",
        default_model="Meta-Llama-3.3-70B-Instruct",
        api_key_env="SAMBANOVA_API_KEY",
        rate_limit="20 RPM, 200K TPD",
        notes="No credit card.",
    ),
    ProviderInfo(
        id="nvidia",
        label="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        default_model="meta/llama-3.1-70b-instruct",
        api_key_env="NVIDIA_API_KEY",
        rate_limit="~40 RPM",
        notes="NVIDIA Developer account.",
    ),
    ProviderInfo(
        id="github",
        label="GitHub Models",
        base_url="https://models.github.ai/inference",
        default_model="gpt-4.1-mini",
        api_key_env="GITHUB_TOKEN",
        rate_limit="10-15 RPM",
        notes="Uses a GitHub PAT as the API key.",
    ),
    ProviderInfo(
        id="siliconflow",
        label="SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen3-8B",
        api_key_env="SILICONFLOW_API_KEY",
        rate_limit="30 RPM, 60K TPM",
    ),
    ProviderInfo(
        id="llm7",
        label="LLM7.io",
        base_url="https://api.llm7.io/v1",
        default_model="deepseek-v4-flash",
        api_key_env="LLM7_API_KEY",
        rate_limit="30 RPM",
        notes="Needs a free token from token.llm7.io for most models.",
    ),
    ProviderInfo(
        id="ovhcloud",
        label="OVHcloud AI Endpoints",
        base_url="https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        default_model="Qwen3.5-9B",
        api_key_env="OVH_API_KEY",
        rate_limit="2 RPM (anonymous)",
        notes="Verified working with NO key (2 RPM). Bigger models need a free key.",
    ),
]

PROVIDERS_BY_ID: dict[str, ProviderInfo] = {p.id: p for p in PROVIDERS}


def get_provider(provider_id: str) -> ProviderInfo | None:
    return PROVIDERS_BY_ID.get(provider_id)
