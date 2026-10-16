"""Rutas para Cron externo (Serverless / Cloud).

Permite que servicios como cron-job.org, EasyCron, GitHub Actions o Render Cron
disparen los recordatorios de repaso SRS a las 08:30 y 20:00 sin procesos en memoria.

Endpoint:
  POST /api/cron/trigger-srs
  (Protegido por el header CRON_SECRET o X-Cron-Secret)
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from src.application.use_cases import GlossaryService
from src.config import settings
from src.interfaces.webhook_routes import (
    format_card_front,
    get_allowed_chat_id,
    keyboard_front,
    send_message,
)

logger = logging.getLogger("cron_routes")
router = APIRouter(prefix="/api/cron", tags=["Cloud Cron"])


def init_cron_routes(service_factory):
    """Fábrica de rutas que inyecta la instancia de servicio."""

    @router.post("/trigger-srs")
    @router.get("/trigger-srs")
    async def trigger_srs_reminder(
        request: Request,
        cron_secret: Optional[str] = Header(None, alias="CRON_SECRET"),
        x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret"),
        authorization: Optional[str] = Header(None),
    ):
        expected_secret = settings.CRON_SECRET
        if expected_secret:
            token = cron_secret or x_cron_secret
            if not token and authorization and authorization.startswith("Bearer "):
                token = authorization.split("Bearer ", 1)[1].strip()

            if not token or not settings.verify_cron_secret(token):
                raise HTTPException(status_code=403, detail="CRON_SECRET inválido o ausente.")

        chat_id = get_allowed_chat_id()
        if not chat_id:
            raise HTTPException(
                status_code=400,
                detail="TELEGRAM_CHAT_ID no está configurado en el entorno.",
            )

        service: GlossaryService = service_factory()
        today = date.today()
        count = service.count_due(today)

        if count == 0:
            logger.info("Cron trigger: 0 términos pendientes hoy.")
            return {"status": "ok", "due_count": 0, "notified": False}

        logger.info("Cron trigger: enviando recordatorio de %d términos a chat %s", count, chat_id)

        # 1. Mensaje de alerta
        await send_message(
            chat_id=chat_id,
            text=f"⏰ <b>¡Hora de tu Repaso SRS!</b>\n"
                 f"Tienes <b>{count}</b> término(s) listos hoy. Entrenaremos una tanda ligera de hasta <b>5 tarjetas</b>.",
        )

        # 2. Primera tarjeta interactiva (step=1 de 5)
        due_cards = service.due_cards(today=today, limit=1)
        if due_cards:
            card = due_cards[0]
            await send_message(
                chat_id=chat_id,
                text=format_card_front(card, remaining=count, step=1),
                reply_markup=keyboard_front(card.term.id, step=1),
            )

        return {"status": "ok", "due_count": count, "notified": True, "chat_id": chat_id}

    return router
