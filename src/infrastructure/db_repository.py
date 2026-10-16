"""Acceso a PostgreSQL (Cloud-native para Supabase, Render, Neon, etc.).

Reglas de negocio garantizadas aquí (y reforzadas con triggers a nivel de PostgreSQL):
  * `terms` es APPEND-ONLY: no existe ningún método DELETE y la BD aborta cualquier DELETE.
  * Ningún campo ya escrito de `terms` puede sobrescribirse (solo rellenar campos NULL).
  * `categories` almacena `simple_explanation` y `zoom_level`.
  * `srs_state` almacena el progreso SM-2.
  * `review_log` almacena el histórico append-only.
"""
from __future__ import annotations

import os
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator, Optional

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from src.domain.models import (
    Category, NewTermDraft, Rating, ReviewLog, SRSState, Term, ZoomLevel,
)


class TermAlreadyExistsError(Exception):
    def __init__(self, term: Term):
        super().__init__(f"El término '{term.term}' ya existe (id={term.id}).")
        self.term = term


_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id                 SERIAL PRIMARY KEY,
    macro_area         VARCHAR(150) NOT NULL,
    name               VARCHAR(150) NOT NULL,
    simple_explanation TEXT NOT NULL,
    zoom_level         INTEGER NOT NULL CHECK (zoom_level BETWEEN 1 AND 4),
    CONSTRAINT uq_categories_area_name UNIQUE (macro_area, name)
);

CREATE TABLE IF NOT EXISTS terms (
    id                       SERIAL PRIMARY KEY,
    category_id              INTEGER NOT NULL REFERENCES categories(id),
    term                     VARCHAR(200) NOT NULL UNIQUE,
    technical_definition     TEXT NOT NULL,
    daily_analogy            TEXT NOT NULL,
    tradeoff_when_not_to_use TEXT,
    created_at               TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS srs_state (
    term_id          INTEGER PRIMARY KEY REFERENCES terms(id),
    interval_days    INTEGER NOT NULL DEFAULT 0,
    ease_factor      REAL NOT NULL DEFAULT 2.5,
    repetitions      INTEGER NOT NULL DEFAULT 0,
    next_review_date DATE NOT NULL DEFAULT CURRENT_DATE,
    last_reviewed_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS review_log (
    id                SERIAL PRIMARY KEY,
    term_id           INTEGER NOT NULL REFERENCES terms(id),
    rating            INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 3),
    reviewed_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    interval_before   INTEGER NOT NULL,
    interval_after    INTEGER NOT NULL,
    ease_factor_after REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_srs_next_review ON srs_state(next_review_date);

-- =========================================================================
-- Inmutabilidad reforzada con funciones y triggers nativos de PostgreSQL
-- =========================================================================
CREATE OR REPLACE FUNCTION trg_terms_prevent_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'terms es append-only: DELETE estrictamente prohibido';
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_terms_no_delete'
    ) THEN
        CREATE TRIGGER trg_terms_no_delete
        BEFORE DELETE ON terms
        FOR EACH ROW EXECUTE FUNCTION trg_terms_prevent_delete();
    END IF;
END $$;

CREATE OR REPLACE FUNCTION trg_terms_prevent_destructive_update()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id <> OLD.id
       OR NEW.term <> OLD.term
       OR NEW.category_id <> OLD.category_id
       OR NEW.created_at <> OLD.created_at
       OR NEW.technical_definition <> OLD.technical_definition
       OR NEW.daily_analogy <> OLD.daily_analogy
       OR (OLD.tradeoff_when_not_to_use IS NOT NULL AND NEW.tradeoff_when_not_to_use IS DISTINCT FROM OLD.tradeoff_when_not_to_use) THEN
        RAISE EXCEPTION 'terms es inmutable: no se permite sobrescribir datos existentes';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_terms_no_destructive_update'
    ) THEN
        CREATE TRIGGER trg_terms_no_destructive_update
        BEFORE UPDATE ON terms
        FOR EACH ROW EXECUTE FUNCTION trg_terms_prevent_destructive_update();
    END IF;
