"""Suite de pruebas automatizadas para la arquitectura Cloud-Native de Glosario SRS.

Verifica:
1. Dominio: algoritmo SM-2 puro (Olvidado -> 0d, Dudé -> corto plazo, Dominado -> espaciado).
2. Endpoints Cloud:
   - GET /api/health
   - POST /api/cron/trigger-srs (seguridad con CRON_SECRET)
   - POST /api/telegram/webhook (autenticación y formato de callbacks)
3. Inmutabilidad y reglas de negocio del repositorio (append-only, triggers).
"""
from __future__ import annotations

import os
from datetime import date, datetime
import unittest

from fastapi.testclient import TestClient

from src.domain.models import (
    DEFAULT_LIMITS_LABEL, DEFAULT_ZOOM_LABELS, Category, MacroArea, NewTermDraft,
    Rating, ReviewLog, SRSState, Term, UserTermCustomization, ZoomLevel,
)
from src.domain import srs_scheduler
from src.application.use_cases import GlossaryService
from src.interfaces.api_server import app, get_service


class TestSRSScheduler(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 15, 10, 0, 0)
        self.state = SRSState(term_id=1, interval_days=0, ease_factor=2.5, repetitions=0)

    def test_forgotten_resets_reps_and_due_today(self):
        advanced = SRSState(term_id=1, interval_days=20, ease_factor=2.4, repetitions=5)
        new_state = srs_scheduler.schedule(advanced, Rating.FORGOTTEN, self.now)
        self.assertEqual(new_state.interval_days, 0)
        self.assertEqual(new_state.repetitions, 0)
        self.assertEqual(new_state.next_review_date, self.now.date())

    def test_doubted_short_term_growth(self):
        s1 = srs_scheduler.schedule(self.state, Rating.DOUBTED, self.now)
        self.assertEqual(s1.interval_days, 1)
        self.assertEqual(s1.repetitions, 1)
        self.assertLess(s1.ease_factor, 2.5)

        s2 = srs_scheduler.schedule(s1, Rating.DOUBTED, self.now)
        self.assertEqual(s2.interval_days, 3)
        self.assertEqual(s2.repetitions, 2)

    def test_mastered_sm2_progression(self):
        s1 = srs_scheduler.schedule(self.state, Rating.MASTERED, self.now)
        self.assertEqual(s1.interval_days, 1)
        self.assertEqual(s1.repetitions, 1)
        self.assertGreater(s1.ease_factor, 2.5)

        s2 = srs_scheduler.schedule(s1, Rating.MASTERED, self.now)
        self.assertEqual(s2.interval_days, 6)
        self.assertEqual(s2.repetitions, 2)

        s3 = srs_scheduler.schedule(s2, Rating.MASTERED, self.now)
        expected = round(6 * s3.ease_factor)
        self.assertEqual(s3.interval_days, expected)


