"""Application settings loaded from environment variables and .env."""

import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """Load simple KEY=VALUE entries without overriding the process environment."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for RecallAP."""

    groq_api_key: str = ""
    groq_model_primary: str = "openai/gpt-oss-120b"
    groq_model_fallback: str = "qwen/qwen3.8-27b"
    hindsight_base_url: str = ""
    hindsight_api_key: str = ""
    bank_id: str = "recallap-demo"
    retain_agent_decisions: bool = False

    @classmethod
    def from_environment(cls) -> "Settings":
        """Read configured values, applying defaults for unset variables."""
        retain = os.getenv("RETAIN_AGENT_DECISIONS", "false").strip().lower()
        return cls(
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            groq_model_primary=os.getenv("GROQ_MODEL_PRIMARY", "openai/gpt-oss-120b"),
            groq_model_fallback=os.getenv("GROQ_MODEL_FALLBACK", "qwen/qwen3.8-27b"),
            hindsight_base_url=os.getenv("HINDSIGHT_BASE_URL", ""),
            hindsight_api_key=os.getenv("HINDSIGHT_API_KEY", ""),
            bank_id=os.getenv("BANK_ID", "recallap-demo"),
            retain_agent_decisions=retain in {"1", "true", "yes", "on"},
        )


settings = Settings.from_environment()
_active_bank_id = settings.bank_id
_active_bank_lock = threading.Lock()
_bank_aliases: dict[str, str] = {}


def get_active_bank_id() -> str:
    """Return the current runtime bank, which may be rotated by demo reset."""
    with _active_bank_lock:
        return _active_bank_id


def set_active_bank_id(bank_id: str) -> None:
    """Set the current runtime bank safely for concurrent requests."""
    global _active_bank_id
    with _active_bank_lock:
        _active_bank_id = bank_id


def resolve_bank_id(bank_id: str | None = None) -> str:
    """Resolve a requested bank alias or return the current live bank."""
    with _active_bank_lock:
        if bank_id is None:
            return _active_bank_id
        return _bank_aliases.get(bank_id, bank_id)


def rotate_bank_id(bank_id: str | None = None) -> str:
    """Map an experiment/live bank name to a fresh physical bank identifier."""
    global _active_bank_id
    alias = bank_id or settings.bank_id
    fresh_id = f"{alias}-demo-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    with _active_bank_lock:
        if bank_id is None:
            _active_bank_id = fresh_id
        else:
            _bank_aliases[alias] = fresh_id
    return fresh_id


def new_bank_id(bank_id: str | None = None) -> str:
    """Return a fresh physical identifier without changing the active mapping."""
    alias = bank_id or settings.bank_id
    return f"{alias}-demo-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def register_bank_id(bank_id: str | None, fresh_id: str) -> None:
    """Publish an already provisioned bank for an alias or as the live bank."""
    global _active_bank_id
    alias = bank_id or settings.bank_id
    with _active_bank_lock:
        if bank_id is None:
            _active_bank_id = fresh_id
        else:
            _bank_aliases[alias] = fresh_id
