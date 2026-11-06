"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.config import settings

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
FRONTEND_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RecallAP", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    """Report service availability and whether external integrations are configured."""
    return {
        "status": "ok",
        "hindsight_ok": bool(settings.hindsight_api_key and settings.hindsight_base_url),
        "llm_ok": bool(settings.groq_api_key),
    }


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
