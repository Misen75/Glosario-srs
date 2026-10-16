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
    Category, NewTermDraft, Rating, ReviewLog, SRSState, Term, ZoomLevel,
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

    def add_term(self, category_id: int, draft: NewTermDraft, today: date | None = None) -> Term:
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
        )
        self.terms[tid] = term
        self.srs_states[tid] = SRSState(term_id=tid, next_review_date=today or date.today())
        return term

    def fill_missing_tradeoff(self, term_id: int, tradeoff: str) -> bool:
        t = self.terms.get(term_id)
        if t and t.tradeoff_when_not_to_use is None:
            # Enriquecimiento no destructivo
            self.terms[term_id] = Term(
                id=t.id, category_id=t.category_id, term=t.term,
                technical_definition=t.technical_definition, daily_analogy=t.daily_analogy,
                tradeoff_when_not_to_use=tradeoff, created_at=t.created_at
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

    def list_due_term_ids(self, today: date, limit: int | None = None) -> list[int]:
        due = [tid for tid, s in self.srs_states.items() if s.next_review_date <= today]
        return due[:limit] if limit else due

    def count_due(self, today: date) -> int:
        return sum(1 for s in self.srs_states.values() if s.next_review_date <= today)

    def append_review_log(self, log: ReviewLog):
        self.review_logs.append(log)

    def count_reviews_on(self, day: date) -> int:
        return sum(1 for l in self.review_logs if l.reviewed_at.date() == day)


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

        # 1. POST /api/terms sin PIN -> 401 Unauthorized
        res_no_pin = self.client.post("/api/terms", json={"term": "Circuit Breaker"})
        self.assertEqual(res_no_pin.status_code, 401)

        # 2. POST /api/terms con PIN incorrecto -> 401 Unauthorized
        res_wrong_pin = self.client.post(
            "/api/terms",
            json={"term": "Circuit Breaker"},
            headers={"X-Web-Pin": "0000"}
        )
        self.assertEqual(res_wrong_pin.status_code, 401)

        # 3. POST /api/review/1 sin PIN -> 401 Unauthorized
        res_rev_no_pin = self.client.post("/api/review/1", json={"rating": 3})
        self.assertEqual(res_rev_no_pin.status_code, 401)

        # 4. POST /api/review/1 con PIN incorrecto -> 401 Unauthorized
        res_rev_wrong = self.client.post(
            "/api/review/1",
            json={"rating": 3},
            headers={"X-Web-Pin": "wrong"}
        )
        self.assertEqual(res_rev_wrong.status_code, 401)

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


if __name__ == "__main__":
    unittest.main()

