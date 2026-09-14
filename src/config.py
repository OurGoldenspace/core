"""
Configuration loaded from the environment on every read.

Properties read os.environ live so tests can isolate DATABASE_URL
without reimporting the process.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings. Values come from the environment at access time."""

    def _get(self, key: str, default: str) -> str:
        return os.getenv(key, default)

    @property
    def DATABASE_URL(self) -> str:
        return self._get("DATABASE_URL", "sqlite+aiosqlite:///./workcore.db")

    @property
    def ANTHROPIC_API_KEY(self) -> str:
        return self._get("ANTHROPIC_API_KEY", "")

    @property
    def GROQ_API_KEY(self) -> str:
        return self._get("GROQ_API_KEY", "")

    @property
    def LLM_MODEL(self) -> str:
        return self._get("LLM_MODEL", "claude-3-5-sonnet-20241022")

    @property
    def GROQ_MODEL(self) -> str:
        return self._get("GROQ_MODEL", "openai/gpt-oss-20b")

    @property
    def LLM_PROVIDER(self) -> str:
        return self._get("LLM_PROVIDER", "auto").strip().lower()

    @property
    def LLM_MAX_TOKENS(self) -> int:
        return int(self._get("LLM_MAX_TOKENS", "1024"))

    @property
    def LLM_TIMEOUT_SECONDS(self) -> int:
        return int(self._get("LLM_TIMEOUT_SECONDS", "30"))

    @property
    def MAX_AGENT_ITERATIONS(self) -> int:
        return int(self._get("MAX_AGENT_ITERATIONS", "5"))

    @property
    def APPROVAL_THRESHOLD(self) -> float:
        return float(self._get("APPROVAL_THRESHOLD", "5000"))

    @property
    def CONTEXT_TOKEN_BUDGET(self) -> int:
        return int(self._get("CONTEXT_TOKEN_BUDGET", "4000"))

    @property
    def CLAIM_STALE_SECONDS(self) -> int:
        return int(self._get("CLAIM_STALE_SECONDS", "30"))

    @property
    def MAX_JOB_RETRIES(self) -> int:
        return int(self._get("MAX_JOB_RETRIES", "3"))

    @property
    def JOB_RETRY_BASE_SECONDS(self) -> int:
        return int(self._get("JOB_RETRY_BASE_SECONDS", "2"))

    @property
    def JOB_HEARTBEAT_SECONDS(self) -> float:
        return float(self._get("JOB_HEARTBEAT_SECONDS", "5"))

    @property
    def EMBEDDING_DIMENSIONS(self) -> int:
        return int(self._get("EMBEDDING_DIMENSIONS", "64"))

    @property
    def RETRIEVAL_CHUNK_CHARS(self) -> int:
        return int(self._get("RETRIEVAL_CHUNK_CHARS", "800"))

    @property
    def RETRIEVAL_CHUNK_OVERLAP_CHARS(self) -> int:
        return int(self._get("RETRIEVAL_CHUNK_OVERLAP_CHARS", "120"))

    @property
    def SERVER_HOST(self) -> str:
        return self._get("SERVER_HOST", "0.0.0.0")

    @property
    def SERVER_PORT(self) -> int:
        return int(self._get("SERVER_PORT", "8000"))

    @property
    def DEBUG(self) -> bool:
        return self._get("DEBUG", "false").lower() == "true"

    @property
    def LOG_LEVEL(self) -> str:
        return self._get("LOG_LEVEL", "INFO")

    @property
    def API_KEY(self) -> str:
        return self._get("API_KEY", "test-key-12345")

    @property
    def TENANT_NAME(self) -> str:
        return self._get("TENANT_NAME", "demo-tenant")

    @property
    def has_anthropic(self) -> bool:
        key = self.ANTHROPIC_API_KEY.strip()
        return bool(key) and not key.startswith("your-api-key")

    @property
    def has_groq(self) -> bool:
        key = self.GROQ_API_KEY.strip()
        return bool(key) and not key.startswith("your-api-key")

    @property
    def llm_provider(self) -> str:
        if self.LLM_PROVIDER == "policy":
            return "policy"
        if self.LLM_PROVIDER == "groq":
            return "groq" if self.has_groq else "policy"
        if self.LLM_PROVIDER == "anthropic":
            return "anthropic" if self.has_anthropic else "policy"
        if self.has_groq:
            return "groq"
        if self.has_anthropic:
            return "anthropic"
        return "policy"

    @property
    def active_llm_model(self) -> str:
        if self.llm_provider == "groq":
            return self.GROQ_MODEL
        if self.llm_provider == "anthropic":
            return self.LLM_MODEL
        return "policy"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
