"""Rutas de la Web App (FastAPI) con blindaje de seguridad mediante WEB_ACCESS_PIN.

Endpoints de escritura protegidos con el header 'X-Web-Pin':
  * POST /api/terms
  * POST /api/review/{term_id}
Si el PIN no coincide con WEB_ACCESS_PIN, responde 401 Unauthorized inmediatamente
sin consumir cuota de Gemini ni realizar escrituras en PostgreSQL.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from src.application.use_cases import GlossaryService, TermCard
from src.config import settings
from src.domain.models import Category, Rating
from src.infrastructure.ai_enricher import EnrichmentError

logger = logging.getLogger("web_routes")
router = APIRouter(prefix="/api", tags=["Web App"])


# ------------------------------------------------------------- Dependencia de Seguridad
def require_web_pin(x_web_pin: Optional[str] = Header(None, alias="X-Web-Pin")) -> str:
    """Verifica que el header X-Web-Pin coincida con settings.WEB_ACCESS_PIN."""
    if settings.WEB_ACCESS_PIN:
        if not x_web_pin or not settings.verify_pin(x_web_pin):
            logger.warning("Intento de escritura denegado: X-Web-Pin inválido o ausente.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="PIN de acceso inválido o no suministrado en el header X-Web-Pin.",
                headers={"WWW-Authenticate": "X-Web-Pin"},
            )
    return x_web_pin or ""


# ------------------------------------------------------------- DTOs
class NewTermIn(BaseModel):
    term: str = Field(min_length=1, max_length=120)


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
    def verify_pin_endpoint(body: VerifyPinIn):
        """Permite a la PWA verificar si el PIN guardado en localStorage es válido."""
        if not settings.WEB_ACCESS_PIN:
            return {"valid": True, "auth_required": False}
        valid = settings.verify_pin(body.pin)
        return {"valid": valid, "auth_required": True}

    @router.get("/stats")
    def get_stats():
        service: GlossaryService = service_factory()
        return service.stats()

    @router.get("/areas")
    def get_areas():
        service: GlossaryService = service_factory()
        counts = service.repo.count_terms_by_category()
        return [
            {"name": a.name, "categories": [_cat_dto(c, counts) for c in a.categories]}
            for a in service.list_areas()
        ]

    @router.get("/categories")
    def get_categories():
        service: GlossaryService = service_factory()
        counts = service.repo.count_terms_by_category()
        return [_cat_dto(c, counts) for c in service.repo.list_categories()]

    @router.get("/terms")
    def get_terms():
        service: GlossaryService = service_factory()
        return [_card_dto(c, service) for c in service.list_cards()]

    @router.get("/terms/{term_id}")
    def get_term(term_id: int):
        service: GlossaryService = service_factory()
        card = service.get_card(term_id)
        if not card:
            raise HTTPException(404, "Término no encontrado")
        return _card_dto(card, service)

    # =========================================================================
    # ENDPOINTS DE ESCRITURA PROTEGIDOS CON X-Web-Pin
    # =========================================================================

    @router.post("/terms", status_code=201)
    def add_term(
        body: NewTermIn,
        _pin: str = Header(None, alias="X-Web-Pin"),
    ):
        # 1. Blindaje de seguridad antes de cualquier llamada pesada o externa
        require_web_pin(_pin)

        service: GlossaryService = service_factory()
        try:
            result = service.capture_term(body.term)
        except RuntimeError as e:
            raise HTTPException(503, str(e))
        except EnrichmentError as e:
            raise HTTPException(502, str(e))

        return {
            "created": result.created,
            "new_category": result.new_category,
            "card": _card_dto(result.card, service),
            "category": _cat_dto(result.card.category),
        }

    @router.post("/review/{term_id}")
    def review_term(
        term_id: int,
        body: ReviewIn,
        _pin: str = Header(None, alias="X-Web-Pin"),
    ):
        # 1. Blindaje de seguridad antes de modificar el estado en base de datos
        require_web_pin(_pin)

        service: GlossaryService = service_factory()
        try:
            s = service.review(term_id, Rating(body.rating), datetime.now())
        except KeyError:
            raise HTTPException(404, "Término no encontrado")

        return {
            "term_id": term_id,
            "interval_days": s.interval_days,
            "ease_factor": s.ease_factor,
            "next_review_date": s.next_review_date.isoformat(),
            "due_remaining": service.count_due(),
        }

    @router.get("/review/due")
    def review_due(limit: int = 50):
        service: GlossaryService = service_factory()
        cards = service.due_cards(limit=limit)
        return {
            "count": service.count_due(),
            "cards": [_card_dto(c, service, with_preview=True) for c in cards],
        }

    return router
