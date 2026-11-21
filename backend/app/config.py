import os
from dataclasses import dataclass, field


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def sqlite_path_from_url(url: str) -> str:
    """sqlite:////data/app.db -> /data/app.db, sqlite:///./app.db -> ./app.db"""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("DATABASE_URL must look like sqlite:///relative.db or sqlite:////abs/path.db")
    return url[len(prefix):]


@dataclass
class Settings:
    api_keys: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    database_url: str = "sqlite:///./app.db"
    allowed_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    enable_failure_simulation: bool = False
    cache_type: str = "disk"
    cache_dir: str = "./.llm_cache"
    cache_ttl: int = 86400
    default_system_prompt: str = "You are a helpful assistant. Keep answers clear and concise."
    request_timeout: float = 30.0
    max_history_messages: int = 40

    @classmethod
    def from_env(cls) -> "Settings":
        keys = _split(os.getenv("LLM_API_KEYS"))
        default_model = os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash").strip()
        per_key_models = _split(os.getenv("LLM_MODELS"))
        # LLM_MODELS is optional. If given, it maps one model to each key in order;
        # missing entries fall back to LLM_MODEL.
        models = [per_key_models[i] if i < len(per_key_models) else default_model for i in range(len(keys))]

        return cls(
            api_keys=keys,
            models=models,
            database_url=os.getenv("DATABASE_URL", "sqlite:///./app.db"),
            allowed_origins=_split(os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")),
            enable_failure_simulation=_bool(os.getenv("ENABLE_FAILURE_SIMULATION")),
            cache_type=os.getenv("CACHE_TYPE", "disk").strip().lower(),
            cache_dir=os.getenv("CACHE_DIR", "./.llm_cache"),
            cache_ttl=int(os.getenv("CACHE_TTL_SECONDS", "86400")),
            default_system_prompt=os.getenv("DEFAULT_SYSTEM_PROMPT", cls.default_system_prompt),
            request_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "40")),
        )

    @property
    def db_path(self) -> str:
        return sqlite_path_from_url(self.database_url)
