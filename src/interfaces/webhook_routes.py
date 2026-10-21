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

from src.application.use_cases import GlossaryService, QuotaExceededError, TermCard
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


def _format_epistemological_meta(term) -> str:
    canonical_src = (getattr(term, "canonical_source", "") or "").strip()
    parent_t = (getattr(term, "parent_term", "") or "").strip()
    related_t = getattr(term, "related_terms", ()) or ()
    lines = []
    if canonical_src:
        lines.append(f"📚 <b>Fuente Canónica:</b> <i>{escape_html(canonical_src)}</i>")
    if parent_t or related_t:
        rel_parts = []
        if parent_t:
            rel_parts.append(f"↑ Hiperónimo: <b>{escape_html(parent_t)}</b>")
        if related_t:
            rel_parts.append(f"↔ Relacionados: <i>{', '.join(escape_html(r) for r in related_t)}</i>")
        lines.append(f"🌲 <b>Topología Ontológica:</b> {' · '.join(rel_parts)}")
    return ("\n\n" + "\n".join(lines)) if lines else ""


def format_card_back(card: TermCard) -> str:
    tradeoff = card.term.tradeoff_when_not_to_use or "No especificado."
    scopes = getattr(card.term, "other_scopes", ()) or ()
    scopes_block = ""
    if scopes:
        scopes_str = ", ".join(escape_html(s) for s in scopes)
        scopes_block = f"\n\n🌐 <b>Otros ámbitos del término:</b>\n<i>{scopes_str}</i>"
    meta_block = _format_epistemological_meta(card.term)
    return (
        f"📖 <b>{escape_html(card.term.term)}</b>\n"
        f"🏷️ <i>{escape_html(card.category.name)} (Zoom {int(card.category.zoom_level)})</i>\n\n"
        f"📋 <b>Definición Técnica:</b>\n{escape_html(card.term.technical_definition)}\n\n"
        f"💡 <b>Analogía Cotidiana:</b>\n{escape_html(card.term.daily_analogy)}\n\n"
        f"⚠️ <b>Cuándo NO usarlo (Trade-off):</b>\n{escape_html(tradeoff)}"
        f"{meta_block}"
        f"{scopes_block}"
    )


def format_captured_term(result) -> str:
    card = result.card
    tradeoff = card.term.tradeoff_when_not_to_use or "No especificado."
    created_cards = getattr(result, "created_cards", ()) or ()
    existing_cards = getattr(result, "existing_cards", ()) or ()
    from_cache = bool(getattr(result, "from_canonical_cache", False))

    if result.created and len(created_cards) > 1:
        status_header = f"✨ <b>¡TÉRMINO REGISTRADO AUTOMÁTICAMENTE EN {len(created_cards)} ÁMBITOS!</b>"
    elif result.created:
        status_header = "✨ <b>¡NUEVA FICHA CANÓNICA AÑADIDA AL GLOSARIO!</b>"
    elif from_cache:
        status_header = f"⚡ <b>ASIGNADO DESDE EL CATÁLOGO CANÓNICO ($0 IA · {len(existing_cards)} ámbito(s)):</b>"
    else:
        status_header = "ℹ️ <b>EL TÉRMINO YA EXISTE EN ESTE ÁMBITO:</b>"

    if result.new_category:
        status_header += "\n<i>(Se estructuraron automáticamente las categorías correspondientes)</i>"

    other_cards = [c for c in existing_cards if c.term.id != card.term.id]
    other_html = ""
    if other_cards:
        items = []
        for c in other_cards:
            short_def = c.term.technical_definition
            if len(short_def) > 130:
                short_def = short_def[:127].rstrip() + "..."
            items.append(
                f"  • <b>{escape_html(c.category.macro_area)} › {escape_html(c.category.name)}</b> (Zoom {int(c.category.zoom_level)}):\n"
                f"    <i>{escape_html(short_def)}</i>"
            )
        other_html = (
            f"\n\n🗂️ <b>Todas las demás interpretaciones guardadas de «{escape_html(card.term.term)}» ({len(other_cards)}):</b>\n"
            + "\n".join(items)
        )

    meta_block = _format_epistemological_meta(card.term)
    settings_hint = (
        "\n\n⚙️ <i>En el <b>Explorador y Mazo</b> de la Web App puedes seleccionar qué Áreas, Categorías y Términos individuales deseas estudiar.</i>"
    )

    return (
        f"{status_header}\n\n"
        f"📌 <b>{escape_html(card.term.term)}</b>\n"
        f"🏷️ <b>Ámbito Principal:</b> {escape_html(card.category.name)} (Zoom {int(card.category.zoom_level)})\n"
        f"📍 <b>Área:</b> {escape_html(card.category.macro_area)}\n\n"
        f"📋 <b>Definición Técnica:</b>\n{escape_html(card.term.technical_definition)}\n\n"
        f"💡 <b>Analogía Cotidiana:</b>\n{escape_html(card.term.daily_analogy)}\n\n"
        f"⚠️ <b>Trade-off / Cuándo NO usarlo:</b>\n{escape_html(tradeoff)}"
        f"{meta_block}"
        f"{other_html}"
        f"{settings_hint}"
    )


