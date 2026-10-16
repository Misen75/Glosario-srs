"""Configuración centralizada y tipada del sistema Glosario SRS (Cloud 24/7).

Carga variables de entorno desde .env o el entorno de ejecución (Render, Supabase).
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    # Base de datos
    DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")

    # IA (Google Gemini)
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ALLOWED_CHAT_ID")
    TELEGRAM_WEBHOOK_SECRET: Optional[str] = os.getenv("TELEGRAM_WEBHOOK_SECRET")

    # Cron HTTP
    CRON_SECRET: Optional[str] = os.getenv("CRON_SECRET")

    # Blindaje Web App
    WEB_ACCESS_PIN: Optional[str] = os.getenv("WEB_ACCESS_PIN")

    # Servidor
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))

    def verify_pin(self, pin_candidate: Optional[str]) -> bool:
        """Verifica el PIN contra WEB_ACCESS_PIN en tiempo constante (previene timing attacks)."""
        pin = self.WEB_ACCESS_PIN
        if not pin:
            return True
        if not pin_candidate:
            return False
        return secrets.compare_digest(str(pin_candidate).strip(), str(pin).strip())

    def verify_telegram_webhook_secret(self, token_candidate: Optional[str]) -> bool:
        """Verifica el header X-Telegram-Bot-Api-Secret-Token en tiempo constante."""
        secret = self.TELEGRAM_WEBHOOK_SECRET
        if not secret:
            return True
        if not token_candidate:
            return False
        return secrets.compare_digest(str(token_candidate).strip(), str(secret).strip())



settings = Settings()
