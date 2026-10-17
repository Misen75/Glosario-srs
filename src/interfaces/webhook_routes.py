"""Webhook de Telegram para Cloud / Serverless (FastAPI).

Reemplaza el polling continuo en memoria por un endpoint HTTP reactivo:
  POST /api/telegram/webhook

* Valida TELEGRAM_CHAT_ID (o ALLOWED_CHAT_ID) para que solo el propietario pueda usarlo.
* Procesa términos en texto plano -> consulta Gemini -> inserta en PostgreSQL.
* Procesa botones inline interactivos (Mostrar Respuesta -> Olvidado / Dudé / Dominado).
* Responde de forma asíncrona mediante llamadas HTTP directas a la API de Telegram.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response
from pydantic import BaseModel

from src.application.use_cases import GlossaryService, TermCard
from src.config import settings
from src.domain.models import Rating
from src.infrastructure.ai_enricher import EnrichmentError, InvalidTermError


logger = logging.getLogger("telegram_webhook")
router = APIRouter(prefix="/api/telegram", tags=["Telegram Webhook"])

TELEGRAM_API_BASE = "https://api.telegram.org"
SESSION_CARD_LIMIT = 5

_DEDUP_CACHE_SIZE = 2000
_processed_update_ids: set[int] = set()
_update_ids_order: deque[int] = deque()
_in_flight_update_ids: set[int] = set()


def is_update_duplicate_or_in_flight(update_id: int) -> bool:
    if update_id in _processed_update_ids or update_id in _in_flight_update_ids:
        return True
    _in_flight_update_ids.add(update_id)
    return False


def mark_update_completed(update_id: int):
    _in_flight_update_ids.discard(update_id)
    if update_id not in _processed_update_ids:
        _processed_update_ids.add(update_id)
        _update_ids_order.append(update_id)
        if len(_update_ids_order) > _DEDUP_CACHE_SIZE:
            oldest = _update_ids_order.popleft()
            _processed_update_ids.discard(oldest)


def clear_update_id_cache():
    _processed_update_ids.clear()
    _update_ids_order.clear()
    _in_flight_update_ids.clear()


def get_bot_token() -> Optional[str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    return token.strip() if token else None


def get_allowed_chat_id() -> Optional[str]:
    cid = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ALLOWED_CHAT_ID")
    return cid.strip() if cid else None


# ------------------------------------------------------------- Cliente HTTP Telegram
async def call_telegram_api(method: str, payload: dict) -> dict:
    token = get_bot_token()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN no configurado. Omitiendo llamada a Telegram (%s).", method)
        return {"ok": False, "description": "Token no configurado"}
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=payload)
            data = res.json()
            if not data.get("ok"):
                logger.error("Error en Telegram API (%s): %s", method, data)
            return data
    except Exception as e:
        logger.error("Error de red conectando con Telegram API (%s): %s", method, e)
        return {"ok": False, "description": str(e)}



async def send_message(
    chat_id: int | str,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> dict:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await call_telegram_api("sendMessage", payload)


async def edit_message_text(
    chat_id: int | str,
    message_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> dict:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await call_telegram_api("editMessageText", payload)


async def answer_callback_query(callback_query_id: str, text: Optional[str] = None) -> dict:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return await call_telegram_api("answerCallbackQuery", payload)


# ------------------------------------------------------------- Formateadores
def escape_html(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def format_card_front(
    card: TermCard,
    remaining: int = 0,
    step: int = 1,
    session_limit: int = SESSION_CARD_LIMIT,
    category_due: int = 0,
    mastery_score: Optional[float] = None,
) -> str:
    header = f"🧠 <b>ENTRENAMIENTO SRS (Tarjeta {step}/{session_limit} de la tanda)</b>"
    if category_due > 0:
        m_txt = f" · Maestría: <b>{mastery_score:.0f}%</b>" if mastery_score is not None else ""
        header += f"\n🎯 <i>Enfoque activo en esta categoría: quedan <b>{category_due}</b> hoy{m_txt}</i>"
    if remaining > 0:
        header += f"\n📚 <i>Total global pendientes hoy: <b>{remaining}</b></i>"
    zoom_badge = f"[Zoom {int(card.category.zoom_level)}: {card.category.zoom_level.label}]"
    return (
        f"{header}\n\n"
        f"🏷️ <b>Categoría:</b> {escape_html(card.category.name)} <i>{zoom_badge}</i>\n"
        f"📍 <b>Área:</b> {escape_html(card.category.macro_area)}\n\n"
        f"❓ <b>Término:</b>\n"
        f"👉 <b><u>{escape_html(card.term.term)}</u></b>\n\n"
        f"<i>Intenta recordar su definición, analogía y trade-off...</i>"
    )


def format_card_back(card: TermCard) -> str:
    tradeoff = card.term.tradeoff_when_not_to_use or "No especificado."
    scopes = getattr(card.term, "other_scopes", ()) or ()
    scopes_block = ""
    if scopes:
        scopes_str = ", ".join(escape_html(s) for s in scopes)
        scopes_block = f"\n\n🌐 <b>Otros ámbitos del término:</b>\n<i>{scopes_str}</i>"
    return (
        f"📖 <b>{escape_html(card.term.term)}</b>\n"
        f"🏷️ <i>{escape_html(card.category.name)} (Zoom {int(card.category.zoom_level)})</i>\n\n"
        f"📋 <b>Definición Técnica:</b>\n{escape_html(card.term.technical_definition)}\n\n"
        f"💡 <b>Analogía Cotidiana:</b>\n{escape_html(card.term.daily_analogy)}\n\n"
        f"⚠️ <b>Cuándo NO usarlo (Trade-off):</b>\n{escape_html(tradeoff)}"
        f"{scopes_block}"
    )


def format_captured_term(result) -> str:
    card = result.card
    tradeoff = card.term.tradeoff_when_not_to_use or "No especificado."
    status_header = (
        "✨ <b>¡NUEVA ACEPCIÓN AÑADIDA AL GLOSARIO!</b>"
        if result.created
        else "ℹ️ <b>EL TÉRMINO YA EXISTE EN ESTE ÁMBITO:</b>"
    )
    if result.new_category:
        status_header += "\n<i>(Se creó una nueva categoría para agrupar este ámbito)</i>"

    other_cards = [c for c in getattr(result, "existing_cards", ()) if c.term.id != card.term.id]
    other_html = ""
    if other_cards:
        items = "\n".join(
            f"  • <b>{escape_html(c.category.name)}</b> ({escape_html(c.category.macro_area)}, Zoom {int(c.category.zoom_level)})"
            for c in other_cards
        )
        other_html = f"\n\n🗂️ <b>Otras interpretaciones ya registradas de «{escape_html(card.term.term)}»:</b>\n{items}"

    scopes_hint = ""
    scopes = getattr(result, "suggested_scopes", ())
    if scopes:
        scopes_list_html = ", ".join(f"<code>{escape_html(s)}</code>" for s in scopes)
        scopes_hint = (
            f"\n\n🌐 <b>Ámbitos alternativos identificados ({len(scopes)}):</b> {scopes_list_html}\n"
            f"🔍 <i>¿Deseas agregar la interpretación de <b>{escape_html(card.term.term)}</b> en otro ámbito? "
            f"Toca un botón abajo o escribe <code>{escape_html(card.term.term)} (Ámbito)</code>:</i>"
        )

    return (
        f"{status_header}\n\n"
        f"📌 <b>{escape_html(card.term.term)}</b>\n"
        f"🏷️ <b>Ámbito / Categoría:</b> {escape_html(card.category.name)} (Zoom {int(card.category.zoom_level)})\n"
        f"📍 <b>Área:</b> {escape_html(card.category.macro_area)}\n\n"
        f"📋 <b>Definición Técnica:</b>\n{escape_html(card.term.technical_definition)}\n\n"
        f"💡 <b>Analogía Cotidiana:</b>\n{escape_html(card.term.daily_analogy)}\n\n"
        f"⚠️ <b>Trade-off / Cuándo NO usarlo:</b>\n{escape_html(tradeoff)}"
        f"{other_html}"
        f"{scopes_hint}"
    )


def keyboard_captured_term(result) -> Optional[dict]:
    scopes = getattr(result, "suggested_scopes", ())
    term_id = result.card.term.id
    if not scopes or not term_id:
        return None
    rows = []
    for s in scopes:
        # Telegram callback_data max 64 bytes -> truncamos el nombre del scope de forma segura en UTF-8
        safe_scope = s.strip().replace(":", " ")
        while len(f"scope:{term_id}:{safe_scope}".encode("utf-8")) > 62:
            safe_scope = safe_scope[:-1]
        rows.append([{"text": f"➕ Ámbito: {s}", "callback_data": f"scope:{term_id}:{safe_scope}"}])
    return {"inline_keyboard": rows}


def keyboard_front(term_id: int, step: int = 1) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "👁️ Mostrar Respuesta", "callback_data": f"rev:{term_id}:{step}"}]
        ]
    }


def keyboard_back(service: GlossaryService, card: TermCard, step: int = 1) -> dict:
    previews = service.preview_intervals(card.srs)
    p1 = previews.get(Rating.FORGOTTEN, 0)
    p2 = previews.get(Rating.DOUBTED, 1)
    p3 = previews.get(Rating.MASTERED, 6)

    lbl_1 = f"❌ Olvidado ({p1}d)"
    lbl_2 = f"🤔 Dudé ({p2}d)"
    lbl_3 = f"✅ Dominado ({p3}d)"

    return {
        "inline_keyboard": [
            [
                {"text": lbl_1, "callback_data": f"rate:{card.term.id}:1:{step}"},
                {"text": lbl_2, "callback_data": f"rate:{card.term.id}:2:{step}"},
                {"text": lbl_3, "callback_data": f"rate:{card.term.id}:3:{step}"},
            ]
        ]
    }


# ------------------------------------------------------------- Lógica de sesión SRS
async def send_next_review_card(service: GlossaryService, chat_id: int | str, step: int = 1):
    due = service.due_cards(limit=1)
    if not due:
        await send_message(
            chat_id=chat_id,
            text="🎉 <b>¡Completaste todos los repasos programados para hoy!</b>\n"
                 "Excelente trabajo reforzando tu arquitectura mental en PostgreSQL.",
        )
        return

    card = due[0]
    total_due = service.count_due()
    cat_due = service.count_due(category_id=card.category.id)
    cat_mastery = service.category_mastery(card.category.id) if hasattr(service, "category_mastery") else None
    m_score = cat_mastery["mastery_score"] if cat_mastery else None
    text = format_card_front(
        card,
        remaining=total_due,
        step=step,
        session_limit=SESSION_CARD_LIMIT,
        category_due=cat_due,
        mastery_score=m_score,
    )
    await send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard_front(card.term.id, step=step),
    )


# ------------------------------------------------------------- Procesador de Updates
async def process_telegram_update(payload: dict, service: GlossaryService):
    """Procesa un update de Telegram de forma desacoplada."""
    allowed_id = get_allowed_chat_id()

    # 1. Callback query (clic en botón inline)
    if "callback_query" in payload:
        cq = payload["callback_query"]
        cq_id = cq["id"]
        user_id = str(cq["from"]["id"])
        if allowed_id and user_id != allowed_id:
            await answer_callback_query(cq_id, "No tienes permiso para interactuar con este bot.")
            return

        data = cq.get("data", "")
        msg = cq.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")

        await answer_callback_query(cq_id)

        if data == "session:start" and chat_id:
            await send_next_review_card(service, chat_id, step=1)

        elif data.startswith("scope:") and chat_id:
            parts = data.split(":", 2)
            if len(parts) == 3:
                term_id = int(parts[1])
                target_scope = parts[2]
                base_card = service.get_card(term_id)
                if base_card:
                    await call_telegram_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
                    try:
                        res = service.capture_term(base_card.term.term, target_scope=target_scope)
                        await send_message(
                            chat_id=chat_id,
                            text=format_captured_term(res),
                            reply_markup=keyboard_captured_term(res),
                        )
                    except Exception as e:
                        await send_message(chat_id=chat_id, text=f"❌ Error generando ámbito: {escape_html(str(e))}")

        elif data.startswith("rev:") and chat_id and message_id:
            parts = data.split(":")
            term_id = int(parts[1])
            step = int(parts[2]) if len(parts) > 2 else 1
            card = service.get_card(term_id)
            if card:
                await edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=format_card_back(card),
                    reply_markup=keyboard_back(service, card, step=step),
                )

        elif data.startswith("rate:") and chat_id and message_id:
            parts = data.split(":")
            term_id = int(parts[1])
            rating = Rating(int(parts[2]))
            step = int(parts[3]) if len(parts) > 3 else 1

            card = service.get_card(term_id)
            prev_cat = card.category if card else None
            new_state = service.review(term_id, rating, datetime.now())

            rating_labels = {
                Rating.FORGOTTEN: "❌ Olvidado (repetir hoy)",
                Rating.DOUBTED: "🤔 Dudé (corto plazo)",
                Rating.MASTERED: "✅ Dominado (espaciado)",
            }
            term_name = card.term.term if card else f"ID {term_id}"

            summary = (
                f"<b>{escape_html(term_name)}</b> ({step}/{SESSION_CARD_LIMIT}) calificado como: <b>{rating_labels[rating]}</b>\n"
                f"📅 Próximo repaso: <b>{new_state.next_review_date.isoformat()}</b> "
                f"(en {new_state.interval_days} días · EF: {new_state.ease_factor:.2f})\n"
            )

            if prev_cat and hasattr(service, "category_mastery"):
                cm = service.category_mastery(prev_cat.id)
                summary += f"📈 Maestría de «{escape_html(prev_cat.name)}»: <b>{cm['mastery_score']:.1f}%</b> ({cm['status_label']})\n"

            next_focus = service.active_focus()
            if prev_cat and next_focus and next_focus["category_id"] != prev_cat.id:
                summary += (
                    f"\n🏆 <b>¡Categoría «{escape_html(prev_cat.name)}» dominada hoy!</b>\n"
                    f"➡️ Cambiando de enfoque a: <b>{escape_html(next_focus['macro_area'])} · {escape_html(next_focus['category'])}</b>\n"
                )

            await edit_message_text(chat_id=chat_id, message_id=message_id, text=summary)

            # Verificar si se alcanzó el tope saludable de la tanda (5 tarjetas)
            remaining = service.count_due()
            if remaining == 0:
                await send_next_review_card(service, chat_id, step=1)
            elif step >= SESSION_CARD_LIMIT:
                await send_message(
                    chat_id=chat_id,
                    text=(
                        f"🎯 <b>¡Tanda de {SESSION_CARD_LIMIT} tarjetas completada!</b>\n"
                        f"Excelente enfoque. Dosificar el estudio evita la fatiga cognitiva.\n"
                        f"📌 Quedan <b>{remaining}</b> tarjeta(s) pendientes para tus próximos turnos.\n\n"
                        f"<i>¿Deseas descansar o entrenar otra tanda de {SESSION_CARD_LIMIT} ahora mismo?</i>"
                    ),
                    reply_markup={
                        "inline_keyboard": [
                            [{"text": f"🔥 Otra tanda de {SESSION_CARD_LIMIT} tarjetas", "callback_data": "session:start"}]
                        ]
                    },
                )
            else:
                await send_next_review_card(service, chat_id, step=step + 1)

        return

    # 2. Mensajes normales
    message = payload.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    sender_id = str(message.get("from", {}).get("id"))

    # Validar usuario autorizado
    if allowed_id and sender_id != allowed_id:
        logger.warning("Mensaje ignorado de usuario no autorizado: %s", sender_id)
        await send_message(
            chat_id=chat_id,
            text=f"⛔ Acceso denegado. Tu chat ID es <code>{sender_id}</code>. Configúralo en ALLOWED_CHAT_ID.",
        )
        return

    raw_text = (message.get("text") or "").strip()
    if not raw_text:
        return

    # Comandos
    if raw_text.startswith("/start") or raw_text.startswith("/help") or raw_text.startswith("/ayuda"):
        msg_text = (
            f"👋 <b>¡Entrenador SRS Multidisciplinar activo en la nube!</b>\n\n"
            f"Tu Chat ID es: <code>{sender_id}</code>\n"
            f"Base de datos: <b>PostgreSQL en la Nube</b>\n\n"
            f"<b>Uso rápido:</b>\n"
            f"• Escribe el nombre de un concepto (ej. <code>REST</code>, <code>Circuit Breaker</code>, <code>Proceso (Derecho Procesal)</code>) y la IA generará la ficha canónica y te sugerirá otros ámbitos donde aplica.\n"
            f"• Usa /repasar para iniciar una tanda dosificada de {SESSION_CARD_LIMIT} tarjetas.\n"
            f"• Usa /stats para ver tus métricas."
        )
        await send_message(chat_id=chat_id, text=msg_text)
        return

    if raw_text.startswith("/stats"):
        st = service.stats()
        gm = st.get("global_mastery") or {}
        focus = st.get("active_focus")
        focus_line = ""
        if focus:
            focus_line = (
                f"\n🎯 <b>Enfoque Activo:</b> {escape_html(focus['macro_area'])} · {escape_html(focus['category'])}\n"
                f"   └ Pendientes hoy: <b>{focus['category_due']}/{focus['category_total']}</b> · "
                f"Maestría: <b>{focus.get('mastery_score', 0.0):.1f}%</b> ({escape_html(focus.get('status_label', ''))})"
            )
        msg_text = (
            f"📊 <b>ESTADÍSTICAS DEL GLOSARIO (PostgreSQL)</b>\n\n"
            f"📚 Términos totales: <b>{st['total_terms']}</b>\n"
            f"🏷️ Categorías: <b>{st['total_categories']}</b>\n"
            f"🧠 Tarjetas pendientes hoy: <b>{st['due_today']}</b> (en tandas de {SESSION_CARD_LIMIT})\n"
            f"✅ Repasadas hoy: <b>{st['reviewed_today']}</b>\n"
            f"📈 Maestría Global: <b>{gm.get('mastery_score', 0.0):.1f}%</b> "
            f"(Maduras ≥21d: <b>{gm.get('mastered_count', 0)}/{gm.get('total_terms', 0)}</b>)\n"
            f"🤖 Enriquecedor IA: <b>{'Activo' if st['ai_available'] else 'Sin API Key'}</b>"
            f"{focus_line}"
        )
        await send_message(chat_id=chat_id, text=msg_text)
        return

    if raw_text.startswith("/repasar") or raw_text.startswith("/entrenar"):
        await send_next_review_card(service, chat_id, step=1)
        return

    # Captura de término con IA
    await call_telegram_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    try:
        result = service.capture_term(raw_text)
        reply = format_captured_term(result)
        await send_message(chat_id=chat_id, text=reply, reply_markup=keyboard_captured_term(result))
    except InvalidTermError as e:
        await send_message(
            chat_id=chat_id,
            text=(
                f"💬 <b>No se guardó en el glosario:</b>\n"
                f"{escape_html(str(e))}\n\n"
                f"<i>💡 Escribe un concepto técnico o académico (ej. <code>Event Sourcing</code>, "
                f"<code>Usucapión (Derecho)</code>) o usa /repasar para entrenar tus tarjetas.</i>"
            ),
        )
    except RuntimeError as e:
        await send_message(chat_id=chat_id, text=f"⚠️ Configuración: {escape_html(str(e))}")
    except EnrichmentError as e:
        await send_message(chat_id=chat_id, text=f"❌ Error con Gemini: {escape_html(str(e))}")
    except Exception as e:
        logger.exception("Error capturando término")
        await send_message(chat_id=chat_id, text=f"❌ Ocurrió un error inesperado: {escape_html(str(e))}")


async def _handle_update_safe(payload: dict, service_factory, update_id: Optional[int]):
    try:
        service: GlossaryService = service_factory()
        await process_telegram_update(payload, service)
    except Exception as e:
        logger.exception("Error no capturado procesando update %s en background: %s", update_id, e)
    finally:
        if update_id is not None:
            mark_update_completed(update_id)


# ------------------------------------------------------------- Endpoint Webhook
def init_webhook_routes(service_factory):
    """Fábrica de rutas que inyecta la instancia del servicio."""

    @router.post("/webhook")
    async def telegram_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_telegram_bot_api_secret_token: Optional[str] = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
    ):
        if settings.TELEGRAM_WEBHOOK_SECRET:
            if not x_telegram_bot_api_secret_token or not settings.verify_telegram_webhook_secret(x_telegram_bot_api_secret_token):
                logger.warning("Rechazado webhook no autenticado: X-Telegram-Bot-Api-Secret-Token inválido o ausente.")
                raise HTTPException(status_code=403, detail="X-Telegram-Bot-Api-Secret-Token inválido o ausente.")

        payload = await request.json()
        update_id = payload.get("update_id")

        if update_id is not None and is_update_duplicate_or_in_flight(update_id):
            logger.info("Telegram update %s ignorado (duplicado o ya en progreso)", update_id)
            return {"ok": True, "status": "duplicate_ignored"}

        # Responder a Telegram inmediatamente (< 5ms) y procesar en segundo plano para evitar timeouts y reintentos
        background_tasks.add_task(_handle_update_safe, payload, service_factory, update_id)
        return {"ok": True}

    return router
