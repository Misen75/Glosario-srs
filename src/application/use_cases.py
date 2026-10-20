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


class QuotaExceededError(Exception):
    """Se lanza cuando un usuario no administrador alcanza su límite diario de generaciones IA."""
    def __init__(self, used: int, limit: int):
        super().__init__(
            f"Has alcanzado tu cuota diaria de generación con IA ({used}/{limit} términos nuevos hoy). "
            f"Puedes seguir repasando y consultando todos los términos del catálogo global sin límite."
        )
        self.used = used
        self.limit = limit


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
    created: bool              # False => ya existía en el catálogo (servido desde caché canónico $0 IA)
    new_category: bool = False
    existing_cards: tuple[TermCard, ...] = ()
    suggested_scopes: tuple[str, ...] = ()
    created_cards: tuple[TermCard, ...] = ()
    from_canonical_cache: bool = False


class GlossaryService:
    STYLE_EXAMPLES = ("Cohesión", "Strategy", "Bounded Context", "Circuit Breaker", "Idempotencia")

    def __init__(self, repo: GlossaryRepository, enricher: Optional[TermEnricher] = None):
        self.repo = repo
        self.enricher = enricher

    # ------------------------------------------------------------- lectura
    def _card(self, term: Term, cats: Optional[dict[int, Category]] = None, user_id: int = 1) -> TermCard:
        cat = cats[term.category_id] if cats else self.repo.get_category(term.category_id)
        try:
            srs = self.repo.get_srs_state(term.id, user_id=user_id)
        except TypeError:
            srs = self.repo.get_srs_state(term.id)
        if srs is None:
            srs = SRSState(term_id=term.id)
        return TermCard(term, cat, srs)

    def get_card(self, term_id: int, user_id: int = 1) -> Optional[TermCard]:
        t = self.repo.get_term(term_id)
        return self._card(t, user_id=user_id) if t else None

    def list_cards(self, user_id: int = 1) -> list[TermCard]:
        cats = {c.id: c for c in self.repo.list_categories()}
        try:
            states = self.repo.list_srs_states(user_id=user_id)
        except TypeError:
            states = self.repo.list_srs_states()
        return [
            TermCard(t, cats[t.category_id], states.get(t.id, SRSState(term_id=t.id)))
            for t in self.repo.list_terms()
            if (int(user_id) == 1 or t.id in states)
        ]

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
    def capture_term(
        self,
        raw_name: str,
        target_scope: Optional[str] = None,
        user_id: int = 1,
    ) -> CaptureResult:
        """Captura Total Automática + Caché Canónico ($0 IA):
        1. Si el término ya existe en el Catálogo Canónico Global y no se pide un nuevo ámbito forzado,
           asigna todas sus interpretaciones al usuario sin consumir llamadas a la IA ($0 costo).
        2. Si es un término nuevo, verifica cuota diaria IA del usuario y llama a `enrich_all` para generar
           y persistir en lote TODAS las interpretaciones/ámbitos válidos sin requerir botones manuales.
        """
        base_name, scope = self._parse_term_and_scope(raw_name, target_scope)
        existing_terms = self._find_all_by_name(base_name)
        cfg = self.get_settings(user_id=user_id)
        initial_ef = float(cfg.get("initial_ease_factor", 2.5))

        # 1. Caché Canónico ($0 IA): si ya existen acepciones en la BD y no se forzó un nuevo ámbito específico
        if existing_terms and not scope:
            if hasattr(self.repo, "ensure_user_terms"):
                self.repo.ensure_user_terms(
                    user_id=user_id,
                    term_ids=[t.id for t in existing_terms if t.id is not None],
                    initial_ease_factor=initial_ef,
                )
            cards = tuple(self._card(t, user_id=user_id) for t in existing_terms)
            return CaptureResult(
                card=cards[0],
                created=False,
                existing_cards=cards,
                suggested_scopes=(),
                created_cards=(),
                from_canonical_cache=True,
            )

        if not self.enricher or not self.enricher.available:
            raise RuntimeError("Autocompletado IA no configurado: define GEMINI_API_KEY en .env")

        # 2. Control de cuota diaria IA por usuario
        if hasattr(self.repo, "check_and_consume_ai_quota"):
            allowed, used, limit = self.repo.check_and_consume_ai_quota(user_id=user_id)
            if not allowed:
                raise QuotaExceededError(used, limit)

        categories = self.repo.list_categories()
        cats_by_id = {c.id: c for c in categories}
        examples = [
            (t, cats_by_id[t.category_id])
            for n in self.STYLE_EXAMPLES
            if (t := self.repo.find_term_by_name(n))
        ]
        exclude_cats = [cats_by_id[t.category_id].name for t in existing_terms if t.category_id in cats_by_id]

        # 3. Generación en lote de todas las interpretaciones posibles (enrich_all)
        drafts: list[NewTermDraft] = []
        if hasattr(self.enricher, "enrich_all") and not scope:
            drafts = list(
                self.enricher.enrich_all(
                    base_name,
                    categories,
                    examples,
                    target_scope=scope,
                    exclude_categories=exclude_cats,
                )
            )
        else:
            try:
                single_draft = self.enricher.enrich(
                    base_name,
                    categories,
                    examples,
                    target_scope=scope,
                    exclude_categories=exclude_cats,
                )
            except TypeError:
                single_draft = self.enricher.enrich(base_name, categories, examples)
            drafts = [single_draft]

        before_cat_ids = {c.id for c in categories}
        created_terms: list[Term] = []
        fallback_term: Optional[Term] = None
        any_new_category = False

        for draft in drafts:
            cat = self.repo.get_or_create_category(
                draft.macro_area, draft.category_name, draft.category_description, draft.zoom_level
            )
            if cat.id not in before_cat_ids:
                any_new_category = True
            try:
                try:
                    t_new = self.repo.add_term(
                        cat.id, draft, initial_ease_factor=initial_ef, user_id=user_id
                    )
                except TypeError:
                    try:
                        t_new = self.repo.add_term(cat.id, draft, initial_ease_factor=initial_ef)
                    except TypeError:
                        t_new = self.repo.add_term(cat.id, draft)
                created_terms.append(t_new)
            except TermAlreadyExistsError as e:
                if fallback_term is None:
                    fallback_term = e.term
                if hasattr(self.repo, "ensure_user_terms") and e.term.id is not None:
                    self.repo.ensure_user_terms(
                        user_id=user_id, term_ids=[e.term.id], initial_ease_factor=initial_ef
                    )

        primary_term = created_terms[0] if created_terms else (fallback_term or existing_terms[0])
        all_now_terms = self._find_all_by_name(primary_term.term) or (created_terms or [primary_term])
        all_now_cards = tuple(self._card(t, user_id=user_id) for t in all_now_terms)
        created_cards = tuple(self._card(t, user_id=user_id) for t in created_terms)

        return CaptureResult(
            card=self._card(primary_term, user_id=user_id),
            created=len(created_terms) > 0,
            new_category=any_new_category,
            existing_cards=all_now_cards,
            suggested_scopes=(),
            created_cards=created_cards,
            from_canonical_cache=False,
        )

    # ---------------------------------------------------- Administración y Configuración
    def get_settings(self, user_id: int = 1) -> dict:
        if hasattr(self.repo, "get_settings"):
            try:
                cfg = self.repo.get_settings(user_id=user_id)
            except TypeError:
                cfg = self.repo.get_settings()
            cfg.setdefault("active_category_ids", [])
            return cfg
        return {
            "session_card_limit": 5,
            "maturity_threshold_days": 21,
            "initial_ease_factor": 2.5,
            "focused_mode": True,
            "theme": "light",
            "step_by_step_reveal": False,
            "active_category_ids": [],
        }

    def update_settings(self, user_id: int = 1, **kwargs) -> dict:
        if hasattr(self.repo, "update_settings"):
            try:
                return self.repo.update_settings(user_id=user_id, **kwargs)
            except TypeError:
                return self.repo.update_settings(**kwargs)
        return self.get_settings(user_id=user_id)

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

    def delete_term(self, term_id: int, user_id: int = 1, is_admin: bool = True) -> bool:
        """Si es Admin, elimina el registro del catálogo global. Si es Usuario regular, solo lo desvincula de su mazo."""
        if not is_admin and hasattr(self.repo, "unlink_user_term"):
            return self.repo.unlink_user_term(user_id=user_id, term_id=term_id)
        if not hasattr(self.repo, "delete_term"):
            raise RuntimeError("El repositorio no soporta eliminación administrativa.")
        return self.repo.delete_term(term_id)

    def bi_analytics(self) -> dict:
        if hasattr(self.repo, "get_bi_analytics"):
            return self.repo.get_bi_analytics()
        return {
            "total_reviews_logged": 0,
            "hardest_terms": [],
            "area_breakdown": [],
            "user_metrics": [],
        }

    # ---------------------------------------------------------------- SRS
    def category_mastery(self, category_id: int, today: Optional[date] = None, user_id: int = 1) -> dict:
        today = today or date.today()
        threshold = int(self.get_settings(user_id=user_id).get("maturity_threshold_days", 21))
        terms = self.repo.list_terms(category_id=category_id)
        try:
            states_map = self.repo.list_srs_states(user_id=user_id)
        except TypeError:
            states_map = self.repo.list_srs_states()
        states = [states_map[t.id] for t in terms if t.id in states_map]
        return srs_scheduler.compute_mastery_metrics(states, today, maturity_threshold_days=threshold)

    def mastery_by_category(self, today: Optional[date] = None, user_id: int = 1) -> dict[int, dict]:
        today = today or date.today()
        threshold = int(self.get_settings(user_id=user_id).get("maturity_threshold_days", 21))
        cats = self.repo.list_categories()
        terms = self.repo.list_terms()
        try:
            states_map = self.repo.list_srs_states(user_id=user_id)
        except TypeError:
            states_map = self.repo.list_srs_states()
        by_cat: dict[int, list[SRSState]] = {c.id: [] for c in cats}
        for t in terms:
            if t.id in states_map:
                by_cat.setdefault(t.category_id, []).append(states_map[t.id])
        return {
            cid: srs_scheduler.compute_mastery_metrics(st_list, today, maturity_threshold_days=threshold)
            for cid, st_list in by_cat.items()
        }

    def global_mastery(self, today: Optional[date] = None, user_id: int = 1) -> dict:
        today = today or date.today()
        threshold = int(self.get_settings(user_id=user_id).get("maturity_threshold_days", 21))
        try:
            states_map = self.repo.list_srs_states(user_id=user_id)
        except TypeError:
            states_map = self.repo.list_srs_states()
        states = list(states_map.values())
        return srs_scheduler.compute_mastery_metrics(states, today, maturity_threshold_days=threshold)

    def active_focus(self, today: Optional[date] = None, user_id: int = 1) -> Optional[dict]:
        """Devuelve el Área y Categoría específica en la que se concentra el estudio actual hasta dominarla."""
        today = today or date.today()
        cfg = self.get_settings(user_id=user_id)
        if not cfg.get("focused_mode", True):
            return None
        active_cat_ids = cfg.get("active_category_ids") or []
        cat_id = None
        if hasattr(self.repo, "get_active_focus_category_id"):
            try:
                cat_id = self.repo.get_active_focus_category_id(
                    today, user_id=user_id, active_category_ids=active_cat_ids
                )
            except TypeError:
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
        cat_due = self.count_due(today, category_id=cat_id, user_id=user_id)
        cat_mastery = self.category_mastery(cat_id, today, user_id=user_id)
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
        user_id: int = 1,
    ) -> list[TermCard]:
        today = today or date.today()
        cfg = self.get_settings(user_id=user_id)
        use_focused = cfg.get("focused_mode", True) if focused is None else focused
        use_limit = cfg.get("session_card_limit", 5) if limit is None else limit
        active_cat_ids = cfg.get("active_category_ids") or []
        try:
            due_ids = self.repo.list_due_term_ids(
                today,
                limit=use_limit,
                focused=use_focused,
                user_id=user_id,
                active_category_ids=active_cat_ids,
            )
        except TypeError:
            try:
                due_ids = self.repo.list_due_term_ids(today, limit=use_limit, focused=use_focused)
            except TypeError:
                due_ids = self.repo.list_due_term_ids(today, limit=use_limit)
        return [c for tid in due_ids if (c := self.get_card(tid, user_id=user_id))]

    def count_due(
        self,
        today: Optional[date] = None,
        category_id: Optional[int] = None,
        user_id: int = 1,
    ) -> int:
        today = today or date.today()
        active_cat_ids = self.get_settings(user_id=user_id).get("active_category_ids") or []
        try:
            return self.repo.count_due(
                today, category_id=category_id, user_id=user_id, active_category_ids=active_cat_ids
            )
        except TypeError:
            try:
                return self.repo.count_due(today, category_id=category_id)
            except TypeError:
                return self.repo.count_due(today)

    def preview_intervals(self, srs: SRSState, now: Optional[datetime] = None) -> dict[Rating, int]:
        return srs_scheduler.preview(srs, now or datetime.now())

    def review(self, term_id: int, rating: Rating, now: Optional[datetime] = None, user_id: int = 1) -> SRSState:
        now = now or datetime.now()
        try:
            state = self.repo.get_srs_state(term_id, user_id=user_id)
        except TypeError:
            state = self.repo.get_srs_state(term_id)
        if state is None:
            raise KeyError(f"Término {term_id} no existe")
        new_state = srs_scheduler.schedule(state, Rating(rating), now)
        try:
            self.repo.save_srs_state(new_state, user_id=user_id)
        except TypeError:
            self.repo.save_srs_state(new_state)
        log = ReviewLog(
            id=None, term_id=term_id, rating=Rating(rating), reviewed_at=now,
            interval_before=state.interval_days, interval_after=new_state.interval_days,
            ease_factor_after=new_state.ease_factor,
        )
        try:
            self.repo.append_review_log(log, user_id=user_id)
        except TypeError:
            self.repo.append_review_log(log)
        return new_state

    def stats(self, today: Optional[date] = None, user_id: int = 1) -> dict:
        today = today or date.today()
        g_mastery = self.global_mastery(today, user_id=user_id)
        return {
            "total_terms": self.repo.count_terms(),
            "total_categories": len(self.repo.list_categories()),
            "due_today": self.count_due(today, user_id=user_id),
            "reviewed_today": self.repo.count_reviews_on(today),
            "ai_available": bool(self.enricher and self.enricher.available),
            "active_focus": self.active_focus(today, user_id=user_id),
            "global_mastery": g_mastery,
            "settings": self.get_settings(user_id=user_id),
        }