END $$;

CREATE OR REPLACE FUNCTION trg_categories_prevent_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'categories es append-only: DELETE prohibido';
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_categories_no_delete'
    ) THEN
        CREATE TRIGGER trg_categories_no_delete
        BEFORE DELETE ON categories
        FOR EACH ROW EXECUTE FUNCTION trg_categories_prevent_delete();
    END IF;
END $$;
"""


class PostgresGlossaryRepository:
    """Repositorio PostgreSQL con conexión segura vía DATABASE_URL."""

    def __init__(self, database_url: Optional[str] = None):
        url = database_url or os.getenv("DATABASE_URL")
        if not url:
            raise ValueError(
                "DATABASE_URL no configurada. Define la URL de conexión a PostgreSQL en tu .env o entorno de nube."
            )
        # Normalizar prefijo postgres:// a postgresql:// para psycopg2
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        self.database_url = url

    @contextmanager
    def _connect(self) -> Iterator[psycopg2.extensions.connection]:
        conn = psycopg2.connect(self.database_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_PG_SCHEMA)

    # ------------------------------------------------------------- Mappers
    @staticmethod
    def _to_category(r: dict) -> Category:
        desc = r.get("simple_explanation") or r.get("description") or ""
        return Category(
            id=r["id"],
            macro_area=r["macro_area"],
            name=r["name"],
            description=desc,
            zoom_level=ZoomLevel(r["zoom_level"]),
        )

    @staticmethod
    def _to_term(r: dict) -> Term:
        c_at = r["created_at"]
        if isinstance(c_at, str):
            c_at = datetime.fromisoformat(c_at)
        return Term(
            id=r["id"],
            category_id=r["category_id"],
            term=r["term"],
            technical_definition=r["technical_definition"],
            daily_analogy=r["daily_analogy"],
            tradeoff_when_not_to_use=r["tradeoff_when_not_to_use"],
            created_at=c_at,
        )

    @staticmethod
    def _to_srs(r: dict) -> SRSState:
        nxt = r["next_review_date"]
        if isinstance(nxt, str):
            nxt = date.fromisoformat(nxt)
        last_rev = r.get("last_reviewed_at")
        if isinstance(last_rev, str):
            last_rev = datetime.fromisoformat(last_rev)
        return SRSState(
            term_id=r["term_id"],
            interval_days=r["interval_days"],
            ease_factor=r["ease_factor"],
            repetitions=r["repetitions"],
            next_review_date=nxt,
            last_reviewed_at=last_rev,
        )

    # ---------------------------------------------------------- Categories
    def list_categories(self) -> list[Category]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM categories ORDER BY macro_area, zoom_level, id")
                rows = cur.fetchall()
        return [self._to_category(r) for r in rows]

    def get_category(self, category_id: int) -> Optional[Category]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
                r = cur.fetchone()
        return self._to_category(r) if r else None

    def get_or_create_category(
        self, macro_area: str, name: str, description: str, zoom_level: ZoomLevel
    ) -> Category:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM categories WHERE LOWER(macro_area) = LOWER(%s) AND LOWER(name) = LOWER(%s)",
                    (macro_area, name),
                )
                r = cur.fetchone()
                if r:
                    return self._to_category(r)

                cur.execute(
                    """INSERT INTO categories (macro_area, name, simple_explanation, zoom_level)
                       VALUES (%s, %s, %s, %s)
                       RETURNING *""",
                    (macro_area.strip(), name.strip(), description.strip(), int(zoom_level)),
                )
                r = cur.fetchone()
        return self._to_category(r)

    def count_terms_by_category(self) -> dict[int, int]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT category_id, COUNT(*) AS n FROM terms GROUP BY category_id")
                rows = cur.fetchall()
        return {r["category_id"]: r["n"] for r in rows}

    # --------------------------------------------------------------- Terms
    @staticmethod
    def normalize_name(name: str) -> str:
        decomposed = unicodedata.normalize("NFKD", name)
        no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        return " ".join(no_accents.casefold().split())

    def find_term_by_name(self, name: str) -> Optional[Term]:
        key = self.normalize_name(name)
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM terms")
                rows = cur.fetchall()
        for r in rows:
            if self.normalize_name(r["term"]) == key:
                return self._to_term(r)
        return None

    def get_term(self, term_id: int) -> Optional[Term]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM terms WHERE id = %s", (term_id,))
                r = cur.fetchone()
        return self._to_term(r) if r else None

    def list_terms(self, category_id: Optional[int] = None) -> list[Term]:
        query = "SELECT * FROM terms"
        params = ()
        if category_id is not None:
            query += " WHERE category_id = %s"
            params = (category_id,)
        query += " ORDER BY id"
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        return [self._to_term(r) for r in rows]

    def add_term(self, category_id: int, draft: NewTermDraft, today: Optional[date] = None) -> Term:
        existing = self.find_term_by_name(draft.term)
        if existing:
            raise TermAlreadyExistsError(existing)

        today_date = today or date.today()
        tradeoff = draft.tradeoff_when_not_to_use.strip() if draft.tradeoff_when_not_to_use else None

        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO terms (category_id, term, technical_definition, daily_analogy, tradeoff_when_not_to_use)
                       VALUES (%s, %s, %s, %s, %s)
                       RETURNING *""",
                    (category_id, draft.term.strip(), draft.technical_definition.strip(),
                     draft.daily_analogy.strip(), tradeoff),
                )
                t_row = cur.fetchone()
                term_id = t_row["id"]

                cur.execute(
                    """INSERT INTO srs_state (term_id, next_review_date)
                       VALUES (%s, %s)""",
                    (term_id, today_date),
                )
        return self._to_term(t_row)

    def fill_missing_tradeoff(self, term_id: int, tradeoff: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE terms
                       SET tradeoff_when_not_to_use = %s
                       WHERE id = %s AND tradeoff_when_not_to_use IS NULL""",
                    (tradeoff.strip(), term_id),
                )
                return cur.rowcount == 1

    def count_terms(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM terms")
                return cur.fetchone()[0]

    # ----------------------------------------------------------------- SRS
    def get_srs_state(self, term_id: int) -> Optional[SRSState]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM srs_state WHERE term_id = %s", (term_id,))
                r = cur.fetchone()
        return self._to_srs(r) if r else None

    def list_srs_states(self) -> dict[int, SRSState]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM srs_state")
                rows = cur.fetchall()
        return {r["term_id"]: self._to_srs(r) for r in rows}

    def save_srs_state(self, s: SRSState) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE srs_state
                       SET interval_days = %s,
                           ease_factor = %s,
                           repetitions = %s,
                           next_review_date = %s,
                           last_reviewed_at = %s
                       WHERE term_id = %s""",
                    (s.interval_days, s.ease_factor, s.repetitions, s.next_review_date,
                     s.last_reviewed_at, s.term_id),
                )

    def list_due_term_ids(self, today: date, limit: Optional[int] = None) -> list[int]:
        query = """SELECT term_id FROM srs_state
                   WHERE next_review_date <= %s
                   ORDER BY next_review_date, ease_factor, term_id"""
        params = [today]
        if limit:
            query += " LIMIT %s"
            params.append(limit)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
        return [r[0] for r in rows]

    def count_due(self, today: date) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM srs_state WHERE next_review_date <= %s", (today,))
                return cur.fetchone()[0]

    # ---------------------------------------------------------- Review Log
    def append_review_log(self, log: ReviewLog) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO review_log (term_id, rating, reviewed_at, interval_before, interval_after, ease_factor_after)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (log.term_id, int(log.rating), log.reviewed_at, log.interval_before,
                     log.interval_after, log.ease_factor_after),
                )

    def count_reviews_on(self, day: date) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM review_log WHERE DATE(reviewed_at) = %s",
                    (day,),
                )
                return cur.fetchone()[0]
