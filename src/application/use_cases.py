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

    def enrich(
        self,
        term: str,
        categories: Sequence[Category],
        examples: Sequence[tuple[Term, Category]] = (),
        target_scope: Optional[str] = None,
        exclude_categories: Sequence[str] = (),
    ) -> NewTermDraft: ...


@dataclass(frozen=True)
class TermCard:
    """Vista agregada de un término lista para cualquier interfaz."""
    term: Term
    category: Category
    srs: SRSState


@dataclass(frozen=True)
class CaptureResult:
    card: TermCard
    created: bool              # False => ya existía en ese ámbito (no se tocó nada)
    new_category: bool = False
    existing_cards: tuple[TermCard, ...] = ()
    suggested_scopes: tuple[str, ...] = ()


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

    def _find_all_by_name(self, name: str) -> list[Term]:
        if hasattr(self.repo, "find_terms_by_name"):
            return self.repo.find_terms_by_name(name)
        single = self.repo.find_term_by_name(name)
        return [single] if single else []

    @staticmethod
    def _parse_term_and_scope(raw_name: str, explicit_scope: Optional[str] = None) -> tuple[str, Optional[str]]:
        import re
        cleaned = " ".join(raw_name.split())
        if explicit_scope:
            return cleaned, explicit_scope.strip()
        # Soporta formato "Término (Ámbito)" o "Ámbito: Término"
        m_paren = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", cleaned)
        if m_paren:
            return m_paren.group(1).strip(), m_paren.group(2).strip()
        if ":" in cleaned:
            parts = cleaned.split(":", 1)
            if len(parts[0].strip()) >= 3 and len(parts[1].strip()) >= 2:
                return parts[1].strip(), parts[0].strip()
        return cleaned, None

    # ------------------------------------------------------------ captura
    def capture_term(self, raw_name: str, target_scope: Optional[str] = None) -> CaptureResult:
        """Nombre (+ ámbito opcional) -> IA -> INSERT en su categoría correspondiente (1:N por ámbito)."""
        base_name, scope = self._parse_term_and_scope(raw_name, target_scope)
        existing_terms = self._find_all_by_name(base_name)

        # Si el usuario no pidió un nuevo ámbito específico y ya existen acepciones en la BD:
        if existing_terms and not scope:
            cards = tuple(self._card(t) for t in existing_terms)
            occupied_cats = {c.category.name.casefold() for c in cards}
            persisted_scopes = [s for t in existing_terms for s in (t.other_scopes or ())]
            candidate_scopes = persisted_scopes or [
                "Programación e Implementación",
                "Diseño de Software",
                "Arquitectura de Sistemas",
                "Bases de Datos y Persistencia",
            ]
            available_scopes = tuple(
                dict.fromkeys(s for s in candidate_scopes if s.casefold() not in occupied_cats)
            )
            return CaptureResult(
                card=cards[0],
                created=False,
                existing_cards=cards,
                suggested_scopes=available_scopes,
            )

        if not self.enricher or not self.enricher.available:
            raise RuntimeError("Autocompletado IA no configurado: define GEMINI_API_KEY en .env")

        categories = self.repo.list_categories()
        cats_by_id = {c.id: c for c in categories}
        examples = [
            (t, cats_by_id[t.category_id])
            for n in self.STYLE_EXAMPLES
            if (t := self.repo.find_term_by_name(n))
        ]
        exclude_cats = [cats_by_id[t.category_id].name for t in existing_terms if t.category_id in cats_by_id]

        try:
            draft = self.enricher.enrich(
                base_name,
                categories,
                examples,
                target_scope=scope,
                exclude_categories=exclude_cats,
            )
        except TypeError:
            # Retrocompatibilidad con dobles de prueba que reciben solo 3 argumentos
            draft = self.enricher.enrich(base_name, categories, examples)

        # Verificar también acepciones previas bajo el nombre canónico normalizado por la IA
        canonical_existing = self._find_all_by_name(draft.term)
        all_prev = {t.id: t for t in (*existing_terms, *canonical_existing) if t.id is not None}

        before = {c.id for c in categories}
        cat = self.repo.get_or_create_category(
            draft.macro_area, draft.category_name, draft.category_description, draft.zoom_level
        )
        cfg = self.get_settings()
        try:
            try:
                term = self.repo.add_term(cat.id, draft, initial_ease_factor=cfg.get("initial_ease_factor", 2.5))
            except TypeError:
                term = self.repo.add_term(cat.id, draft)
        except TermAlreadyExistsError as e:
            all_cards = tuple(self._card(t) for t in self._find_all_by_name(draft.term)) or (self._card(e.term),)
            return CaptureResult(
                card=self._card(e.term),
                created=False,
                existing_cards=all_cards,
                suggested_scopes=draft.other_scopes,
            )

        all_now_terms = self._find_all_by_name(term.term) or [term]
        all_now_cards = tuple(self._card(t) for t in all_now_terms)
        occupied_names = {c.category.name.casefold() for c in all_now_cards}
        filtered_scopes = tuple(
            s for s in draft.other_scopes if s.casefold() not in occupied_names
        )

        return CaptureResult(
            card=self._card(term),
            created=True,
            new_category=cat.id not in before,
            existing_cards=all_now_cards,
            suggested_scopes=filtered_scopes,
        )

    # ---------------------------------------------------- Administración y Configuración
    def get_settings(self) -> dict:
        if hasattr(self.repo, "get_settings"):
            return self.repo.get_settings()
        return {
            "session_card_limit": 5,
            "maturity_threshold_days": 21,
            "initial_ease_factor": 2.5,
            "focused_mode": True,
            "theme": "light",
            "step_by_step_reveal": False,
        }

    def update_settings(self, **kwargs) -> dict:
        if hasattr(self.repo, "update_settings"):
            return self.repo.update_settings(**kwargs)
        return self.get_settings()

    def reenrich_term(self, term_id: int, target_scope: Optional[str] = None) -> TermCard:
        """Reenvía un registro existente a la IA para regenerar su ficha con rigor canónico."""
        card = self.get_card(term_id)
        if not card:
            raise KeyError(f"Término {term_id} no existe")
        if not self.enricher or not self.enricher.available:
            raise RuntimeError("Autocompletado IA no configurado: define GEMINI_API_KEY en .env")

        categories = self.repo.list_categories()
        cats_by_id = {c.id: c for c in categories}
        examples = [
            (t, cats_by_id[t.category_id])
            for n in self.STYLE_EXAMPLES
            if (t := self.repo.find_term_by_name(n))
        ]
        effective_scope = (target_scope or "").strip() or card.category.name
        try:
            draft = self.enricher.enrich(
                card.term.term,
                categories,
                examples,
                target_scope=effective_scope,
            )
        except TypeError:
            draft = self.enricher.enrich(card.term.term, categories, examples)

        cat = self.repo.get_or_create_category(
            draft.macro_area, draft.category_name, draft.category_description, draft.zoom_level
        )
        # Evitar colisión UNIQUE (category_id, term) si ya existe otra acepción distinta en esa categoría
        collision = self.repo.find_term_by_name(draft.term, category_id=cat.id)
        target_cat_id = card.category.id if (collision and collision.id != term_id) else cat.id

        updated = self.repo.update_term_from_draft(term_id, target_cat_id, draft)
        if not updated:
            raise KeyError(f"No se pudo actualizar el término {term_id}")
        return self.get_card(term_id)

    def delete_term(self, term_id: int) -> bool:
        """Elimina un registro y su historial SRS asociado."""
        if not hasattr(self.repo, "delete_term"):
            raise RuntimeError("El repositorio no soporta eliminación administrativa.")
        return self.repo.delete_term(term_id)

    # ---------------------------------------------------------------- SRS
    def category_mastery(self, category_id: int, today: Optional[date] = None) -> dict:
        today = today or date.today()
        threshold = int(self.get_settings().get("maturity_threshold_days", 21))
        terms = self.repo.list_terms(category_id=category_id)
        states_map = self.repo.list_srs_states()
        states = [states_map[t.id] for t in terms if t.id in states_map]
        return srs_scheduler.compute_mastery_metrics(states, today, maturity_threshold_days=threshold)

    def mastery_by_category(self, today: Optional[date] = None) -> dict[int, dict]:
        today = today or date.today()
        threshold = int(self.get_settings().get("maturity_threshold_days", 21))
        cats = self.repo.list_categories()
        terms = self.repo.list_terms()
        states_map = self.repo.list_srs_states()
        by_cat: dict[int, list[SRSState]] = {c.id: [] for c in cats}
        for t in terms:
            if t.id in states_map:
                by_cat.setdefault(t.category_id, []).append(states_map[t.id])
        return {
            cid: srs_scheduler.compute_mastery_metrics(st_list, today, maturity_threshold_days=threshold)
            for cid, st_list in by_cat.items()
        }

    def global_mastery(self, today: Optional[date] = None) -> dict:
        today = today or date.today()
        threshold = int(self.get_settings().get("maturity_threshold_days", 21))
        states = list(self.repo.list_srs_states().values())
        return srs_scheduler.compute_mastery_metrics(states, today, maturity_threshold_days=threshold)

    def active_focus(self, today: Optional[date] = None) -> Optional[dict]:
        """Devuelve el Área y Categoría específica en la que se concentra el estudio actual hasta dominarla."""
        today = today or date.today()
        cfg = self.get_settings()
        if not cfg.get("focused_mode", True):
            return None
        cat_id = None
        if hasattr(self.repo, "get_active_focus_category_id"):
            cat_id = self.repo.get_active_focus_category_id(today)
        else:
            due_ids = self.repo.list_due_term_ids(today, limit=1)
            if due_ids:
                t = self.repo.get_term(due_ids[0])
                cat_id = t.category_id if t else None
        if cat_id is None:
            return None
        cat = self.repo.get_category(cat_id)
        if not cat:
            return None
        cat_due = self.count_due(today, category_id=cat_id)
        cat_mastery = self.category_mastery(cat_id, today)
        return {
            "category_id": cat.id,
            "category": cat.name,
            "macro_area": cat.macro_area,
            "zoom_level": int(cat.zoom_level),
            "zoom_label": cat.zoom_level.label,
            "category_due": cat_due,
            "category_total": cat_mastery["total_terms"],
            "mastery_score": cat_mastery["mastery_score"],
            "status_label": cat_mastery["status_label"],
            "is_canonically_mastered": cat_mastery["is_canonically_mastered"],
            "mastery": cat_mastery,
        }

    def due_cards(
        self,
        today: Optional[date] = None,
        limit: Optional[int] = None,
        focused: Optional[bool] = None,
    ) -> list[TermCard]:
        today = today or date.today()
        cfg = self.get_settings()
        use_focused = cfg.get("focused_mode", True) if focused is None else focused
        use_limit = cfg.get("session_card_limit", 5) if limit is None else limit
        try:
            due_ids = self.repo.list_due_term_ids(today, limit=use_limit, focused=use_focused)
        except TypeError:
            due_ids = self.repo.list_due_term_ids(today, limit=use_limit)
        return [c for tid in due_ids if (c := self.get_card(tid))]

    def count_due(self, today: Optional[date] = None, category_id: Optional[int] = None) -> int:
        today = today or date.today()
        try:
            return self.repo.count_due(today, category_id=category_id)
        except TypeError:
            return self.repo.count_due(today)

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
        g_mastery = self.global_mastery(today)
        return {
            "total_terms": self.repo.count_terms(),
            "total_categories": len(self.repo.list_categories()),
            "due_today": self.repo.count_due(today),
            "reviewed_today": self.repo.count_reviews_on(today),
            "ai_available": bool(self.enricher and self.enricher.available),
            "active_focus": self.active_focus(today),
            "global_mastery": g_mastery,
            "settings": self.get_settings(),
        }