class InMemoryRepository:
    """Doble de prueba en memoria que replica las reglas append-only de PostgreSQL."""

    def __init__(self):
        self.categories: dict[int, Category] = {}
        self.terms: dict[int, Term] = {}
        self.srs_states: dict[int, SRSState] = {}
        self.review_logs: list[ReviewLog] = []
        self.customizations: dict[tuple[int, int], UserTermCustomization] = {}
        self._next_cat_id = 1
        self._next_term_id = 1

    def init_schema(self):
        pass

    def get_or_create_category(self, macro_area: str, name: str, description: str, zoom_level: ZoomLevel) -> Category:
        for c in self.categories.values():
            if c.macro_area.casefold() == macro_area.casefold() and c.name.casefold() == name.casefold():
                return c
        cat = Category(self._next_cat_id, macro_area, name, description, zoom_level)
        self.categories[self._next_cat_id] = cat
        self._next_cat_id += 1
        return cat

    def list_categories(self) -> list[Category]:
        return list(self.categories.values())


    def upsert_macro_area(self, name, description="", zoom_labels=None, limits_label=None):
        if not hasattr(self, "_macro_areas"):
            self._macro_areas = {}
        labels = tuple(zoom_labels) if zoom_labels and len(zoom_labels) == 4 else DEFAULT_ZOOM_LABELS
        prev = self._macro_areas.get(name)
        lim = limits_label or (prev.limits_label if prev else DEFAULT_LIMITS_LABEL)
        ma = MacroArea(
            id=len(self._macro_areas) + 1 if not prev else prev.id,
            name=name,
            description=description or (prev.description if prev else ""),
            zoom_labels=labels,
            limits_label=lim,
        )
        self._macro_areas[name] = ma
        return ma

    def list_macro_areas(self):
        return dict(getattr(self, "_macro_areas", {}))


    def get_category(self, cid: int) -> Category | None:
        return self.categories.get(cid)

    def count_terms_by_category(self) -> dict[int, int]:
        counts = {}
        for t in self.terms.values():
            counts[t.category_id] = counts.get(t.category_id, 0) + 1
        return counts

    def find_terms_by_name(self, name: str) -> list[Term]:
        norm = name.strip().casefold()
        return [t for t in self.terms.values() if t.term.strip().casefold() == norm]

    def find_term_by_name(self, name: str, category_id: int | None = None) -> Term | None:
        matches = self.find_terms_by_name(name)
        if category_id is not None:
            return next((t for t in matches if t.category_id == category_id), None)
        return matches[0] if matches else None

    def get_term(self, tid: int) -> Term | None:
        return self.terms.get(tid)

    def list_terms(self, category_id: int | None = None) -> list[Term]:
        if category_id is not None:
            return [t for t in self.terms.values() if t.category_id == category_id]
        return list(self.terms.values())

    def add_term(
        self,
        category_id: int,
        draft: NewTermDraft,
        today: date | None = None,
        initial_ease_factor: float = 2.5,
    ) -> Term:
        existing = self.find_term_by_name(draft.term, category_id=category_id)
        if existing:
            from src.infrastructure.db_repository import TermAlreadyExistsError
            raise TermAlreadyExistsError(existing)

        tid = self._next_term_id
        self._next_term_id += 1
        term = Term(
            id=tid,
            category_id=category_id,
            term=draft.term,
            technical_definition=draft.technical_definition,
            daily_analogy=draft.daily_analogy,
            tradeoff_when_not_to_use=draft.tradeoff_when_not_to_use,
            created_at=datetime.now(),
            other_scopes=tuple(draft.other_scopes or ()),
        )
        self.terms[tid] = term
        self.srs_states[tid] = SRSState(
            term_id=tid,
            ease_factor=float(initial_ease_factor),
            next_review_date=today or date.today(),
        )
        return term

    def update_term_from_draft(self, term_id: int, category_id: int, draft: NewTermDraft) -> Term | None:
        old = self.terms.get(term_id)
        if not old:
            return None
        updated = Term(
            id=term_id,
            category_id=category_id,
            term=draft.term,
            technical_definition=draft.technical_definition,
            daily_analogy=draft.daily_analogy,
            tradeoff_when_not_to_use=draft.tradeoff_when_not_to_use,
            created_at=old.created_at,
            other_scopes=tuple(draft.other_scopes or ()),
        )
        self.terms[term_id] = updated
        return updated

    def delete_term(self, term_id: int) -> bool:
        if term_id not in self.terms:
            return False
        del self.terms[term_id]
        self.srs_states.pop(term_id, None)
        self.review_logs = [l for l in self.review_logs if l.term_id != term_id]
        return True

    def fill_missing_tradeoff(self, term_id: int, tradeoff: str) -> bool:
        t = self.terms.get(term_id)
        if t and t.tradeoff_when_not_to_use is None:
            # Enriquecimiento no destructivo
            self.terms[term_id] = Term(
                id=t.id, category_id=t.category_id, term=t.term,
                technical_definition=t.technical_definition, daily_analogy=t.daily_analogy,
                tradeoff_when_not_to_use=tradeoff, created_at=t.created_at,
                other_scopes=t.other_scopes,
            )
            return True
        return False

    def count_terms(self) -> int:
        return len(self.terms)

    def get_srs_state(self, tid: int) -> SRSState | None:
        return self.srs_states.get(tid)

    def list_srs_states(self) -> dict[int, SRSState]:
        return dict(self.srs_states)

    def save_srs_state(self, s: SRSState):
        self.srs_states[s.term_id] = s

    def get_active_focus_category_id(
        self,
        today: date,
        user_id: int = 1,
        active_category_ids: list[int] | None = None,
    ) -> int | None:
        filter_ids = {int(cid) for cid in (active_category_ids or []) if cid}
        due_cats = []
        for c in self.categories.values():
            if filter_ids and c.id not in filter_ids:
                continue
            cat_terms = [t for t in self.terms.values() if t.category_id == c.id]
            has_due = any(self.srs_states[t.id].next_review_date <= today for t in cat_terms if t.id in self.srs_states)
            if not has_due:
                continue
            reviewed_today_ts = [
                self.srs_states[t.id].last_reviewed_at
                for t in cat_terms
                if t.id in self.srs_states
                and self.srs_states[t.id].last_reviewed_at is not None
                and self.srs_states[t.id].last_reviewed_at.date() == today
            ]
            has_review_today = 0 if reviewed_today_ts else 1
            latest_ts = max(reviewed_today_ts).timestamp() if reviewed_today_ts else 0.0
            due_cats.append(((has_review_today, -latest_ts, c.id, int(c.zoom_level)), c.id))
        if not due_cats:
            return None
        due_cats.sort(key=lambda item: item[0])
        return due_cats[0][1]

    def list_due_term_ids(
        self,
        today: date,
        limit: int | None = None,
        focused: bool = True,
        category_id: int | None = None,
        user_id: int = 1,
        active_category_ids: list[int] | None = None,
        distinct_terms: bool = True,
    ) -> list[int]:
        filter_ids = {int(cid) for cid in (active_category_ids or []) if cid}
        target_cat_id = category_id
        if focused and target_cat_id is None:
            target_cat_id = self.get_active_focus_category_id(
                today, user_id=user_id, active_category_ids=list(filter_ids)
            )
            if target_cat_id is None:
                return []

        def sort_key(item):
            tid, s = item
            reviewed_today = 1 if (s.last_reviewed_at and s.last_reviewed_at.date() == today) else 0
            return (reviewed_today, s.next_review_date, s.ease_factor, tid)

        raw_due = [
            tid for tid, s in sorted(self.srs_states.items(), key=sort_key)
            if s.next_review_date <= today
            and tid in self.terms
            and (
                (target_cat_id is not None and self.terms[tid].category_id == target_cat_id)
                or (target_cat_id is None and (not filter_ids or self.terms[tid].category_id in filter_ids))
            )
        ]
        if not distinct_terms:
            return raw_due[:limit] if limit else raw_due

        primary_ids = []
        overflow_ids = []
        seen_names = set()
        for tid in raw_due:
            norm = self.terms[tid].term.strip().casefold()
            if norm not in seen_names:
                seen_names.add(norm)
                primary_ids.append(tid)
            else:
                overflow_ids.append(tid)
        ordered = primary_ids + overflow_ids
        return ordered[:limit] if limit else ordered

    def count_due(
        self,
        today: date,
        category_id: int | None = None,
        user_id: int = 1,
        active_category_ids: list[int] | None = None,
    ) -> int:
        filter_ids = {int(cid) for cid in (active_category_ids or []) if cid}
        return sum(
            1 for tid, s in self.srs_states.items()
            if s.next_review_date <= today
            and tid in self.terms
            and (
                (category_id is not None and self.terms[tid].category_id == category_id)
                or (category_id is None and (not filter_ids or self.terms[tid].category_id in filter_ids))
            )
        )

    def append_review_log(self, log: ReviewLog, user_id: int = 1):
        self.review_logs.append(log)

    def count_reviews_on(self, day: date, user_id: int | None = None) -> int:
        return sum(1 for l in self.review_logs if l.reviewed_at.date() == day)

    def get_settings(self, user_id: int = 1) -> dict:
        if not hasattr(self, "_settings_by_user"):
            self._settings_by_user = {}
        if user_id not in self._settings_by_user:
            self._settings_by_user[user_id] = {
                "session_card_limit": 5,
                "maturity_threshold_days": 21,
                "initial_ease_factor": 2.5,
                "focused_mode": True,
                "theme": "light",
                "step_by_step_reveal": False,
                "active_category_ids": [],
            }
        return dict(self._settings_by_user[user_id])

    def update_settings(self, user_id: int = 1, **kwargs) -> dict:
        cur = self.get_settings(user_id=user_id)
        for k, v in kwargs.items():
            if v is not None and k in cur:
                cur[k] = v
        self._settings_by_user[user_id] = cur
        return dict(self._settings_by_user[user_id])

    # --- Soporte Multi-Tenant & BI en memoria ---
    def _ensure_users(self):
        if not hasattr(self, "users"):
            self.users = {
                1: {
                    "id": 1,
                    "telegram_chat_id": None,
                    "display_name": "Administrador Principal",
                    "role": "admin",
                    "web_pin": None,
                    "daily_ai_limit": 9999,
                    "ai_calls_today": 0,
                    "ai_calls_date": date.today().isoformat(),
                    "is_approved": True,
                }
            }
            self._next_user_id = 2
            self.user_unlinked: dict[int, set[int]] = {}

    def list_users(self) -> list[dict]:
        self._ensure_users()
        return list(self.users.values())

    def get_user_by_pin(self, pin: str) -> dict | None:
        self._ensure_users()
        for u in self.users.values():
            if u.get("web_pin") == pin and u.get("is_approved"):
                return dict(u)
        return None

    def get_user_by_chat_id(self, chat_id: str | int) -> dict | None:
        self._ensure_users()
        cid = str(chat_id).strip()
        for u in self.users.values():
            if u.get("telegram_chat_id") == cid:
                return dict(u)
        return None

    def create_or_update_user(
        self,
        user_id: int | None = None,
        telegram_chat_id: str | None = None,
        display_name: str = "Usuario",
        role: str = "user",
        web_pin: str | None = None,
        daily_ai_limit: int = 10,
        is_approved: bool = True,
    ) -> dict:
        self._ensure_users()
        uid = user_id or self._next_user_id
        if user_id is None:
            self._next_user_id += 1
        u = {
            "id": uid,
            "telegram_chat_id": str(telegram_chat_id).strip() if telegram_chat_id else None,
            "display_name": display_name,
            "role": role,
            "web_pin": web_pin,
            "daily_ai_limit": int(daily_ai_limit),
            "ai_calls_today": 0,
            "ai_calls_date": date.today().isoformat(),
            "is_approved": bool(is_approved),
        }
        self.users[uid] = u
        return dict(u)

    def delete_user(self, user_id: int) -> bool:
        self._ensure_users()
        if user_id == 1 or user_id not in self.users:
            return False
        del self.users[user_id]
        return True

    def check_and_consume_ai_quota(self, user_id: int = 1, today: date | None = None) -> tuple[bool, int, int]:
        self._ensure_users()
        u = self.users.get(user_id)
        if not u:
            return True, 0, 9999
        if u["role"] == "admin":
            u["ai_calls_today"] += 1
            return True, u["ai_calls_today"], u["daily_ai_limit"]
        if u["ai_calls_today"] >= u["daily_ai_limit"]:
            return False, u["ai_calls_today"], u["daily_ai_limit"]
        u["ai_calls_today"] += 1
        return True, u["ai_calls_today"], u["daily_ai_limit"]

    def ensure_user_terms(self, user_id: int, term_ids: list[int], initial_ease_factor: float = 2.5) -> int:
        self._ensure_users()
        return len(term_ids)

    def unlink_user_term(self, user_id: int, term_id: int) -> bool:
        self._ensure_users()
        if term_id not in self.terms:
            return False
        self.user_unlinked.setdefault(user_id, set()).add(term_id)
        return True

    def get_bi_analytics(self) -> dict:
        self._ensure_users()
        return {
            "total_reviews_logged": len(self.review_logs),
            "hardest_terms": [
                {
                    "term_id": t.id,
                    "term": t.term,
                    "macro_area": self.categories[t.category_id].macro_area,
                    "category_name": self.categories[t.category_id].name,
                    "total_reviews": sum(1 for r in self.review_logs if r.term_id == t.id),
                    "forgotten_count": sum(1 for r in self.review_logs if r.term_id == t.id and int(r.rating) == 1),
                    "forgotten_rate_pct": 0.0,
                }
                for t in self.terms.values()
            ],
            "area_breakdown": [
                {
                    "macro_area": c.macro_area,
                    "category_count": 1,
                    "term_count": sum(1 for t in self.terms.values() if t.category_id == c.id),
                    "avg_ease_factor": 2.5,
                    "avg_interval_days": 0.0,
                    "canonically_mastered_terms": 0,
                }
                for c in self.categories.values()
            ],
            "user_metrics": list(self.users.values()),
        }

    def get_user_customization(self, user_id: int, term_id: int) -> UserTermCustomization | None:
        return self.customizations.get((int(user_id), int(term_id)))

    def list_user_customizations(self, user_id: int) -> dict[int, UserTermCustomization]:
        return {tid: c for (uid, tid), c in self.customizations.items() if uid == int(user_id)}

    def save_user_customization(
        self,
        user_id: int,
        term_id: int,
        custom_definition: str | None = None,
        custom_analogy: str | None = None,
        custom_limits: str | None = None,
        personal_notes: str | None = None,
    ) -> UserTermCustomization:
        prev = self.get_user_customization(user_id, term_id)
        cust = UserTermCustomization(
            user_id=int(user_id),
            term_id=int(term_id),
            custom_definition=custom_definition if custom_definition is not None else (prev.custom_definition if prev else None),
            custom_analogy=custom_analogy if custom_analogy is not None else (prev.custom_analogy if prev else None),
            custom_limits=custom_limits if custom_limits is not None else (prev.custom_limits if prev else None),
            personal_notes=personal_notes if personal_notes is not None else (prev.personal_notes if prev else None),
            status=prev.status if prev else "private",
            proposal_justification=prev.proposal_justification if prev else None,
            proposal_source=prev.proposal_source if prev else None,
            arbitration_score=prev.arbitration_score if prev else None,
            arbitration_verdict=prev.arbitration_verdict if prev else None,
            updated_at=datetime.now(),
        )
        self.customizations[(int(user_id), int(term_id))] = cust
        return cust

    def delete_user_customization(self, user_id: int, term_id: int) -> bool:
        return self.customizations.pop((int(user_id), int(term_id)), None) is not None

    def submit_canonical_proposal(
        self,
        user_id: int,
        term_id: int,
        proposal_justification: str,
        proposal_source: str,
        custom_definition: str | None = None,
        custom_analogy: str | None = None,
        custom_limits: str | None = None,
    ) -> UserTermCustomization:
        prev = self.get_user_customization(user_id, term_id)
        cust = UserTermCustomization(
            user_id=int(user_id),
            term_id=int(term_id),
            custom_definition=custom_definition or (prev.custom_definition if prev else None),
            custom_analogy=custom_analogy or (prev.custom_analogy if prev else None),
            custom_limits=custom_limits or (prev.custom_limits if prev else None),
            personal_notes=prev.personal_notes if prev else None,
            status="proposed_to_canonical",
            proposal_justification=proposal_justification,
            proposal_source=proposal_source,
            updated_at=datetime.now(),
        )
        self.customizations[(int(user_id), int(term_id))] = cust
        return cust

    def save_proposal_arbitration(self, user_id: int, term_id: int, score: float, verdict: str) -> None:
        prev = self.get_user_customization(user_id, term_id)
        if prev:
            self.customizations[(int(user_id), int(term_id))] = UserTermCustomization(
                user_id=prev.user_id,
                term_id=prev.term_id,
                custom_definition=prev.custom_definition,
                custom_analogy=prev.custom_analogy,
                custom_limits=prev.custom_limits,
                personal_notes=prev.personal_notes,
                status=prev.status,
                proposal_justification=prev.proposal_justification,
                proposal_source=prev.proposal_source,
                arbitration_score=score,
                arbitration_verdict=verdict,
                updated_at=datetime.now(),
            )

    def list_pending_proposals(self) -> list[dict]:
        out = []
        for (uid, tid), c in self.customizations.items():
            if c.status in ("proposed_to_canonical", "rejected_by_arbitration"):
                t = self.terms.get(tid)
                cat = self.categories.get(t.category_id) if t else None
                out.append({
                    "user_id": uid,
                    "term_id": tid,
                    "user_name": "Estudiante Test",
                    "user_role": "user",
                    "term_name": t.term if t else "",
                    "macro_area": cat.macro_area if cat else "",
                    "category_name": cat.name if cat else "",
                    "canonical_definition": t.technical_definition if t else "",
                    "canonical_analogy": t.daily_analogy if t else "",
                    "canonical_limits": t.tradeoff_when_not_to_use if t else None,
                    "canonical_source": t.canonical_source if t else "",
                    "proposed_definition": c.custom_definition,
                    "proposed_analogy": c.custom_analogy,
                    "proposed_limits": c.custom_limits,
                    "proposal_justification": c.proposal_justification,
                    "proposal_source": c.proposal_source,
                    "arbitration_score": c.arbitration_score,
                    "arbitration_verdict": c.arbitration_verdict,
                    "status": c.status,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                })
        return out

    def resolve_proposal(self, user_id: int, term_id: int, action: str, admin_notes: str | None = None) -> bool:
        prev = self.get_user_customization(user_id, term_id)
        if not prev:
            return False
        if action.lower() == "merge":
            t = self.terms.get(term_id)
            if t:
                self.terms[term_id] = Term(
                    id=t.id,
                    category_id=t.category_id,
                    term=t.term,
                    technical_definition=prev.custom_definition or t.technical_definition,
                    daily_analogy=prev.custom_analogy or t.daily_analogy,
                    tradeoff_when_not_to_use=prev.custom_limits if prev.custom_limits is not None else t.tradeoff_when_not_to_use,
                    created_at=t.created_at,
                    other_scopes=t.other_scopes,
                    canonical_source=prev.proposal_source or t.canonical_source,
                    parent_term=t.parent_term,
                    related_terms=t.related_terms,
                )
            self.customizations[(int(user_id), int(term_id))] = UserTermCustomization(
                user_id=prev.user_id,
                term_id=prev.term_id,
                custom_definition=prev.custom_definition,
                custom_analogy=prev.custom_analogy,
                custom_limits=prev.custom_limits,
                personal_notes=prev.personal_notes,
                status="merged_to_canonical",
                proposal_justification=prev.proposal_justification,
                proposal_source=prev.proposal_source,
                arbitration_score=prev.arbitration_score,
                arbitration_verdict=admin_notes or prev.arbitration_verdict,
                updated_at=datetime.now(),
            )
            return True
        elif action.lower() == "reject":
            self.customizations[(int(user_id), int(term_id))] = UserTermCustomization(
                user_id=prev.user_id,
                term_id=prev.term_id,
                custom_definition=prev.custom_definition,
                custom_analogy=prev.custom_analogy,
                custom_limits=prev.custom_limits,
                personal_notes=prev.personal_notes,
                status="rejected_by_arbitration",
                proposal_justification=prev.proposal_justification,
                proposal_source=prev.proposal_source,
                arbitration_score=prev.arbitration_score,
                arbitration_verdict=admin_notes or prev.arbitration_verdict,
                updated_at=datetime.now(),
            )
            return True
        return False



