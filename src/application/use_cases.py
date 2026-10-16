"""Casos de uso compartidos por la Web (FastAPI) y el Bot de Telegram.

Orquesta dominio + infraestructura para que ninguna interfaz duplique lógica de negocio.
Depende de abstracciones (Protocol) -> se puede testear con dobles sin red ni BD real.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Protocol, Sequence

from src.domain import srs_scheduler
from src.domain.models import (
    Area, Category, NewTermDraft, Rating, ReviewLog, SRSState, Term,
)
from src.infrastructure.db_repository import (
    PostgresGlossaryRepository as GlossaryRepository,
    TermAlreadyExistsError,
)


class TermEnricher(Protocol):
    available: bool

    def enrich(self, term: str, categories: Sequence[Category],
               examples: Sequence[tuple[Term, Category]] = ()) -> NewTermDraft: ...


@dataclass(frozen=True)
class TermCard:
    """Vista agregada de un término lista para cualquier interfaz."""
    term: Term
    category: Category
    srs: SRSState


@dataclass(frozen=True)
class CaptureResult:
    card: TermCard
    created: bool              # False => ya existía (no se tocó nada)
    new_category: bool = False


class GlossaryService:
    STYLE_EXAMPLES = ("Cohesión", "Strategy", "Bounded Context", "Circuit Breaker", "Idempotencia")

    def __init__(self, repo: GlossaryRepository, enricher: Optional[TermEnricher] = None):
        self.repo = repo
        self.enricher = enricher

    # ------------------------------------------------------------- lectura
    def _card(self, term: Term, cats: Optional[dict[int, Category]] = None) -> TermCard:
        cat = cats[term.category_id] if cats else self.repo.get_category(term.category_id)
        return TermCard(term, cat, self.repo.get_srs_state(term.id))

    def get_card(self, term_id: int) -> Optional[TermCard]:
        t = self.repo.get_term(term_id)
        return self._card(t) if t else None

    def list_cards(self) -> list[TermCard]:
        cats = {c.id: c for c in self.repo.list_categories()}
        states = self.repo.list_srs_states()
        return [TermCard(t, cats[t.category_id], states[t.id]) for t in self.repo.list_terms()]

    def list_areas(self) -> list[Area]:
        """Macro-áreas con sus categorías ordenadas Micro -> Macro."""
        grouped: dict[str, list[Category]] = {}
        for c in self.repo.list_categories():
            grouped.setdefault(c.macro_area, []).append(c)
        return [Area(name, tuple(sorted(cs, key=lambda c: (c.zoom_level, c.id))))
                for name, cs in grouped.items()]

    # ------------------------------------------------------------ captura
    def capture_term(self, raw_name: str) -> CaptureResult:
        """Nombre -> (si no existe) IA -> INSERT. Nunca modifica registros previos."""
        name = " ".join(raw_name.split())
        existing = self.repo.find_term_by_name(name)
        if existing:
            return CaptureResult(self._card(existing), created=False)
        if not self.enricher or not self.enricher.available:
            raise RuntimeError("Autocompletado IA no configurado: define GEMINI_API_KEY en .env")

        categories = self.repo.list_categories()
        cats_by_id = {c.id: c for c in categories}
        examples = [(t, cats_by_id[t.category_id])
                    for n in self.STYLE_EXAMPLES if (t := self.repo.find_term_by_name(n))]
        draft = self.enricher.enrich(name, categories, examples)

        before = {c.id for c in categories}
        cat = self.repo.get_or_create_category(draft.macro_area, draft.category_name,
                                               draft.category_description, draft.zoom_level)
        try:
            term = self.repo.add_term(cat.id, draft)
        except TermAlreadyExistsError as e:  # el nombre canónico de la IA ya existía
            return CaptureResult(self._card(e.term), created=False)
        return CaptureResult(self._card(term), created=True, new_category=cat.id not in before)

    # ---------------------------------------------------------------- SRS
    def due_cards(self, today: Optional[date] = None, limit: Optional[int] = None) -> list[TermCard]:
        today = today or date.today()
        return [c for tid in self.repo.list_due_term_ids(today, limit) if (c := self.get_card(tid))]

    def count_due(self, today: Optional[date] = None) -> int:
        return self.repo.count_due(today or date.today())

    def preview_intervals(self, srs: SRSState, now: Optional[datetime] = None) -> dict[Rating, int]:
        return srs_scheduler.preview(srs, now or datetime.now())

    def review(self, term_id: int, rating: Rating, now: Optional[datetime] = None) -> SRSState:
        now = now or datetime.now()
        state = self.repo.get_srs_state(term_id)
        if state is None:
            raise KeyError(f"Término {term_id} no existe")
        new_state = srs_scheduler.schedule(state, Rating(rating), now)
        self.repo.save_srs_state(new_state)
        self.repo.append_review_log(ReviewLog(
            id=None, term_id=term_id, rating=Rating(rating), reviewed_at=now,
            interval_before=state.interval_days, interval_after=new_state.interval_days,
            ease_factor_after=new_state.ease_factor,
        ))
        return new_state

    def stats(self, today: Optional[date] = None) -> dict:
        today = today or date.today()
        return {
            "total_terms": self.repo.count_terms(),
            "total_categories": len(self.repo.list_categories()),
            "due_today": self.repo.count_due(today),
            "reviewed_today": self.repo.count_reviews_on(today),
            "ai_available": bool(self.enricher and self.enricher.available),
        }
