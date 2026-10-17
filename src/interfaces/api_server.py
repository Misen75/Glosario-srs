"""Backend FastAPI Cloud-native: Servidor principal y composition de routers.

Monta los routers modulares:
  * web_routes.py: Endpoints REST para PWA protegidos por X-Web-Pin.
  * webhook_routes.py: Telegram Webhook validando X-Telegram-Bot-Api-Secret-Token y ALLOWED_CHAT_ID.
  * cron_routes.py: Disparador de repasos programados protegido por CRON_SECRET.
  * PWA static files: HTML, CSS, JS, Manifest y Service Worker.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.application.use_cases import GlossaryService
from src.bootstrap import build_service
from src.config import settings
from src.interfaces.cron_routes import init_cron_routes
from src.interfaces.web_routes import init_web_routes
from src.interfaces.webhook_routes import init_webhook_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_server")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(
    title="Glosario SRS Cloud Hardened",
    description="Glosario Técnico Inmutable y Entrenador de Repetición Espaciada 100% Cloud (Supabase PostgreSQL + Render + Telegram Webhook + Hardening PIN)",
    version="2.1",
)

_service_instance: Optional[GlossaryService] = None


def get_service() -> GlossaryService:
    global _service_instance
    if _service_instance is None:
        try:
            _service_instance = build_service()
        except Exception as e:
            logger.error("Error inicializando GlossaryService: %s", e)
            raise HTTPException(
                status_code=503,
                detail="Base de datos temporalmente no disponible.",
            )
    return _service_instance


# ---------------------------------------------------------------- Middleware de Seguridad
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path in ("/", "/index.html", "/app.js", "/styles.css", "/sw.js"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# ---------------------------------------------------------------- Routers Modulares
app.include_router(init_web_routes(get_service))
app.include_router(init_webhook_routes(get_service))
app.include_router(init_cron_routes(get_service))


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "cloud": True,
        "database": "PostgreSQL",
        "pin_protection_active": bool(settings.WEB_ACCESS_PIN),
        "telegram_secret_active": bool(settings.TELEGRAM_WEBHOOK_SECRET),
    }


# ---------------------------------------------------------------- Web estática PWA
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)