class TestCloudEndpoints(unittest.TestCase):
    def setUp(self):
        # Inyectar repo en memoria para pruebas aisladas
        self.repo = InMemoryRepository()
        self.service = GlossaryService(self.repo)
        
        # Configurar dependency override para get_service en FastAPI
        import src.interfaces.api_server as api_module
        api_module._service_instance = self.service

        from src.config import settings
        settings.WEB_ACCESS_PIN = None
        settings.TELEGRAM_WEBHOOK_SECRET = None
        settings.CRON_SECRET = None
        settings.TELEGRAM_CHAT_ID = None

        os.environ.pop("WEB_ACCESS_PIN", None)
        os.environ.pop("TELEGRAM_WEBHOOK_SECRET", None)
        os.environ.pop("CRON_SECRET", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)

        from src.interfaces.web_routes import _failed_pin_attempts
        _failed_pin_attempts.clear()

        from src.interfaces.webhook_routes import clear_update_id_cache
        clear_update_id_cache()

        from src.interfaces.cron_routes import reset_cron_cooldown
        reset_cron_cooldown()

        self.client = TestClient(app)


    def test_health_check(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("cloud"))

    def test_cron_trigger_security(self):
        from src.config import settings
        settings.CRON_SECRET = "clave_secreta_test"
        settings.TELEGRAM_CHAT_ID = "12345678"
        os.environ["CRON_SECRET"] = "clave_secreta_test"
        os.environ["TELEGRAM_CHAT_ID"] = "12345678"

        # 1. Petición sin header -> 403
        bad_res = self.client.post("/api/cron/trigger-srs")
        self.assertEqual(bad_res.status_code, 403)

        # 2. Petición con header correcto
        good_res = self.client.post(
            "/api/cron/trigger-srs",
            headers={"CRON_SECRET": "clave_secreta_test"}
        )
        self.assertEqual(good_res.status_code, 200)
        self.assertEqual(good_res.json()["status"], "ok")

    def test_telegram_webhook_unauthorized_filter(self):
        from src.config import settings
        settings.TELEGRAM_CHAT_ID = "9999"
        settings.TELEGRAM_WEBHOOK_SECRET = None
        os.environ["TELEGRAM_CHAT_ID"] = "9999"

        # Mensaje de otro usuario no autorizado
        payload = {
            "update_id": 1,
            "message": {
                "message_id": 100,
                "chat": {"id": 1111},
                "from": {"id": 1111},
                "text": "CQRS"
            }
        }
        res = self.client.post("/api/telegram/webhook", json=payload)
        self.assertEqual(res.status_code, 200)
        # El término no debió ser agregado porque el usuario no es 9999
        self.assertEqual(self.repo.count_terms(), 0)

    def test_web_access_pin_hardening(self):
        from src.config import settings
        settings.WEB_ACCESS_PIN = "7890"

        # 1. Endpoints de lectura protegidos sin PIN -> 401 Unauthorized
        for path in ("/api/stats", "/api/terms", "/api/areas", "/api/review/due"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 401, f"GET {path} debería requerir PIN")

        # 2. Endpoints de lectura con PIN correcto -> 200 OK
        for path in ("/api/stats", "/api/terms", "/api/areas", "/api/review/due"):
            res = self.client.get(path, headers={"X-Web-Pin": "7890"})
            self.assertEqual(res.status_code, 200, f"GET {path} debería responder 200 con PIN")

        # 3. POST /api/terms sin PIN -> 401 Unauthorized
        res_no_pin = self.client.post("/api/terms", json={"term": "Circuit Breaker"})
        self.assertEqual(res_no_pin.status_code, 401)

        # 4. POST /api/terms con PIN incorrecto -> 401 Unauthorized
        res_wrong_pin = self.client.post(
            "/api/terms",
            json={"term": "Circuit Breaker"},
            headers={"X-Web-Pin": "0000"}
        )
        self.assertEqual(res_wrong_pin.status_code, 401)

        # 5. POST /api/review/1 sin PIN -> 401 Unauthorized
        res_rev_no_pin = self.client.post("/api/review/1", json={"rating": 3})
        self.assertEqual(res_rev_no_pin.status_code, 401)

        # 6. POST /api/review/1 con PIN incorrecto -> 401 Unauthorized
        res_rev_wrong = self.client.post(
            "/api/review/1",
            json={"rating": 3},
            headers={"X-Web-Pin": "wrong"}
        )
        self.assertEqual(res_rev_wrong.status_code, 401)

    def test_rate_limiting_brute_force_lockout(self):
        from src.config import settings
        settings.WEB_ACCESS_PIN = "secret123"

        test_ip_headers = {"X-Forwarded-For": "198.51.100.5"}

        # 5 intentos fallidos -> 401
        for i in range(5):
            res = self.client.get(
                "/api/stats",
                headers={"X-Web-Pin": f"bad_pin_{i}", **test_ip_headers}
            )
            self.assertEqual(res.status_code, 401)

        # El 6to intento debe recibir 429 Too Many Requests
        lockout_res = self.client.get(
            "/api/stats",
            headers={"X-Web-Pin": "bad_pin_6", **test_ip_headers}
        )
        self.assertEqual(lockout_res.status_code, 429)
        self.assertIn("Demasiados intentos fallidos", lockout_res.json()["detail"])

    def test_security_headers_middleware(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(res.headers.get("x-frame-options"), "DENY")
        self.assertEqual(res.headers.get("referrer-policy"), "strict-origin-when-cross-origin")

    def test_telegram_webhook_secret_cryptographic_verification(self):
        from src.config import settings
        settings.TELEGRAM_WEBHOOK_SECRET = "telegram_secret_seguro_123"
        settings.TELEGRAM_CHAT_ID = "1234"
        os.environ["TELEGRAM_CHAT_ID"] = "1234"

        payload = {"update_id": 999, "message": {"chat": {"id": 1234}, "text": "/start"}}

        # 1. Sin header -> 403 Forbidden
        res_no_secret = self.client.post("/api/telegram/webhook", json=payload)
        self.assertEqual(res_no_secret.status_code, 403)

        # 2. Con secret incorrecto -> 403 Forbidden
        res_bad_secret = self.client.post(
            "/api/telegram/webhook",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret_falso"}
        )
        self.assertEqual(res_bad_secret.status_code, 403)

        # 3. Con secret correcto -> 200 OK
        res_ok = self.client.post(
            "/api/telegram/webhook",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram_secret_seguro_123"}
        )
        self.assertEqual(res_ok.status_code, 200)

    def test_pwa_static_delivery(self):
        index_res = self.client.get("/")
        self.assertEqual(index_res.status_code, 200)
        self.assertIn("text/html", index_res.headers["content-type"])

        sw_res = self.client.get("/sw.js")
        self.assertEqual(sw_res.status_code, 200)

    def test_greeting_guardrail_rejects_hola(self):
        from src.infrastructure.ai_enricher import GeminiEnricher, InvalidTermError
        enricher = GeminiEnricher(api_key="dummy_key")
        for noise in ("Hola", "buenos días", "gracias", "ok", "?", "jaja"):
            with self.assertRaises(InvalidTermError):
                enricher.enrich(noise, [])

    def test_ai_enricher_max_length_guardrail(self):
        from src.infrastructure.ai_enricher import GeminiEnricher, InvalidTermError
        enricher = GeminiEnricher(api_key="dummy_key")

        # Término > 120 caracteres -> InvalidTermError
        with self.assertRaises(InvalidTermError) as cm_term:
            enricher.enrich("A" * 121, [])
        self.assertIn("120 caracteres", str(cm_term.exception))

        # Target scope > 60 caracteres -> InvalidTermError
        with self.assertRaises(InvalidTermError) as cm_scope:
            enricher.enrich("REST", [], target_scope="B" * 61)
        self.assertIn("60 caracteres", str(cm_scope.exception))

    def test_multi_scope_same_term_different_categories(self):
        c_arch = self.repo.get_or_create_category(
            "Ingeniería de Software", "Arquitectura de Software", "Estilo macro", ZoomLevel.MACRO
        )
        c_prog = self.repo.get_or_create_category(
            "Ingeniería de Software", "Programación de APIs", "Implementación en código", ZoomLevel.MICRO
        )
        d_arch = NewTermDraft(
            term="REST",
            macro_area="Ingeniería de Software",
            category_name="Arquitectura de Software",
            category_description="Estilo macro",
            zoom_level=ZoomLevel.MACRO,
            technical_definition="Estilo arquitectónico de Roy Fielding para sistemas hipermedia distribuidos.",
            daily_analogy="Un catálogo con enlaces navegables.",
            tradeoff_when_not_to_use="Overhead en comunicación binaria de ultra baja latencia.",
        )
        d_prog = NewTermDraft(
            term="REST",
            macro_area="Ingeniería de Software",
            category_name="Programación de APIs",
            category_description="Implementación en código",
            zoom_level=ZoomLevel.MICRO,
            technical_definition="Convención de programación de controladores HTTP usando verbos CRUD y JSON.",
            daily_analogy="Un formulario estandarizado de pedidos.",
            tradeoff_when_not_to_use="Peticiones anidadas complejas donde GraphQL evita over-fetching.",
        )
        t1 = self.repo.add_term(c_arch.id, d_arch)
        t2 = self.repo.add_term(c_prog.id, d_prog)
        self.assertNotEqual(t1.id, t2.id)
        self.assertEqual(len(self.repo.find_terms_by_name("REST")), 2)

    def test_forgotten_card_requeued_to_back_of_session(self):
        cat = self.repo.get_or_create_category(
            "Arquitectura", "Patrones", "Patrones GoF", ZoomLevel.MEDIO
        )
        d1 = NewTermDraft("Adapter", "Arquitectura", "Patrones", "desc", ZoomLevel.MEDIO, "def 1", "analog 1", "trade 1")
        d2 = NewTermDraft("Bridge", "Arquitectura", "Patrones", "desc", ZoomLevel.MEDIO, "def 2", "analog 2", "trade 2")
        today = date(2026, 10, 17)
        t1 = self.repo.add_term(cat.id, d1, today=today)
        t2 = self.repo.add_term(cat.id, d2, today=today)

        # 1. Primera tarjeta entregada es t1 (Adapter)
        first_due = self.service.due_cards(today=today, limit=1)
        self.assertEqual(len(first_due), 1)
        self.assertEqual(first_due[0].term.id, t1.id)

        # 2. Calificar t1 como OLVIDADO
        now = datetime(2026, 10, 17, 10, 0, 0)
        self.service.review(t1.id, Rating.FORGOTTEN, now=now)

        # 3. La siguiente tarjeta NO debe ser t1 de nuevo: debe ser t2 (Bridge)!
        next_due = self.service.due_cards(today=today, limit=1)
        self.assertEqual(len(next_due), 1)
        self.assertEqual(next_due[0].term.id, t2.id, "Una tarjeta olvidada debe ir al final de la cola de hoy")

        # 4. Calificar t2 como DOMINADO
        self.service.review(t2.id, Rating.MASTERED, now=now)

        # 5. Ahora que t2 ya no está pendiente, t1 vuelve a presentarse para completar su ciclo
        re_due = self.service.due_cards(today=today, limit=1)
        self.assertEqual(len(re_due), 1)
        self.assertEqual(re_due[0].term.id, t1.id)

    def test_telegram_webhook_idempotency_deduplication(self):
        payload = {
            "update_id": 77777,
            "message": {
                "message_id": 50,
                "chat": {"id": 1234},
                "from": {"id": 1234},
                "text": "/stats"
            }
        }
        # Primer envío -> 200 OK
        r1 = self.client.post("/api/telegram/webhook", json=payload)
        self.assertEqual(r1.status_code, 200)

        # Segundo envío idéntico (reintento simulado de Telegram) -> ignorado
        r2 = self.client.post("/api/telegram/webhook", json=payload)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json().get("status"), "duplicate_ignored")

    def test_cron_cooldown_prevents_spam_triggers(self):
        from src.config import settings
        settings.CRON_SECRET = "secret_cron_cooldown"
        settings.TELEGRAM_CHAT_ID = "123456"
        os.environ["CRON_SECRET"] = "secret_cron_cooldown"
        os.environ["TELEGRAM_CHAT_ID"] = "123456"

        # 1. Primer trigger -> procesado
        r1 = self.client.post("/api/cron/trigger-srs", headers={"CRON_SECRET": "secret_cron_cooldown"})
        self.assertEqual(r1.status_code, 200)
        self.assertFalse(r1.json().get("cooldown"))

        # 2. Segundo trigger inmediato -> omitido por cooldown
        r2 = self.client.post("/api/cron/trigger-srs", headers={"CRON_SECRET": "secret_cron_cooldown"})
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json().get("cooldown"))

    def test_focused_category_progression_until_mastered(self):
        today = date(2026, 10, 17)
        now = datetime(2026, 10, 17, 10, 0, 0)
        cat_a = self.repo.get_or_create_category("Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO)
        cat_b = self.repo.get_or_create_category("Ingeniería de Software", "2. Arquitectura", "Macro", ZoomLevel.MACRO)

        t_a1 = self.repo.add_term(
            cat_a.id,
            NewTermDraft("Cohesión", "Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO, "def", "analog", "trade", ("Psicología", "Física")),
            today=today,
        )
        t_a2 = self.repo.add_term(
            cat_a.id,
            NewTermDraft("Acoplamiento", "Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO, "def", "analog", "trade"),
            today=today,
        )
        t_b1 = self.repo.add_term(
            cat_b.id,
            NewTermDraft("Microservicios", "Ingeniería de Software", "2. Arquitectura", "Macro", ZoomLevel.MACRO, "def", "analog", "trade"),
            today=today,
        )

        # 1. El enfoque inicial debe estar exclusivamente en cat_a (Principios)
        focus_1 = self.service.active_focus(today=today)
        self.assertEqual(focus_1["category_id"], cat_a.id)
        self.assertEqual(focus_1["category_due"], 2)

        due_1 = self.service.due_cards(today=today, limit=5)
        self.assertEqual([c.term.id for c in due_1], [t_a1.id, t_a2.id])
        self.assertEqual(due_1[0].term.other_scopes, ("Psicología", "Física"))

        # 2. Si olvida t_a1, sigue enfocado en cat_a hasta dominarla
        self.service.review(t_a1.id, Rating.FORGOTTEN, now=now)
        self.assertEqual(self.service.active_focus(today=today)["category_id"], cat_a.id)

        # 3. Domina t_a2 y luego domina t_a1 -> cat_a queda 100% dominada hoy
        self.service.review(t_a2.id, Rating.MASTERED, now=now)
        self.service.review(t_a1.id, Rating.MASTERED, now=now)

        # 4. Automáticamente el enfoque cambia a cat_b (2. Arquitectura)
        focus_2 = self.service.active_focus(today=today)
        self.assertEqual(focus_2["category_id"], cat_b.id)
        due_2 = self.service.due_cards(today=today, limit=5)
        self.assertEqual([c.term.id for c in due_2], [t_b1.id])

    def test_protected_active_session_not_hijacked_by_new_term(self):
        today = date(2026, 10, 17)
        now = datetime(2026, 10, 17, 10, 0, 0)
        cat_a = self.repo.get_or_create_category("Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO)
        cat_b = self.repo.get_or_create_category("Ingeniería de Software", "2. Arquitectura", "Macro", ZoomLevel.MACRO)

        # cat_a ya fue despejada antes; cat_b tiene 2 términos por dominar hoy
        t_b1 = self.repo.add_term(
            cat_b.id,
            NewTermDraft("CQRS", "Ingeniería de Software", "2. Arquitectura", "Macro", ZoomLevel.MACRO, "def", "analog", "trade"),
            today=today,
        )
        t_b2 = self.repo.add_term(
            cat_b.id,
            NewTermDraft("Event Sourcing", "Ingeniería de Software", "2. Arquitectura", "Macro", ZoomLevel.MACRO, "def", "analog", "trade"),
            today=today,
        )

        # El usuario inicia su sesión en cat_b repasando t_b1
        self.service.review(t_b1.id, Rating.MASTERED, now=now)
        self.assertEqual(self.service.active_focus(today=today)["category_id"], cat_b.id)

        # A mitad de sesión de cat_b, el usuario agrega un nuevo término a cat_a (que alfabéticamente va antes)
        t_a_new = self.repo.add_term(
            cat_a.id,
            NewTermDraft("DRY", "Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO, "def", "analog", "trade"),
            today=today,
        )

        # El enfoque NO debe saltar a cat_a a mitad de sesión: debe terminar cat_b primero
        focus_mid = self.service.active_focus(today=today)
        self.assertEqual(focus_mid["category_id"], cat_b.id, "La sesión activa en cat_b no debe ser interrumpida")

        # Una vez que termina el último término pendiente de cat_b (t_b2), ahora sí pasa a cat_a para estudiar DRY
        self.service.review(t_b2.id, Rating.MASTERED, now= datetime(2026, 10, 17, 10, 5, 0))
        focus_after = self.service.active_focus(today=today)
        self.assertEqual(focus_after["category_id"], cat_a.id)
        self.assertEqual(self.service.due_cards(today=today, limit=5)[0].term.id, t_a_new.id)

    def test_quantitative_mastery_indicator_and_spaced_return(self):
        today = date(2026, 10, 17)
        cat = self.repo.get_or_create_category("Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO)
        t1 = self.repo.add_term(
            cat.id,
            NewTermDraft("SOLID", "Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO, "def", "analog", "trade"),
            today=today,
        )

        # Etapa 0: Nuevo (0%)
        m0 = self.service.category_mastery(cat.id, today=today)
        self.assertEqual(m0["mastery_score"], 0.0)
        self.assertFalse(m0["is_canonically_mastered"])

        # Etapa 1: 1er repaso Dominado -> 1 día (30% - En Adquisición, pero hoy ya está despejada)
        s1 = self.service.review(t1.id, Rating.MASTERED, now=datetime(2026, 10, 17, 9, 0, 0))
        self.assertEqual(s1.interval_days, 1)
        m1 = self.service.category_mastery(cat.id, today=today)
        self.assertEqual(m1["mastery_score"], 30.0)
        self.assertTrue(m1["is_session_cleared"])
        self.assertFalse(m1["is_canonically_mastered"])

        # Al día siguiente (día +1), la categoría regresa automáticamente al foco activo por repetición espaciada
        day_2 = date(2026, 10, 18)
        self.assertEqual(self.service.active_focus(today=day_2)["category_id"], cat.id)
        s2 = self.service.review(t1.id, Rating.MASTERED, now=datetime(2026, 10, 18, 9, 0, 0))
        self.assertEqual(s2.interval_days, 6)

        # 6 días después (día +7), vuelve al foco y al dominarse salta a ~16 días (70% - En Consolidación)
        day_8 = date(2026, 10, 24)
        self.assertEqual(self.service.active_focus(today=day_8)["category_id"], cat.id)
        s3 = self.service.review(t1.id, Rating.MASTERED, now=datetime(2026, 10, 24, 9, 0, 0))
        self.assertGreaterEqual(s3.interval_days, 15)
        m3 = self.service.category_mastery(cat.id, today=day_8)
        self.assertEqual(m3["mastery_score"], 70.0)

        # En el siguiente repaso supera el umbral de 21 días -> 100% Maestría Canónica
        day_24 = date(2026, 11, 9)
        s4 = self.service.review(t1.id, Rating.MASTERED, now=datetime(2026, 11, 9, 9, 0, 0))
        self.assertGreaterEqual(s4.interval_days, 21)
        m4 = self.service.category_mastery(cat.id, today=day_24)
        self.assertEqual(m4["mastery_score"], 100.0)
        self.assertTrue(m4["is_canonically_mastered"])
        self.assertEqual(m4["status_label"], "🏆 Maestría Canónica")

    def test_settings_and_record_management_endpoints(self):
        class FakeEnricher:
            available = True

            def enrich(self, term, categories, examples=(), target_scope=None, exclude_categories=()):
                return NewTermDraft(
                    term=term,
                    macro_area="Ingeniería de Software",
                    category_name=target_scope or "Arquitectura de Software",
                    category_description="Descripción canónica actualizada",
                    zoom_level=ZoomLevel.MACRO,
                    technical_definition="Definición técnica regenerada por IA con máxima precisión terminológica.",
                    daily_analogy="Analogía regenerada.",
                    tradeoff_when_not_to_use="Trade-off regenerado.",
                    other_scopes=("Derecho", "Economía", "Biología", "Redes"),
                )

        self.service.enricher = FakeEnricher()

        # 1. GET /api/settings y PUT /api/settings
        get_cfg = self.client.get("/api/settings")
        self.assertEqual(get_cfg.status_code, 200)
        self.assertEqual(get_cfg.json()["session_card_limit"], 5)

        put_cfg = self.client.put(
            "/api/settings",
            json={
                "session_card_limit": 8,
                "maturity_threshold_days": 30,
                "initial_ease_factor": 2.7,
                "focused_mode": False,
                "theme": "dark",
                "step_by_step_reveal": True,
            },
        )
        self.assertEqual(put_cfg.status_code, 200)
        self.assertEqual(put_cfg.json()["session_card_limit"], 8)
        self.assertEqual(put_cfg.json()["maturity_threshold_days"], 30)
        self.assertFalse(put_cfg.json()["focused_mode"])
        self.assertEqual(put_cfg.json()["theme"], "dark")
        self.assertTrue(put_cfg.json()["step_by_step_reveal"])

        # 2. Crear un término con versión antigua y luego regenerarlo vía POST /api/terms/{id}/reenrich
        cat = self.repo.get_or_create_category("Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO)
        t_old = self.repo.add_term(
            cat.id,
            NewTermDraft("Resiliencia", "Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO, "def corta", "analog corta", "trade corto"),
        )
        res_reenrich = self.client.post(f"/api/terms/{t_old.id}/reenrich", json={"scope": "Sistemas Distribuidos"})
        self.assertEqual(res_reenrich.status_code, 200)
        re_data = res_reenrich.json()["card"]
        self.assertIn("máxima precisión terminológica", re_data["technical_definition"])
        self.assertEqual(len(re_data["other_scopes"]), 4)

        # 3. Eliminar el registro vía DELETE /api/terms/{id}
        res_del = self.client.delete(f"/api/terms/{t_old.id}")
        self.assertEqual(res_del.status_code, 200)
        self.assertTrue(res_del.json()["deleted"])
        self.assertIsNone(self.repo.get_term(t_old.id))

    def test_auto_multi_scope_batch_capture_without_manual_buttons(self):
        from src.interfaces.webhook_routes import format_captured_term, keyboard_captured_term

        class MultiScopeFakeEnricher:
            available = True

            def enrich_all(self, term, categories, examples=(), target_scope=None, exclude_categories=()):
                return [
                    NewTermDraft(
                        term="Contraprestativo",
                        macro_area="Derecho",
                        category_name="Derecho Civil y Obligaciones",
                        category_description="Vínculos jurídicos patrimoniales",
                        zoom_level=ZoomLevel.MEDIO,
                        technical_definition="Acto o negocio jurídico que exige una prestación recíproca equivalente.",
                        daily_analogy="Un contrato de compraventa donde entregas dinero a cambio de un bien.",
                        tradeoff_when_not_to_use="No aplica en actos de liberalidad pura como la donación simple.",
                        other_scopes=("Economía y Finanzas", "Derecho Tributario"),
                    ),
                    NewTermDraft(
                        term="Contraprestativo",
                        macro_area="Economía",
                        category_name="Economía y Finanzas",
                        category_description="Intercambio de bienes y flujos",
                        zoom_level=ZoomLevel.MACRO,
                        technical_definition="Transacción económica bilateral con flujo de valor en ambos sentidos.",
                        daily_analogy="Pagar una suscripción mensual por acceso a un servicio.",
                        tradeoff_when_not_to_use="No aplica en transferencias unilaterales o subsidios.",
                        other_scopes=("Derecho Civil y Obligaciones", "Derecho Tributario"),
                    ),
                    NewTermDraft(
                        term="Contraprestativo",
                        macro_area="Derecho",
                        category_name="Derecho Tributario",
                        category_description="Tributos vinculados y tasas",
                        zoom_level=ZoomLevel.MEDIO,
                        technical_definition="Naturaleza de las tasas y precios públicos frente a los impuestos no vinculados.",
                        daily_analogy="Pagar un peaje específico por usar una autopista.",
                        tradeoff_when_not_to_use="No aplica a impuestos generales como el Impuesto a la Renta.",
                        other_scopes=("Derecho Civil y Obligaciones", "Economía y Finanzas"),
                    ),
                ]

        self.service.enricher = MultiScopeFakeEnricher()
        result = self.service.capture_term("Contraprestativo")
        self.assertTrue(result.created)
        self.assertEqual(len(result.created_cards), 3)
        self.assertEqual(len(result.existing_cards), 3)
        self.assertEqual(result.suggested_scopes, ())
        # Telegram jamás debe mostrar botones manuales de "➕ Ámbito"
        self.assertIsNone(keyboard_captured_term(result))
        msg_html = format_captured_term(result)
        self.assertIn("3 ÁMBITOS", msg_html)
        self.assertIn("Derecho Civil y Obligaciones", msg_html)
        self.assertIn("Economía y Finanzas", msg_html)
        self.assertIn("Derecho Tributario", msg_html)

    def test_active_category_filter_and_no_focus_hijack_by_alphabetical_area(self):
        today = date(2026, 10, 17)
        # Usuario tiene primero Ingeniería de Software (id=1) y luego agrega Derecho (id=2, 'D' < 'I')
        cat_sw = self.repo.get_or_create_category("Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO)
        t_sw = self.repo.add_term(
            cat_sw.id,
            NewTermDraft("Cohesión", "Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO, "def", "analog", "trade"),
            today=today,
        )
        cat_law = self.repo.get_or_create_category("Derecho", "Derecho Civil", "Leyes", ZoomLevel.MEDIO)
        t_law = self.repo.add_term(
            cat_law.id,
            NewTermDraft("Contraprestativo", "Derecho", "Derecho Civil", "Leyes", ZoomLevel.MEDIO, "def", "analog", "trade"),
            today=today,
        )

        # Aunque 'Derecho' empieza con D (antes que 'I'), el foco activo respeta el orden del currículo (cat_sw primero)
        focus = self.service.active_focus(today=today)
        self.assertEqual(focus["category_id"], cat_sw.id)

        # Si el usuario en Configuración elige estudiar SOLO Derecho (active_category_ids = [cat_law.id]), el foco cambia a Derecho
        self.service.update_settings(active_category_ids=[cat_law.id])
        focus_filtered = self.service.active_focus(today=today)
        self.assertEqual(focus_filtered["category_id"], cat_law.id)
        due_filtered = self.service.due_cards(today=today, limit=5)
        self.assertEqual([c.term.id for c in due_filtered], [t_law.id])

    def test_canonical_cache_zero_ai_cost_and_daily_quota_and_roles(self):
        ai_calls = {"count": 0}

        class CountingEnricher:
            available = True

            def enrich_all(self, term, categories, examples=(), target_scope=None, exclude_categories=()):
                ai_calls["count"] += 1
                return [
                    NewTermDraft(
                        term=term,
                        macro_area="Ingeniería de Software",
                        category_name="Arquitectura",
                        category_description="Desc",
                        zoom_level=ZoomLevel.MACRO,
                        technical_definition="Def canónica",
                        daily_analogy="Analogía",
                        tradeoff_when_not_to_use="Trade-off",
                    )
                ]

        self.service.enricher = CountingEnricher()

        # 1. Primera captura de "CQRS" llama a la IA (ai_calls = 1)
        r1 = self.client.post("/api/terms", json={"term": "CQRS"})
        self.assertEqual(r1.status_code, 201)
        self.assertTrue(r1.json()["created"])
        self.assertEqual(ai_calls["count"], 1)

        # 2. Segunda captura de "CQRS" se sirve del Caché Canónico ($0 IA, ai_calls sigue en 1)
        r2 = self.client.post("/api/terms", json={"term": "CQRS"})
        self.assertEqual(r2.status_code, 201)
        self.assertFalse(r2.json()["created"])
        self.assertTrue(r2.json()["from_canonical_cache"])
        self.assertEqual(ai_calls["count"], 1)

        # 3. Crear usuario regular con cuota diaria = 1 y PIN propio
        from src.config import settings
        settings.WEB_ACCESS_PIN = "admin_pin_999"
        u_res = self.client.post(
            "/api/admin/users",
            headers={"X-Web-Pin": "admin_pin_999"},
            json={
                "display_name": "Estudiante Invitado",
                "telegram_chat_id": "555666",
                "web_pin": "user_pin_111",
                "role": "user",
                "daily_ai_limit": 1,
                "is_approved": True,
                "seed_all_terms": True,
            },
        )
        self.assertEqual(u_res.status_code, 200)

        # Usuario regular NO puede regenerar el catálogo global con IA (403 Forbidden)
        card_id = r1.json()["card"]["id"]
        forbidden_reenrich = self.client.post(
            f"/api/terms/{card_id}/reenrich",
            headers={"X-Web-Pin": "user_pin_111"},
            json={"scope": "Otro"},
        )
        self.assertEqual(forbidden_reenrich.status_code, 403)

        # Usuario regular al eliminar solo desvincula de su mazo; el término sigue existiendo en el Catálogo Global
        unlink_res = self.client.delete(f"/api/terms/{card_id}", headers={"X-Web-Pin": "user_pin_111"})
        self.assertEqual(unlink_res.status_code, 200)
        self.assertTrue(unlink_res.json()["unlinked_only"])
        self.assertIsNotNone(self.repo.get_term(card_id))

        # Usuario regular consume su única cuota diaria IA (1/1) -> 201 Created
        q1 = self.client.post("/api/terms", headers={"X-Web-Pin": "user_pin_111"}, json={"term": "Saga Pattern"})
        self.assertEqual(q1.status_code, 201)

        # Segundo término nuevo con IA el mismo día supera su cuota (1/1) -> 429 Too Many Requests
        q2 = self.client.post("/api/terms", headers={"X-Web-Pin": "user_pin_111"}, json={"term": "Outbox Pattern"})
        self.assertEqual(q2.status_code, 429)

    def test_business_intelligence_and_csv_export_endpoints(self):
        cat = self.repo.get_or_create_category("Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO)
        self.repo.add_term(
            cat.id,
            NewTermDraft("Idempotencia", "Ingeniería de Software", "1. Principios", "Base", ZoomLevel.MICRO, "def", "analog", "trade"),
        )
        bi_res = self.client.get("/api/admin/bi")
        self.assertEqual(bi_res.status_code, 200)
        self.assertIn("hardest_terms", bi_res.json())
        self.assertIn("area_breakdown", bi_res.json())

        csv_res = self.client.get("/api/admin/bi/export-csv")
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn("text/csv", csv_res.headers["content-type"])
        self.assertIn("Idempotencia", csv_res.text)


if __name__ == "__main__":
    unittest.main()