def keyboard_captured_term(result) -> Optional[dict]:
    """Sin botones manuales de ámbitos: el sistema registra todas las interpretaciones automáticamente."""
    return None


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


def resolve_telegram_user_id(service: GlossaryService, sender_id: str) -> Optional[int]:
    """Determina el user_id autorizado para un remitente de Telegram:
    1. Si coincide con ALLOWED_CHAT_ID / TELEGRAM_CHAT_ID (o no hay restricción configurada y es entorno de prueba), retorna user_id = 1 (Admin).
    2. Si existe en la tabla `users` con `is_approved = True`, retorna su `id`.
    3. En cualquier otro caso, retorna None (acceso denegado).
    """
    allowed_id = get_allowed_chat_id()
    if allowed_id and sender_id == allowed_id:
        return 1
    if hasattr(service.repo, "get_user_by_chat_id"):
        u = service.repo.get_user_by_chat_id(sender_id)
        if u and u.get("is_approved"):
            return int(u["id"])
    if not allowed_id:
        return 1
    return None


# ------------------------------------------------------------- Lógica de sesión SRS
async def send_next_review_card(service: GlossaryService, chat_id: int | str, step: int = 1, user_id: int = 1):
    try:
        due = service.due_cards(limit=1, user_id=user_id)
    except TypeError:
        due = service.due_cards(limit=1)
    if not due:
        await send_message(
            chat_id=chat_id,
            text="🎉 <b>¡Completaste todos los repasos programados para hoy!</b>\n"
                 "Excelente trabajo reforzando tu arquitectura mental en PostgreSQL.",
        )
        return

    try:
        cfg = service.get_settings(user_id=user_id) if hasattr(service, "get_settings") else {}
    except TypeError:
        cfg = service.get_settings() if hasattr(service, "get_settings") else {}
    session_limit = int(cfg.get("session_card_limit", SESSION_CARD_LIMIT))
    focused_mode = bool(cfg.get("focused_mode", True))

    card = due[0]
    try:
        total_due = service.count_due(user_id=user_id)
        cat_due = service.count_due(category_id=card.category.id, user_id=user_id) if focused_mode else 0
        cat_mastery = service.category_mastery(card.category.id, user_id=user_id) if (focused_mode and hasattr(service, "category_mastery")) else None
    except TypeError:
        total_due = service.count_due()
        cat_due = service.count_due(category_id=card.category.id) if focused_mode else 0
        cat_mastery = service.category_mastery(card.category.id) if (focused_mode and hasattr(service, "category_mastery")) else None

    m_score = cat_mastery["mastery_score"] if cat_mastery else None
    text = format_card_front(
        card,
        remaining=total_due,
        step=step,
        session_limit=session_limit,
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
    # 1. Callback query (clic en botón inline)
    if "callback_query" in payload:
        cq = payload["callback_query"]
        cq_id = cq["id"]
        sender_id = str(cq["from"]["id"])
        db_user_id = resolve_telegram_user_id(service, sender_id)
        if db_user_id is None:
            await answer_callback_query(cq_id, "No tienes permiso para interactuar con este bot.")
            return

        try:
            cfg = service.get_settings(user_id=db_user_id) if hasattr(service, "get_settings") else {}
        except TypeError:
            cfg = service.get_settings() if hasattr(service, "get_settings") else {}
        session_limit = int(cfg.get("session_card_limit", SESSION_CARD_LIMIT))

        data = cq.get("data", "")
        msg = cq.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")

        await answer_callback_query(cq_id)

        if data == "session:start" and chat_id:
            await send_next_review_card(service, chat_id, step=1, user_id=db_user_id)

        elif data.startswith("rev:") and chat_id and message_id:
            parts = data.split(":")
            term_id = int(parts[1])
            step = int(parts[2]) if len(parts) > 2 else 1
            try:
                card = service.get_card(term_id, user_id=db_user_id)
            except TypeError:
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

            try:
                card = service.get_card(term_id, user_id=db_user_id)
            except TypeError:
                card = service.get_card(term_id)
            prev_cat = card.category if card else None
            try:
                new_state = service.review(term_id, rating, datetime.now(), user_id=db_user_id)
            except TypeError:
                new_state = service.review(term_id, rating, datetime.now())

            rating_labels = {
                Rating.FORGOTTEN: "❌ Olvidado (repetir hoy)",
                Rating.DOUBTED: "🤔 Dudé (corto plazo)",
                Rating.MASTERED: "✅ Dominado (espaciado)",
            }
            term_name = card.term.term if card else f"ID {term_id}"

            summary = (
                f"<b>{escape_html(term_name)}</b> ({step}/{session_limit}) calificado como: <b>{rating_labels[rating]}</b>\n"
                f"📅 Próximo repaso: <b>{new_state.next_review_date.isoformat()}</b> "
                f"(en {new_state.interval_days} días · EF: {new_state.ease_factor:.2f})\n"
            )

            if prev_cat and hasattr(service, "category_mastery"):
                try:
                    cm = service.category_mastery(prev_cat.id, user_id=db_user_id)
                except TypeError:
                    cm = service.category_mastery(prev_cat.id)
                summary += f"📈 Maestría de «{escape_html(prev_cat.name)}»: <b>{cm['mastery_score']:.1f}%</b> ({cm['status_label']})\n"

            try:
                next_focus = service.active_focus(user_id=db_user_id)
            except TypeError:
                next_focus = service.active_focus()
            category_switched = bool(prev_cat and next_focus and next_focus["category_id"] != prev_cat.id)
            if category_switched:
                summary += (
                    f"\n🏆 <b>¡Categoría «{escape_html(prev_cat.name)}» despejada hoy!</b>\n"
                    f"➡️ Siguiente enfoque: <b>{escape_html(next_focus['macro_area'])} · {escape_html(next_focus['category'])}</b>\n"
                )

            await edit_message_text(chat_id=chat_id, message_id=message_id, text=summary)

            # Verificar siguiente tarjeta y evitar repetir el mismo término en la misma tanda
            try:
                remaining = service.count_due(user_id=db_user_id)
                next_due_cards = service.due_cards(limit=1, user_id=db_user_id)
            except TypeError:
                remaining = service.count_due()
                next_due_cards = service.due_cards(limit=1)

            next_card = next_due_cards[0] if next_due_cards else None
            same_term_consecutive = bool(
                next_card
                and card
                and (
                    next_card.term.id == card.term.id
                    or next_card.term.term.strip().casefold() == card.term.term.strip().casefold()
                )
            )

            if remaining == 0 or not next_card:
                await send_next_review_card(service, chat_id, step=1, user_id=db_user_id)
            elif step >= session_limit or same_term_consecutive:
                reason_txt = (
                    f"🎯 <b>¡Tanda de {step} tarjeta(s) completada!</b>\n"
                    f"Se pausó la tanda para no repetir inmediatamente el mismo término («{escape_html(term_name)}»).\n"
                    if same_term_consecutive and step < session_limit
                    else f"🎯 <b>¡Tanda de {session_limit} tarjetas completada!</b>\n"
                         f"Excelente enfoque. Dosificar el estudio evita la fatiga cognitiva.\n"
                )
                await send_message(
                    chat_id=chat_id,
                    text=(
                        f"{reason_txt}"
                        f"📌 Quedan <b>{remaining}</b> tarjeta(s) pendientes para tus próximos turnos.\n\n"
                        f"<i>¿Deseas descansar o entrenar otra tanda ahora mismo?</i>"
                    ),
                    reply_markup={
                        "inline_keyboard": [
                            [{"text": "🔥 Iniciar siguiente tanda", "callback_data": "session:start"}]
                        ]
                    },
                )
            else:
                await send_next_review_card(service, chat_id, step=step + 1, user_id=db_user_id)

        return

    # 2. Mensajes normales
    message = payload.get("message")
    if not message:
        return

    chat_id = message["chat"]["id"]
    sender_id = str(message.get("from", {}).get("id"))
    db_user_id = resolve_telegram_user_id(service, sender_id)

    # Validar usuario autorizado
    if db_user_id is None:
        logger.warning("Mensaje ignorado de usuario no autorizado: %s", sender_id)
        await send_message(
            chat_id=chat_id,
            text=(
                f"⛔ <b>Acceso restringido.</b>\n"
                f"Tu Telegram Chat ID es: <code>{sender_id}</code>\n\n"
                f"Solicita al Administrador que apruebe tu Chat ID en <b>⚙️ Ajustes → Gestión de Usuarios</b>."
            ),
        )
        return

    try:
        cfg = service.get_settings(user_id=db_user_id) if hasattr(service, "get_settings") else {}
    except TypeError:
        cfg = service.get_settings() if hasattr(service, "get_settings") else {}
    session_limit = int(cfg.get("session_card_limit", SESSION_CARD_LIMIT))

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
            f"• Escribe el nombre de un concepto (ej. <code>REST</code>, <code>Circuit Breaker</code>, <code>Contraprestativo</code>) y el sistema registrará automáticamente todas sus interpretaciones canónicas.\n"
            f"• En <b>⚙️ Ajustes</b> de la Web App puedes elegir qué Áreas y Categorías estudiar.\n"
            f"• Usa /repasar para iniciar una tanda dosificada de {session_limit} tarjetas.\n"
            f"• Usa /stats para ver tus métricas."
        )
        await send_message(chat_id=chat_id, text=msg_text)
        return

    if raw_text.startswith("/stats"):
        try:
            st = service.stats(user_id=db_user_id)
        except TypeError:
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
            f"🧠 Tarjetas pendientes hoy: <b>{st['due_today']}</b> (en tandas de {session_limit})\n"
            f"✅ Repasadas hoy: <b>{st['reviewed_today']}</b>\n"
            f"📈 Maestría Global: <b>{gm.get('mastery_score', 0.0):.1f}%</b> "
            f"(Maduras ≥21d: <b>{gm.get('mastered_count', 0)}/{gm.get('total_terms', 0)}</b>)\n"
            f"🤖 Enriquecedor IA: <b>{'Activo' if st['ai_available'] else 'Sin API Key'}</b>"
            f"{focus_line}"
        )
        await send_message(chat_id=chat_id, text=msg_text)
        return

    if raw_text.startswith("/repasar") or raw_text.startswith("/entrenar"):
        await send_next_review_card(service, chat_id, step=1, user_id=db_user_id)
        return

    # Captura automática de todas las interpretaciones con IA (o Caché Canónico $0)
    await call_telegram_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    try:
        try:
            result = service.capture_term(raw_text, user_id=db_user_id)
        except TypeError:
            result = service.capture_term(raw_text)
        reply = format_captured_term(result)
        await send_message(chat_id=chat_id, text=reply, reply_markup=keyboard_captured_term(result))
    except QuotaExceededError as e:
        await send_message(
            chat_id=chat_id,
            text=f"⏳ <b>Cuota diaria de IA alcanzada ({e.used}/{e.limit}):</b>\n{escape_html(str(e))}",
        )
    except InvalidTermError as e:
        await send_message(
            chat_id=chat_id,
            text=(
                f"💬 <b>No se guardó en el glosario:</b>\n"
                f"{escape_html(str(e))}\n\n"
                f"<i>💡 Escribe un concepto técnico o académico (ej. <code>Event Sourcing</code>, "
                f"<code>Usucapión</code>) o usa /repasar para entrenar tus tarjetas.</i>"
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
