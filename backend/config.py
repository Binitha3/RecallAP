"""Application settings loaded from environment variables and .env."""

import json
import os
import re
import tempfile
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
_bank_routes_path = Path(__file__).resolve().parents[1] / "data" / "snapshots" / "bank_routes.json"
_GENERATED_BANK_SUFFIX = re.compile(r"^(?P<alias>.+)-demo-\d{10,14}-[0-9a-f]{6}$")


def configured_bank_alias(bank_id: str) -> str:
    """Use the stable alias when BANK_ID contains a generated demo-bank ID."""
    match = _GENERATED_BANK_SUFFIX.fullmatch(bank_id)
    return match.group("alias") if match else bank_id


_configured_bank_alias = configured_bank_alias(settings.bank_id)
_active_bank_id = _configured_bank_alias
_active_bank_lock = threading.Lock()


def _load_bank_aliases() -> dict[str, str]:
    """Restore demo-bank aliases so a server restart does not orphan retained memories."""
    try:
        payload = json.loads(_bank_routes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    aliases = payload.get("aliases", {}) if isinstance(payload, dict) else {}
    if not isinstance(aliases, dict):
        return {}
    return {
        alias: physical
        for alias, physical in aliases.items()
        if isinstance(alias, str) and alias and isinstance(physical, str) and physical
    }


_bank_aliases: dict[str, str] = _load_bank_aliases()
# The documented seed script puts its generated physical bank ID in BANK_ID.
# Treat its stable prefix as the alias so UI requests using that alias and
# requests using the configured default still reach the same physical bank.
_bank_aliases.setdefault(_configured_bank_alias, settings.bank_id)


def _persist_bank_aliases() -> None:
    """Atomically save bank aliases; API keys and other credentials are never written here."""
    _bank_routes_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=_bank_routes_path.parent,
            prefix=f"{_bank_routes_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_name = stream.name
            json.dump({"aliases": _bank_aliases}, stream, ensure_ascii=False, indent=2)
        os.replace(temp_name, _bank_routes_path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def get_active_bank_id() -> str:
    """Return the current runtime bank, which may be rotated by demo reset."""
    with _active_bank_lock:
        # The configured BANK_ID remains the stable alias when reset is called
        # with an explicit `?bank=...`. Follow its current physical bank so
        # endpoints that do not take a bank query use the same memory as the UI.
        return _bank_aliases.get(_active_bank_id, _active_bank_id)


def set_active_bank_id(bank_id: str) -> None:
    """Set the current runtime bank safely for concurrent requests."""
    global _active_bank_id
    with _active_bank_lock:
        _active_bank_id = bank_id


def resolve_bank_id(bank_id: str | None = None) -> str:
    """Resolve a requested bank alias or return the current live bank."""
    with _active_bank_lock:
        if bank_id is None:
            return _bank_aliases.get(_active_bank_id, _active_bank_id)
        return _bank_aliases.get(bank_id, bank_id)


def rotate_bank_id(bank_id: str | None = None) -> str:
    """Map an experiment/live bank name to a fresh physical bank identifier."""
    alias = bank_id or _configured_bank_alias
    fresh_id = f"{alias}-demo-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    with _active_bank_lock:
        _bank_aliases[alias] = fresh_id
        _persist_bank_aliases()
    return fresh_id


def new_bank_id(bank_id: str | None = None) -> str:
    """Return a fresh physical identifier without changing the active mapping."""
    alias = bank_id or _configured_bank_alias
    return f"{alias}-demo-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def register_bank_id(bank_id: str | None, fresh_id: str) -> None:
    """Publish an already provisioned bank for an alias or as the live bank."""
    alias = bank_id or _configured_bank_alias
    with _active_bank_lock:
        _bank_aliases[alias] = fresh_id
        _persist_bank_aliases()
