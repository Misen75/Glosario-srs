"""Rutas de la Web App (FastAPI) con blindaje de seguridad mediante WEB_ACCESS_PIN y Rate Limiting.

Protege todos los endpoints de datos con el header 'X-Web-Pin':
  * GET /api/stats, /api/areas, /api/categories, /api/terms, /api/review/due
  * POST /api/terms, /api/review/{term_id}
Incluye protección anti fuerza bruta por IP (bloqueo tras 5 intentos fallidos).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.application.use_cases import GlossaryService, TermCard
from src.config import settings
from src.domain.models import Category, Rating
from src.infrastructure.ai_enricher import EnrichmentError, InvalidTermError

logger = logging.getLogger("web_routes")
router = APIRouter(prefix="/api", tags=["Web App"])

# ------------------------------------------------------------- Rate Limiter en Memoria
_failed_pin_attempts: dict[str, list[float]] = defaultdict(list)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutos


def _get_client_ip(request: Optional[Request]) -> str:
    if not request:
        return "127.0.0.1"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def check_pin_rate_limit(client_ip: str):
    now = time.time()
    attempts = [t for t in _failed_pin_attempts[client_ip] if now - t < LOCKOUT_SECONDS]
    _failed_pin_attempts[client_ip] = attempts
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        oldest_relevant = attempts[-MAX_FAILED_ATTEMPTS]
        remaining = int(LOCKOUT_SECONDS - (now - oldest_relevant))
        logger.warning("Bloqueo de fuerza bruta activo para IP %s (%d s restantes)", client_ip, remaining)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos fallidos de PIN. Intenta nuevamente en {max(1, remaining)} segundos.",
        )


def record_failed_pin(client_ip: str):
    _failed_pin_attempts[client_ip].append(time.time())


def record_successful_pin(client_ip: str):
    _failed_pin_attempts.pop(client_ip, None)


# ------------------------------------------------------------- Dependencia de Seguridad
def require_web_pin(
    request: Request,
    x_web_pin: Optional[str] = Header(None, alias="X-Web-Pin"),
) -> str:
    """Verifica que el header X-Web-Pin coincida con settings.WEB_ACCESS_PIN, con rate limiting."""
    if settings.WEB_ACCESS_PIN:
        client_ip = _get_client_ip(request)
        check_pin_rate_limit(client_ip)

        if not x_web_pin or not settings.verify_pin(x_web_pin):
            record_failed_pin(client_ip)
            logger.warning("Intento no autorizado: X-Web-Pin inválido o ausente desde IP %s", client_ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="PIN de acceso inválido o no suministrado en el header X-Web-Pin.",
                headers={"WWW-Authenticate": "X-Web-Pin"},
            )
        record_successful_pin(client_ip)
    return x_web_pin or ""


# ------------------------------------------------------------- DTOs
class NewTermIn(BaseModel):
    term: str = Field(min_length=1, max_length=120)
    scope: Optional[str] = Field(None, max_length=60)


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=3)


class VerifyPinIn(BaseModel):
    pin: str


def _cat_dto(c: Category, counts: dict[int, int] | None = None) -> dict:
    d = {
        "id": c.id,
        "macro_area": c.macro_area,
        "name": c.name,
        "description": c.description,
        "simple_explanation": c.simple_explanation,
        "zoom_level": int(c.zoom_level),
        "zoom_label": c.zoom_level.label,
    }
    if counts is not None:
        d["term_count"] = counts.get(c.id, 0)
    return d


def _card_dto(card: TermCard, service: GlossaryService, with_preview: bool = False) -> dict:
    t, s = card.term, card.srs
    d = {
        "id": t.id,
        "term": t.term,
        "category_id": t.category_id,
        "category": card.category.name,
        "macro_area": card.category.macro_area,
        "technical_definition": t.technical_definition,
        "daily_analogy": t.daily_analogy,
        "tradeoff_when_not_to_use": t.tradeoff_when_not_to_use,
        "other_scopes": list(getattr(t, "other_scopes", ()) or ()),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "srs": {
            "interval_days": s.interval_days,
            "ease_factor": s.ease_factor,
            "repetitions": s.repetitions,
            "next_review_date": s.next_review_date.isoformat(),
            "due": s.next_review_date <= date.today(),
        },
    }
    if with_preview:
        d["preview"] = {str(int(r)): days for r, days in service.preview_intervals(s).items()}
    return d


# ------------------------------------------------------------- Rutas de Web App
def init_web_routes(service_factory):
    """Fábrica de rutas que inyecta la instancia del servicio."""

    @router.post("/auth/verify-pin")
    def verify_pin_endpoint(body: VerifyPinIn, request: Request):
        """Permite a la PWA verificar si el PIN guardado en localStorage es válido, con rate limiting."""
        if not settings.WEB_ACCESS_PIN:
            return {"valid": True, "auth_required": False}

        client_ip = _get_client_ip(request)
        check_pin_rate_limit(client_ip)

        valid = settings.verify_pin(body.pin)
        if not valid:
            record_failed_pin(client_ip)
            return {"valid": False, "auth_required": True}

        record_successful_pin(client_ip)
        return {"valid": True, "auth_required": True}

    # =========================================================================
    # ENDPOINTS DE LECTURA (Protegidos con X-Web-Pin)
    # =========================================================================

    @router.get("/stats")
    def get_stats(request: Request, _pin: str = Header(None, alias="X-Web-Pin")):
        require_web_pin(request, _pin)
        service: GlossaryService = service_factory()
        return service.stats()

    @router.get("/areas")
    def get_areas(request: Request, _pin: str = Header(None, alias="X-Web-Pin")):
        require_web_pin(request, _pin)
        service: GlossaryService = service_factory()
        counts = service.repo.count_terms_by_category()
        return [
            {"name": a.name, "categories": [_cat_dto(c, counts) for c in a.categories]}
            for a in service.list_areas()
        ]

    @router.get("/categories")
    def get_categories(request: Request, _pin: str = Header(None, alias="X-Web-Pin")):
        require_web_pin(request, _pin)
        service: GlossaryService = service_factory()
        counts = service.repo.count_terms_by_category()
        return [_cat_dto(c, counts) for c in service.repo.list_categories()]

    @router.get("/terms")
    def get_terms(request: Request, _pin: str = Header(None, alias="X-Web-Pin")):
        require_web_pin(request, _pin)
        service: GlossaryService = service_factory()
        return [_card_dto(c, service) for c in service.list_cards()]

    @router.get("/terms/{term_id}")
    def get_term(term_id: int, request: Request, _pin: str = Header(None, alias="X-Web-Pin")):
        require_web_pin(request, _pin)
        service: GlossaryService = service_factory()
        card = service.get_card(term_id)
        if not card:
            raise HTTPException(404, "Término no encontrado")
        return _card_dto(card, service)

    @router.get("/review/due")
    def review_due(request: Request, limit: int = 5, _pin: str = Header(None, alias="X-Web-Pin")):
        require_web_pin(request, _pin)
        service: GlossaryService = service_factory()
        cards = service.due_cards(limit=limit)
        focus = service.active_focus()
        return {
            "count": service.count_due(),
            "active_focus": focus,
            "cards": [_card_dto(c, service, with_preview=True) for c in cards],
        }

    # =========================================================================
    # ENDPOINTS DE ESCRITURA (Protegidos con X-Web-Pin)
    # =========================================================================

    @router.post("/terms", status_code=201)
    def add_term(
        body: NewTermIn,
        request: Request,
        _pin: str = Header(None, alias="X-Web-Pin"),
    ):
        require_web_pin(request, _pin)

        service: GlossaryService = service_factory()
        try:
            result = service.capture_term(body.term, target_scope=body.scope)
        except InvalidTermError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(503, "Servicio de enriquecimiento temporalmente no disponible.")
        except EnrichmentError as e:
            logger.error("Error en enriquecedor Gemini: %s", e)
            raise HTTPException(502, "Error al comunicarse con el proveedor de IA. Inténtalo más tarde.")

        return {
            "created": result.created,
            "new_category": result.new_category,
            "card": _card_dto(result.card, service),
            "category": _cat_dto(result.card.category),
            "existing_cards": [_card_dto(c, service) for c in getattr(result, "existing_cards", ())],
            "suggested_scopes": getattr(result, "suggested_scopes", ()),
        }

    @router.post("/review/{term_id}")
    def review_term(
        term_id: int,
        body: ReviewIn,
        request: Request,
        _pin: str = Header(None, alias="X-Web-Pin"),
    ):
        require_web_pin(request, _pin)

        service: GlossaryService = service_factory()
        try:
            s = service.review(term_id, Rating(body.rating), datetime.now())
        except KeyError:
            raise HTTPException(404, "Término no encontrado")

        return {
            "term_id": term_id,
            "interval_days": s.interval_days,
            "ease_factor": s.ease_factor,
            "repetitions": s.repetitions,
            "next_review_date": s.next_review_date.isoformat(),
            "due_remaining": service.count_due(),
            "active_focus": service.active_focus(),
        }

    return router
