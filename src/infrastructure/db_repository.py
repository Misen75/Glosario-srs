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
    DEFAULT_LIMITS_LABEL, DEFAULT_ZOOM_LABELS, Category, MacroArea, NewTermDraft, Rating, ReviewLog,
    SRSState, Term, UserTermCustomization, ZoomLevel,
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
    term                     VARCHAR(200) NOT NULL,
    technical_definition     TEXT NOT NULL,
    daily_analogy            TEXT NOT NULL,
    tradeoff_when_not_to_use TEXT,
    other_scopes             TEXT[] NOT NULL DEFAULT '{}',
    created_at               TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_terms_category_term UNIQUE (category_id, term)
);

ALTER TABLE terms ADD COLUMN IF NOT EXISTS other_scopes TEXT[] NOT NULL DEFAULT '{}';

-- Trazabilidad epistemológica (Fuente Cero) y topología ontológica del término
ALTER TABLE terms ADD COLUMN IF NOT EXISTS canonical_source TEXT NOT NULL DEFAULT '';
ALTER TABLE terms ADD COLUMN IF NOT EXISTS parent_term VARCHAR(200);
ALTER TABLE terms ADD COLUMN IF NOT EXISTS related_terms TEXT[] NOT NULL DEFAULT '{}';

-- Entidad normalizada de disciplinas con descriptores de granularidad ontológica propios
CREATE TABLE IF NOT EXISTS macro_areas (
    id           SERIAL PRIMARY KEY,
    name         VARCHAR(150) NOT NULL UNIQUE,
    description  TEXT NOT NULL DEFAULT '',
    zoom_1_label VARCHAR(120) NOT NULL DEFAULT 'Micro · Unidad elemental',
    zoom_2_label VARCHAR(120) NOT NULL DEFAULT 'Medio · Componente',
    zoom_3_label VARCHAR(120) NOT NULL DEFAULT 'Macro · Estructura',
    zoom_4_label VARCHAR(120) NOT NULL DEFAULT 'Fundamentos · Doctrina e infraestructura',
    limits_label VARCHAR(150) NOT NULL DEFAULT 'Compensaciones y Anti-patrones (Trade-offs)',
    updated_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

ALTER TABLE macro_areas ADD COLUMN IF NOT EXISTS limits_label VARCHAR(150) NOT NULL DEFAULT 'Compensaciones y Anti-patrones (Trade-offs)';

INSERT INTO macro_areas (name)
SELECT DISTINCT macro_area FROM categories
ON CONFLICT (name) DO NOTHING;

-- Semilla canónica de descriptores de granularidad ontológica y límites por disciplina
INSERT INTO macro_areas (name, description, zoom_1_label, zoom_2_label, zoom_3_label, zoom_4_label, limits_label)
VALUES
    ('Diseño y Arquitectura de Software', 'Principios de construcción, estructura modular, topologías y estilos arquitectónicos de sistemas de software.', 'Micro · Código, Clases y Funciones', 'Medio · Módulos y Componentes', 'Macro · Arquitectura y Topología', 'Fundamentos · Paradigmas y Atributos de Calidad', 'Compensaciones y Anti-patrones (Trade-offs)'),
    ('Ingeniería de Software', 'Disciplina sistemática para el desarrollo, operación, aseguramiento de calidad y mantenimiento de software.', 'Micro · Prácticas de Codificación y Pruebas Unitarias', 'Medio · Artefactos, Pipelines y Servicios', 'Macro · Ciclo de Vida y Gobierno Técnico', 'Fundamentos · Estándares IEEE/ISO y Metodologías', 'Costos de Adopción y Escenarios Inadecuados'),
    ('Ciencias de la Computación', 'Fundamentos matemáticos, algorítmicos, teóricos y sistémicos del cómputo y procesamiento de información.', 'Micro · Primitivas, Tipos y Operaciones Elementales', 'Medio · Estructuras de Datos y Algoritmos', 'Macro · Sistemas Operativos, Redes y Distribución', 'Fundamentos · Teoría de la Computación y Complejidad', 'Límites de Aplicabilidad y Condiciones de Fallo'),
    ('Derecho', 'Ordenamiento normativo e institucional que regula la conducta humana en sociedad y la estructura del Estado.', 'Micro · Norma Jurídica, Artículo y Figura Legal', 'Medio · Institución Jurídica y Procedimiento', 'Macro · Rama del Derecho y Código Normativo', 'Fundamentos · Doctrina, Jurisprudencia y Teoría Constitucional', 'Supuestos de Excepción e Inaplicabilidad'),
    ('Economía', 'Ciencia social que estudia la asignación de recursos escasos, incentivos, mercados y agregados macroeconómicos.', 'Micro · Agente, Precio, Utilidad y Transacción', 'Medio · Mercados, Firmas e Instrumentos Financieros', 'Macro · Política Fiscal/Monetaria y Agregados Nacionales', 'Fundamentos · Teoría Económica, Escuelas y Modelos Axiomáticos', 'Restricciones Axiomáticas y Supuestos de Invalidez'),
    ('Ciencias Sociales', 'Estudio sistemático de las estructuras sociales, instituciones, cultura, poder e interacciones humanas.', 'Micro · Acción Social, Sujeto y Práctica', 'Medio · Grupos, Instituciones y Organizaciones', 'Macro · Estructura Social, Estado y Sistemas Culturales', 'Fundamentos · Epistemología y Teoría Social Clásica/Contemporánea', 'Críticas, Sesgos y Límites del Paradigma')
ON CONFLICT (name) DO UPDATE SET
    description  = CASE WHEN macro_areas.description = '' THEN EXCLUDED.description ELSE macro_areas.description END,
    zoom_1_label = CASE WHEN macro_areas.zoom_1_label = 'Micro · Unidad elemental' THEN EXCLUDED.zoom_1_label ELSE macro_areas.zoom_1_label END,
    zoom_2_label = CASE WHEN macro_areas.zoom_2_label = 'Medio · Componente' THEN EXCLUDED.zoom_2_label ELSE macro_areas.zoom_2_label END,
    zoom_3_label = CASE WHEN macro_areas.zoom_3_label = 'Macro · Estructura' THEN EXCLUDED.zoom_3_label ELSE macro_areas.zoom_3_label END,
    zoom_4_label = CASE WHEN macro_areas.zoom_4_label = 'Fundamentos · Doctrina e infraestructura' THEN EXCLUDED.zoom_4_label ELSE macro_areas.zoom_4_label END,
    limits_label = CASE WHEN macro_areas.limits_label = 'Compensaciones y Anti-patrones (Trade-offs)' THEN EXCLUDED.limits_label ELSE macro_areas.limits_label END;

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

CREATE TABLE IF NOT EXISTS user_settings (
    id                      INTEGER PRIMARY KEY DEFAULT 1,
    session_card_limit      INTEGER NOT NULL DEFAULT 5 CHECK (session_card_limit BETWEEN 1 AND 50),
    maturity_threshold_days INTEGER NOT NULL DEFAULT 21 CHECK (maturity_threshold_days BETWEEN 7 AND 180),
    initial_ease_factor     REAL NOT NULL DEFAULT 2.5 CHECK (initial_ease_factor BETWEEN 1.3 AND 4.0),
    focused_mode            BOOLEAN NOT NULL DEFAULT TRUE,
    theme                   VARCHAR(20) NOT NULL DEFAULT 'light',
    step_by_step_reveal     BOOLEAN NOT NULL DEFAULT FALSE,
    active_category_ids     INTEGER[] NOT NULL DEFAULT '{}',
    updated_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

ALTER TABLE user_settings DROP CONSTRAINT IF EXISTS user_settings_id_check;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS active_category_ids INTEGER[] NOT NULL DEFAULT '{}';
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS excluded_term_ids INTEGER[] NOT NULL DEFAULT '{}';

INSERT INTO user_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id               SERIAL PRIMARY KEY,
    telegram_chat_id VARCHAR(64) UNIQUE,
    display_name     VARCHAR(120) NOT NULL DEFAULT 'Usuario',
    role             VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    web_pin          VARCHAR(128),
    daily_ai_limit   INTEGER NOT NULL DEFAULT 10,
    ai_calls_today   INTEGER NOT NULL DEFAULT 0,
    ai_calls_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    is_approved      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

INSERT INTO users (id, display_name, role, daily_ai_limit, is_approved)
VALUES (1, 'Administrador Principal', 'admin', 9999, TRUE)
ON CONFLICT (id) DO UPDATE SET role = 'admin', is_approved = TRUE;

SELECT setval(pg_get_serial_sequence('users', 'id'), GREATEST((SELECT COALESCE(MAX(id), 1) FROM users), 1));

CREATE TABLE IF NOT EXISTS user_srs_state (
    user_id          INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
    term_id          INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    interval_days    INTEGER NOT NULL DEFAULT 0,
    ease_factor      REAL NOT NULL DEFAULT 2.5,
    repetitions      INTEGER NOT NULL DEFAULT 0,
    next_review_date DATE NOT NULL DEFAULT CURRENT_DATE,
    last_reviewed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (user_id, term_id)
);

CREATE INDEX IF NOT EXISTS idx_user_srs_next_review ON user_srs_state(user_id, next_review_date);

INSERT INTO user_srs_state (user_id, term_id, interval_days, ease_factor, repetitions, next_review_date, last_reviewed_at)
SELECT 1, term_id, interval_days, ease_factor, repetitions, next_review_date, last_reviewed_at
FROM srs_state
ON CONFLICT (user_id, term_id) DO NOTHING;

ALTER TABLE review_log ADD COLUMN IF NOT EXISTS user_id INTEGER NOT NULL DEFAULT 1;

-- =========================================================================
-- Capa de Personalización del Estudiante (Copy-on-Write) y Pipeline de Arbitraje Canónico
-- =========================================================================
CREATE TABLE IF NOT EXISTS user_term_customizations (
    user_id                INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    term_id                INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    custom_definition      TEXT,
    custom_analogy         TEXT,
    custom_limits          TEXT,
    personal_notes         TEXT,
    status                 VARCHAR(50) NOT NULL DEFAULT 'private',
    proposal_justification TEXT,
    proposal_source        TEXT,
    arbitration_score      REAL,
    arbitration_verdict    TEXT,
    updated_at             TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, term_id)
);

CREATE INDEX IF NOT EXISTS idx_user_customizations_user ON user_term_customizations(user_id);
CREATE INDEX IF NOT EXISTS idx_user_customizations_pending ON user_term_customizations(status) WHERE status = 'proposed_to_canonical';

-- =========================================================================
-- Inmutabilidad reforzada con funciones y triggers nativos de PostgreSQL
-- (Permite mutación administrativa explícita dentro de una transacción con
--  SET LOCAL glosario.allow_admin_mutation = '1')
-- =========================================================================
CREATE OR REPLACE FUNCTION trg_terms_prevent_delete()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('glosario.allow_admin_mutation', true) = '1' THEN
        RETURN OLD;
    END IF;
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
    IF current_setting('glosario.allow_admin_mutation', true) = '1' THEN
        RETURN NEW;
    END IF;
    IF NEW.id <> OLD.id
       OR NEW.term <> OLD.term
       OR NEW.category_id <> OLD.category_id
       OR NEW.created_at <> OLD.created_at
       OR NEW.technical_definition <> OLD.technical_definition
       OR NEW.daily_analogy <> OLD.daily_analogy
       OR (OLD.tradeoff_when_not_to_use IS NOT NULL AND NEW.tradeoff_when_not_to_use IS DISTINCT FROM OLD.tradeoff_when_not_to_use)
       OR (OLD.other_scopes IS NOT NULL AND cardinality(OLD.other_scopes) > 0 AND NEW.other_scopes IS DISTINCT FROM OLD.other_scopes)
       OR (COALESCE(OLD.canonical_source, '') <> '' AND NEW.canonical_source IS DISTINCT FROM OLD.canonical_source)
       OR (OLD.parent_term IS NOT NULL AND OLD.parent_term <> '' AND NEW.parent_term IS DISTINCT FROM OLD.parent_term)
       OR (OLD.related_terms IS NOT NULL AND cardinality(OLD.related_terms) > 0 AND NEW.related_terms IS DISTINCT FROM OLD.related_terms) THEN
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
    def _to_macro_area(r: dict) -> MacroArea:
        z1 = (r.get("zoom_1_label") or DEFAULT_ZOOM_LABELS[0]).strip()
        z2 = (r.get("zoom_2_label") or DEFAULT_ZOOM_LABELS[1]).strip()
        z3 = (r.get("zoom_3_label") or DEFAULT_ZOOM_LABELS[2]).strip()
        z4 = (r.get("zoom_4_label") or DEFAULT_ZOOM_LABELS[3]).strip()
        lim = (r.get("limits_label") or DEFAULT_LIMITS_LABEL).strip()
        return MacroArea(
            id=int(r["id"]),
            name=str(r["name"]),
            description=str(r.get("description") or ""),
            zoom_labels=(z1, z2, z3, z4),
            limits_label=lim,
        )

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
        raw_scopes = r.get("other_scopes") or ()
        raw_related = r.get("related_terms") or ()
        parent_val = r.get("parent_term")
        return Term(
            id=r["id"],
            category_id=r["category_id"],
            term=r["term"],
            technical_definition=r["technical_definition"],
            daily_analogy=r["daily_analogy"],
            tradeoff_when_not_to_use=r["tradeoff_when_not_to_use"],
            created_at=c_at,
            other_scopes=tuple(raw_scopes),
            canonical_source=str(r.get("canonical_source") or ""),
            parent_term=str(parent_val).strip() if parent_val else None,
            related_terms=tuple(str(x).strip() for x in raw_related if x and str(x).strip()),
        )

    @staticmethod
    def _to_user_customization(r: dict) -> UserTermCustomization:
        u_at = r.get("updated_at")
        if isinstance(u_at, str):
            u_at = datetime.fromisoformat(u_at)
        return UserTermCustomization(
            user_id=int(r["user_id"]),
            term_id=int(r["term_id"]),
            custom_definition=r.get("custom_definition"),
            custom_analogy=r.get("custom_analogy"),
            custom_limits=r.get("custom_limits"),
            personal_notes=r.get("personal_notes"),
            status=str(r.get("status") or "private"),
            proposal_justification=r.get("proposal_justification"),
            proposal_source=r.get("proposal_source"),
            arbitration_score=float(r["arbitration_score"]) if r.get("arbitration_score") is not None else None,
            arbitration_verdict=r.get("arbitration_verdict"),
            updated_at=u_at,
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

    # --------------------------------------------------------- Macro-Areas
    def list_macro_areas(self) -> dict[str, MacroArea]:
        """Devuelve un diccionario indexado por nombre de macro-área con sus descriptores de granularidad ontológica y límites propios."""
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM macro_areas ORDER BY name ASC")
                    rows = cur.fetchall()
            return {r["name"]: self._to_macro_area(r) for r in rows}
        except Exception:
            return {}

    def upsert_macro_area(
        self,
        name: str,
        description: str = "",
        zoom_labels: Optional[tuple[str, str, str, str] | list[str]] = None,
        limits_label: Optional[str] = None,
    ) -> Optional[MacroArea]:
        clean_name = (name or "").strip()
        if not clean_name:
            return None
        clean_desc = (description or "").strip()
        labels = list(zoom_labels) if zoom_labels and len(zoom_labels) == 4 else list(DEFAULT_ZOOM_LABELS)
        z1 = (labels[0] or DEFAULT_ZOOM_LABELS[0]).strip()[:120]
        z2 = (labels[1] or DEFAULT_ZOOM_LABELS[1]).strip()[:120]
        z3 = (labels[2] or DEFAULT_ZOOM_LABELS[2]).strip()[:120]
        z4 = (labels[3] or DEFAULT_ZOOM_LABELS[3]).strip()[:120]
        clean_limits = (limits_label or DEFAULT_LIMITS_LABEL).strip()[:150]
        has_custom_labels = bool(zoom_labels and len(zoom_labels) == 4 and tuple(labels) != DEFAULT_ZOOM_LABELS)
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    if has_custom_labels or (limits_label and limits_label != DEFAULT_LIMITS_LABEL):
                        cur.execute(
                            """INSERT INTO macro_areas (name, description, zoom_1_label, zoom_2_label, zoom_3_label, zoom_4_label, limits_label, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                               ON CONFLICT (name) DO UPDATE SET
                                   description  = CASE WHEN EXCLUDED.description <> '' THEN EXCLUDED.description ELSE macro_areas.description END,
                                   zoom_1_label = CASE WHEN macro_areas.zoom_1_label = 'Micro · Unidad elemental' THEN EXCLUDED.zoom_1_label ELSE macro_areas.zoom_1_label END,
                                   zoom_2_label = CASE WHEN macro_areas.zoom_2_label = 'Medio · Componente' THEN EXCLUDED.zoom_2_label ELSE macro_areas.zoom_2_label END,
                                   zoom_3_label = CASE WHEN macro_areas.zoom_3_label = 'Macro · Estructura' THEN EXCLUDED.zoom_3_label ELSE macro_areas.zoom_3_label END,
                                   zoom_4_label = CASE WHEN macro_areas.zoom_4_label = 'Fundamentos · Doctrina e infraestructura' THEN EXCLUDED.zoom_4_label ELSE macro_areas.zoom_4_label END,
                                   limits_label = CASE WHEN macro_areas.limits_label = 'Compensaciones y Anti-patrones (Trade-offs)' THEN EXCLUDED.limits_label ELSE macro_areas.limits_label END,
                                   updated_at   = NOW()
                               RETURNING *""",
                            (clean_name, clean_desc, z1, z2, z3, z4, clean_limits),
                        )
                    else:
                        cur.execute(
                            """INSERT INTO macro_areas (name, description, updated_at)
                               VALUES (%s, %s, NOW())
                               ON CONFLICT (name) DO UPDATE SET
                                   description = CASE WHEN macro_areas.description = '' AND EXCLUDED.description <> '' THEN EXCLUDED.description ELSE macro_areas.description END
                               RETURNING *""",
                            (clean_name, clean_desc),
                        )
                    r = cur.fetchone()
                    return self._to_macro_area(r) if r else None
        except Exception:
            return None

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
                try:
                    cur.execute(
                        "INSERT INTO macro_areas (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                        (macro_area.strip(),),
                    )
                except Exception:
                    pass
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

    def find_terms_by_name(self, name: str) -> list[Term]:
        """Devuelve todas las acepciones registradas de un mismo término en distintas categorías."""
        key = self.normalize_name(name)
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM terms ORDER BY id")
                rows = cur.fetchall()
        return [self._to_term(r) for r in rows if self.normalize_name(r["term"]) == key]

    def find_term_by_name(self, name: str, category_id: Optional[int] = None) -> Optional[Term]:
        matches = self.find_terms_by_name(name)
        if category_id is not None:
            return next((t for t in matches if t.category_id == category_id), None)
        return matches[0] if matches else None

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

    def add_term(
        self,
        category_id: int,
        draft: NewTermDraft,
        today: Optional[date] = None,
        initial_ease_factor: float = 2.5,
        user_id: int = 1,
    ) -> Term:
        existing = self.find_term_by_name(draft.term, category_id=category_id)
        if existing:
            raise TermAlreadyExistsError(existing)

        today_date = today or date.today()
        tradeoff = draft.tradeoff_when_not_to_use.strip() if draft.tradeoff_when_not_to_use else None
        scopes_list = [s.strip() for s in (draft.other_scopes or ()) if s and s.strip()]
        canonical_src = (draft.canonical_source or "").strip()
        parent_val = draft.parent_term.strip() if draft.parent_term and draft.parent_term.strip() else None
        related_list = [r.strip() for r in (draft.related_terms or ()) if r and r.strip()]

        if draft.macro_area and (draft.macro_area_zoom_labels or draft.macro_area_description or getattr(draft, "macro_area_limits_label", None)):
            self.upsert_macro_area(
                name=draft.macro_area,
                description=draft.macro_area_description,
                zoom_labels=draft.macro_area_zoom_labels,
                limits_label=getattr(draft, "macro_area_limits_label", DEFAULT_LIMITS_LABEL),
            )

        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO terms (
                           category_id, term, technical_definition, daily_analogy,
                           tradeoff_when_not_to_use, other_scopes,
                           canonical_source, parent_term, related_terms
                       )
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING *""",
                    (
                        category_id,
                        draft.term.strip(),
                        draft.technical_definition.strip(),
                        draft.daily_analogy.strip(),
                        tradeoff,
                        scopes_list,
                        canonical_src,
                        parent_val,
                        related_list,
                    ),
                )
                t_row = cur.fetchone()
                term_id = t_row["id"]

                cur.execute(
                    """INSERT INTO srs_state (term_id, ease_factor, next_review_date)
                       VALUES (%s, %s, %s)""",
                    (term_id, float(initial_ease_factor), today_date),
                )
                try:
                    cur.execute(
                        """INSERT INTO user_srs_state (user_id, term_id, ease_factor, next_review_date)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (user_id, term_id) DO NOTHING""",
                        (int(user_id), term_id, float(initial_ease_factor), today_date),
                    )
                    if int(user_id) != 1:
                        cur.execute(
                            """INSERT INTO user_srs_state (user_id, term_id, ease_factor, next_review_date)
                               VALUES (1, %s, %s, %s)
                               ON CONFLICT (user_id, term_id) DO NOTHING""",
                            (term_id, float(initial_ease_factor), today_date),
                        )
                except Exception:
                    pass
        return self._to_term(t_row)

    def ensure_user_terms(
        self,
        user_id: int,
        term_ids: list[int] | tuple[int, ...],
        today: Optional[date] = None,
        initial_ease_factor: float = 2.5,
    ) -> int:
        """Asigna términos existentes del Catálogo Global al mazo SRS personal del usuario (Caché Canónico $0 IA)."""
        if not term_ids:
            return 0
        today_date = today or date.today()
        inserted = 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                for tid in term_ids:
                    cur.execute(
                        """INSERT INTO user_srs_state (user_id, term_id, ease_factor, next_review_date)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (user_id, term_id) DO NOTHING""",
                        (int(user_id), int(tid), float(initial_ease_factor), today_date),
                    )
                    inserted += cur.rowcount
                    if int(user_id) == 1:
                        cur.execute(
                            """INSERT INTO srs_state (term_id, ease_factor, next_review_date)
                               VALUES (%s, %s, %s)
                               ON CONFLICT (term_id) DO NOTHING""",
                            (int(tid), float(initial_ease_factor), today_date),
                        )
        return inserted

    def unlink_user_term(self, user_id: int, term_id: int) -> bool:
        """Desvincula un término del mazo personal de un usuario sin borrarlo del Catálogo Canónico Global."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM user_srs_state WHERE user_id = %s AND term_id = %s",
                    (int(user_id), int(term_id)),
                )
                return cur.rowcount >= 1

    def update_term_from_draft(self, term_id: int, category_id: int, draft: NewTermDraft) -> Optional[Term]:
        """Regeneración administrativa de un término con IA (con permiso transaccional explícito)."""
        tradeoff = draft.tradeoff_when_not_to_use.strip() if draft.tradeoff_when_not_to_use else None
        scopes_list = [s.strip() for s in (draft.other_scopes or ()) if s and s.strip()]
        canonical_src = (draft.canonical_source or "").strip()
        parent_val = draft.parent_term.strip() if draft.parent_term and draft.parent_term.strip() else None
        related_list = [r.strip() for r in (draft.related_terms or ()) if r and r.strip()]

        if draft.macro_area and (draft.macro_area_zoom_labels or draft.macro_area_description or getattr(draft, "macro_area_limits_label", None)):
            self.upsert_macro_area(
                name=draft.macro_area,
                description=draft.macro_area_description,
                zoom_labels=draft.macro_area_zoom_labels,
                limits_label=getattr(draft, "macro_area_limits_label", DEFAULT_LIMITS_LABEL),
            )

        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET LOCAL glosario.allow_admin_mutation = '1'")
                cur.execute(
                    """UPDATE terms
                       SET category_id = %s,
                           term = %s,
                           technical_definition = %s,
                           daily_analogy = %s,
                           tradeoff_when_not_to_use = %s,
                           other_scopes = %s,
                           canonical_source = %s,
                           parent_term = %s,
                           related_terms = %s
                       WHERE id = %s
                       RETURNING *""",
                    (
                        category_id,
                        draft.term.strip(),
                        draft.technical_definition.strip(),
                        draft.daily_analogy.strip(),
                        tradeoff,
                        scopes_list,
                        canonical_src,
                        parent_val,
                        related_list,
                        term_id,
                    ),
                )
                row = cur.fetchone()
        return self._to_term(row) if row else None

    def delete_term(self, term_id: int) -> bool:
        """Eliminación administrativa de un registro y su estado/historial SRS asociado."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL glosario.allow_admin_mutation = '1'")
                cur.execute("DELETE FROM review_log WHERE term_id = %s", (term_id,))
                cur.execute("DELETE FROM user_srs_state WHERE term_id = %s", (term_id,))
                cur.execute("DELETE FROM srs_state WHERE term_id = %s", (term_id,))
                cur.execute("DELETE FROM terms WHERE id = %s", (term_id,))
                return cur.rowcount == 1

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

    def fill_missing_scopes(self, term_id: int, scopes: list[str] | tuple[str, ...]) -> bool:
        clean_scopes = [s.strip() for s in scopes if s and s.strip()]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE terms
                       SET other_scopes = %s
                       WHERE id = %s AND (other_scopes IS NULL OR cardinality(other_scopes) = 0)""",
                    (clean_scopes, term_id),
                )
                return cur.rowcount == 1

    def count_terms(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM terms")
                return cur.fetchone()[0]

    # ----------------------------------------------------------------- SRS
    def get_srs_state(self, term_id: int, user_id: int = 1) -> Optional[SRSState]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if int(user_id) != 1:
                    cur.execute(
                        "SELECT * FROM user_srs_state WHERE user_id = %s AND term_id = %s",
                        (int(user_id), term_id),
                    )
                    r = cur.fetchone()
                    return self._to_srs(r) if r else None
                cur.execute("SELECT * FROM srs_state WHERE term_id = %s", (term_id,))
                r = cur.fetchone()
        return self._to_srs(r) if r else None

    def list_srs_states(self, user_id: int = 1) -> dict[int, SRSState]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if int(user_id) != 1:
                    cur.execute("SELECT * FROM user_srs_state WHERE user_id = %s", (int(user_id),))
                else:
                    cur.execute("SELECT * FROM srs_state")
                rows = cur.fetchall()
        return {r["term_id"]: self._to_srs(r) for r in rows}

    def save_srs_state(self, s: SRSState, user_id: int = 1) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if int(user_id) == 1:
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
                try:
                    cur.execute(
                        """INSERT INTO user_srs_state (user_id, term_id, interval_days, ease_factor, repetitions, next_review_date, last_reviewed_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (user_id, term_id) DO UPDATE SET
                               interval_days = EXCLUDED.interval_days,
                               ease_factor = EXCLUDED.ease_factor,
                               repetitions = EXCLUDED.repetitions,
                               next_review_date = EXCLUDED.next_review_date,
                               last_reviewed_at = EXCLUDED.last_reviewed_at""",
                        (int(user_id), s.term_id, s.interval_days, s.ease_factor,
                         s.repetitions, s.next_review_date, s.last_reviewed_at),
                    )
                except Exception:
                    pass

    def get_active_focus_category_id(
        self,
        today: date,
        user_id: int = 1,
        active_category_ids: Optional[list[int]] = None,
        excluded_term_ids: Optional[list[int]] = None,
    ) -> Optional[int]:
        """Encuentra la categoría de foco activo:
        1. Respeta `active_category_ids` y `excluded_term_ids` si el usuario personalizó su mazo de estudio.
        2. Si hay una categoría con repasos HOY que aún tiene fichas pendientes hoy, se mantiene fija
           (protección de sesión activa para no interrumpir cuando se captura un término en otra categoría).
        3. De lo contrario, selecciona la primera categoría pendiente por orden cronológico de currículo (`c.id ASC, c.zoom_level ASC`)
           para que una categoría nueva en otra área (ej. 'Derecho') jamás secuestre el foco antes de terminar las actuales.
        """
        filter_ids = [int(cid) for cid in (active_category_ids or []) if cid]
        excl_ids = [int(tid) for tid in (excluded_term_ids or []) if tid]
        srs_table = "user_srs_state" if int(user_id) != 1 else "srs_state"
        user_clause = "AND s.user_id = %s" if int(user_id) != 1 else ""
        cat_clause = "AND c.id = ANY(%s)" if filter_ids else ""
        excl_clause = "AND NOT (t.id = ANY(%s))" if excl_ids else ""

        params: list = [today]
        if int(user_id) != 1:
            params.append(int(user_id))
        if filter_ids:
            params.append(filter_ids)
        if excl_ids:
            params.append(excl_ids)
        params.extend([today, today])

        query = f"""SELECT c.id
                    FROM categories c
                    JOIN terms t ON t.category_id = c.id
                    JOIN {srs_table} s ON s.term_id = t.id
                    WHERE s.next_review_date <= %s {user_clause} {cat_clause} {excl_clause}
                    GROUP BY c.id, c.macro_area, c.zoom_level
                    ORDER BY CASE WHEN MAX(CASE WHEN s.last_reviewed_at IS NOT NULL AND DATE(s.last_reviewed_at) = %s THEN 1 ELSE 0 END) = 1 THEN 0 ELSE 1 END ASC,
                             MAX(CASE WHEN s.last_reviewed_at IS NOT NULL AND DATE(s.last_reviewed_at) = %s THEN s.last_reviewed_at ELSE NULL END) DESC NULLS LAST,
                             c.id ASC,
                             c.zoom_level ASC
                    LIMIT 1"""

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                row = cur.fetchone()
        return row[0] if row else None

    def list_due_term_ids(
        self,
        today: date,
        limit: Optional[int] = None,
        focused: bool = True,
        category_id: Optional[int] = None,
        user_id: int = 1,
        active_category_ids: Optional[list[int]] = None,
        excluded_term_ids: Optional[list[int]] = None,
        distinct_terms: bool = True,
    ) -> list[int]:
        filter_ids = [int(cid) for cid in (active_category_ids or []) if cid]
        excl_ids = [int(tid) for tid in (excluded_term_ids or []) if tid]
        target_cat_id = category_id
        if focused and target_cat_id is None:
            target_cat_id = self.get_active_focus_category_id(
                today, user_id=user_id, active_category_ids=filter_ids, excluded_term_ids=excl_ids
            )
            if target_cat_id is None:
                return []

        srs_table = "user_srs_state" if int(user_id) != 1 else "srs_state"
        where_parts = ["s.next_review_date <= %s"]
        params: list = [today]

        if int(user_id) != 1:
            where_parts.append("s.user_id = %s")
            params.append(int(user_id))

        if target_cat_id is not None:
            where_parts.append("t.category_id = %s")
            params.append(int(target_cat_id))
        elif filter_ids:
            where_parts.append("t.category_id = ANY(%s)")
            params.append(filter_ids)

        if excl_ids:
            where_parts.append("NOT (t.id = ANY(%s))")
            params.append(excl_ids)

        params.append(today)
        query = f"""SELECT s.term_id, t.term
                    FROM {srs_table} s
                    JOIN terms t ON t.id = s.term_id
                    WHERE {' AND '.join(where_parts)}
                    ORDER BY CASE WHEN s.last_reviewed_at IS NOT NULL AND DATE(s.last_reviewed_at) = %s THEN 1 ELSE 0 END ASC,
                             s.next_review_date ASC,
                             s.ease_factor ASC,
                             s.term_id ASC"""

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()

        if not distinct_terms:
            ids = [r[0] for r in rows]
            return ids[:limit] if limit else ids

        # Priorizar conceptos con nombre distinto dentro de la misma tanda antes de repetir acepciones hermanas
        primary_ids: list[int] = []
        overflow_ids: list[int] = []
        seen_names: set[str] = set()
        for tid, t_name in rows:
            norm = self.normalize_name(t_name or "")
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
        category_id: Optional[int] = None,
        user_id: int = 1,
        active_category_ids: Optional[list[int]] = None,
        excluded_term_ids: Optional[list[int]] = None,
    ) -> int:
        filter_ids = [int(cid) for cid in (active_category_ids or []) if cid]
        excl_ids = [int(tid) for tid in (excluded_term_ids or []) if tid]
        srs_table = "user_srs_state" if int(user_id) != 1 else "srs_state"
        where_parts = ["s.next_review_date <= %s"]
        params: list = [today]

        if int(user_id) != 1:
            where_parts.append("s.user_id = %s")
            params.append(int(user_id))

        if category_id is not None:
            where_parts.append("t.category_id = %s")
            params.append(int(category_id))
        elif filter_ids:
            where_parts.append("t.category_id = ANY(%s)")
            params.append(filter_ids)

        if excl_ids:
            where_parts.append("NOT (t.id = ANY(%s))")
            params.append(excl_ids)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT COUNT(*)
                        FROM {srs_table} s
                        JOIN terms t ON t.id = s.term_id
                        WHERE {' AND '.join(where_parts)}""",
                    tuple(params),
                )
                return cur.fetchone()[0]

    # ---------------------------------------------------------- Review Log
    def append_review_log(self, log: ReviewLog, user_id: int = 1) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """INSERT INTO review_log (user_id, term_id, rating, reviewed_at, interval_before, interval_after, ease_factor_after)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (int(user_id), log.term_id, int(log.rating), log.reviewed_at, log.interval_before,
                         log.interval_after, log.ease_factor_after),
                    )
                except Exception:
                    cur.execute(
                        """INSERT INTO review_log (term_id, rating, reviewed_at, interval_before, interval_after, ease_factor_after)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (log.term_id, int(log.rating), log.reviewed_at, log.interval_before,
                         log.interval_after, log.ease_factor_after),
                    )

    def count_reviews_on(self, day: date, user_id: Optional[int] = None) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if user_id is not None and int(user_id) != 1:
                    cur.execute(
                        "SELECT COUNT(*) FROM review_log WHERE DATE(reviewed_at) = %s AND user_id = %s",
                        (day, int(user_id)),
                    )
                else:
                    cur.execute(
                        "SELECT COUNT(*) FROM review_log WHERE DATE(reviewed_at) = %s",
                        (day,),
                    )
                return cur.fetchone()[0]

    # ------------------------------------------------------------- Settings
    def get_settings(self, user_id: int = 1) -> dict:
        defaults = {
            "session_card_limit": 5,
            "maturity_threshold_days": 21,
            "initial_ease_factor": 2.5,
            "focused_mode": True,
            "theme": "light",
            "step_by_step_reveal": False,
            "active_category_ids": [],
            "excluded_term_ids": [],
        }
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM user_settings WHERE id = %s", (int(user_id),))
                    r = cur.fetchone()
                    if not r:
                        return defaults
                    raw_active = r.get("active_category_ids") or []
                    raw_excluded = r.get("excluded_term_ids") or []
                    return {
                        "session_card_limit": int(r["session_card_limit"]),
                        "maturity_threshold_days": int(r["maturity_threshold_days"]),
                        "initial_ease_factor": round(float(r["initial_ease_factor"]), 2),
                        "focused_mode": bool(r["focused_mode"]),
                        "theme": str(r["theme"] or "light"),
                        "step_by_step_reveal": bool(r["step_by_step_reveal"]),
                        "active_category_ids": [int(x) for x in raw_active if x is not None],
                        "excluded_term_ids": [int(x) for x in raw_excluded if x is not None],
                    }
        except Exception:
            return defaults

    def update_settings(self, user_id: int = 1, **kwargs) -> dict:
        current = self.get_settings(user_id=user_id)
        merged = {**current, **{k: v for k, v in kwargs.items() if v is not None and k in current}}
        active_ids = [int(x) for x in (merged.get("active_category_ids") or []) if x is not None]
        excluded_ids = [int(x) for x in (merged.get("excluded_term_ids") or []) if x is not None]
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO user_settings (
                           id, session_card_limit, maturity_threshold_days,
                           initial_ease_factor, focused_mode, theme, step_by_step_reveal,
                           active_category_ids, excluded_term_ids, updated_at
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (id) DO UPDATE SET
                           session_card_limit = EXCLUDED.session_card_limit,
                           maturity_threshold_days = EXCLUDED.maturity_threshold_days,
                           initial_ease_factor = EXCLUDED.initial_ease_factor,
                           focused_mode = EXCLUDED.focused_mode,
                           theme = EXCLUDED.theme,
                           step_by_step_reveal = EXCLUDED.step_by_step_reveal,
                           active_category_ids = EXCLUDED.active_category_ids,
                           excluded_term_ids = EXCLUDED.excluded_term_ids,
                           updated_at = NOW()
                       RETURNING *""",
                    (
                        int(user_id),
                        int(merged["session_card_limit"]),
                        int(merged["maturity_threshold_days"]),
                        float(merged["initial_ease_factor"]),
                        bool(merged["focused_mode"]),
                        str(merged["theme"]),
                        bool(merged["step_by_step_reveal"]),
                        active_ids,
                        excluded_ids,
                    ),
                )
        return self.get_settings(user_id=user_id)

    # --------------------------------------------------- Multi-Tenant & Usuarios
    @staticmethod
    def _to_user_dict(r: dict) -> dict:
        return {
            "id": int(r["id"]),
            "telegram_chat_id": r.get("telegram_chat_id"),
            "display_name": r.get("display_name") or "Usuario",
            "role": r.get("role") or "user",
            "web_pin": r.get("web_pin"),
            "daily_ai_limit": int(r.get("daily_ai_limit") or 10),
            "ai_calls_today": int(r.get("ai_calls_today") or 0),
            "ai_calls_date": r["ai_calls_date"].isoformat() if isinstance(r.get("ai_calls_date"), date) else str(r.get("ai_calls_date") or ""),
            "is_approved": bool(r.get("is_approved", False)),
            "created_at": r["created_at"].isoformat() if isinstance(r.get("created_at"), datetime) else str(r.get("created_at") or ""),
        }

    def list_users(self) -> list[dict]:
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM users ORDER BY id ASC")
                    return [self._to_user_dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def get_user(self, user_id: int) -> Optional[dict]:
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
                    r = cur.fetchone()
                    return self._to_user_dict(r) if r else None
        except Exception:
            return None

    def get_user_by_chat_id(self, chat_id: str | int) -> Optional[dict]:
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM users WHERE telegram_chat_id = %s", (str(chat_id).strip(),))
                    r = cur.fetchone()
                    return self._to_user_dict(r) if r else None
        except Exception:
            return None

    def get_user_by_pin(self, pin: str) -> Optional[dict]:
        if not pin:
            return None
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM users WHERE web_pin = %s AND is_approved = TRUE", (pin.strip(),))
                    r = cur.fetchone()
                    return self._to_user_dict(r) if r else None
        except Exception:
            return None

    def create_or_update_user(
        self,
        user_id: Optional[int] = None,
        telegram_chat_id: Optional[str] = None,
        display_name: str = "Usuario",
        role: str = "user",
        web_pin: Optional[str] = None,
        daily_ai_limit: int = 10,
        is_approved: bool = True,
    ) -> dict:
        clean_chat = str(telegram_chat_id).strip() if telegram_chat_id else None
        clean_pin = str(web_pin).strip() if web_pin else None
        clean_role = "admin" if role == "admin" else "user"
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if user_id is not None:
                    cur.execute(
                        """UPDATE users
                           SET telegram_chat_id = COALESCE(%s, telegram_chat_id),
                               display_name = %s,
                               role = %s,
                               web_pin = %s,
                               daily_ai_limit = %s,
                               is_approved = %s
                           WHERE id = %s
                           RETURNING *""",
                        (clean_chat, display_name.strip(), clean_role, clean_pin, int(daily_ai_limit), bool(is_approved), int(user_id)),
                    )
                    r = cur.fetchone()
                    if r:
                        return self._to_user_dict(r)

                if clean_chat:
                    cur.execute(
                        """INSERT INTO users (telegram_chat_id, display_name, role, web_pin, daily_ai_limit, is_approved)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (telegram_chat_id) DO UPDATE SET
                               display_name = EXCLUDED.display_name,
                               role = EXCLUDED.role,
                               web_pin = COALESCE(EXCLUDED.web_pin, users.web_pin),
                               daily_ai_limit = EXCLUDED.daily_ai_limit,
                               is_approved = EXCLUDED.is_approved
                           RETURNING *""",
                        (clean_chat, display_name.strip(), clean_role, clean_pin, int(daily_ai_limit), bool(is_approved)),
                    )
                else:
                    cur.execute(
                        """INSERT INTO users (display_name, role, web_pin, daily_ai_limit, is_approved)
                           VALUES (%s, %s, %s, %s, %s)
                           RETURNING *""",
                        (display_name.strip(), clean_role, clean_pin, int(daily_ai_limit), bool(is_approved)),
                    )
                r = cur.fetchone()
                uid = int(r["id"])
                cur.execute("INSERT INTO user_settings (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (uid,))
                return self._to_user_dict(r)

    def delete_user(self, user_id: int) -> bool:
        if int(user_id) == 1:
            return False  # El administrador principal no se puede eliminar
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_settings WHERE id = %s", (int(user_id),))
                cur.execute("DELETE FROM users WHERE id = %s", (int(user_id),))
                return cur.rowcount == 1

    def check_and_consume_ai_quota(self, user_id: int = 1, today: Optional[date] = None) -> tuple[bool, int, int]:
        """Verifica y consume 1 crédito de cuota diaria IA para el usuario.
        Devuelve (permitido, usado_hoy, limite_diario).
        """
        today_date = today or date.today()
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
                    u = cur.fetchone()
                    if not u:
                        return True, 0, 9999
                    if u["role"] == "admin":
                        used = (int(u["ai_calls_today"]) if u["ai_calls_date"] == today_date else 0) + 1
                        cur.execute(
                            "UPDATE users SET ai_calls_today = %s, ai_calls_date = %s WHERE id = %s",
                            (used, today_date, int(user_id)),
                        )
                        return True, used, int(u["daily_ai_limit"])

                    limit = int(u["daily_ai_limit"] or 10)
                    used = int(u["ai_calls_today"] or 0) if u["ai_calls_date"] == today_date else 0
                    if used >= limit:
                        return False, used, limit
                    used += 1
                    cur.execute(
                        "UPDATE users SET ai_calls_today = %s, ai_calls_date = %s WHERE id = %s",
                        (used, today_date, int(user_id)),
                    )
                    return True, used, limit
        except Exception:
            return True, 0, 9999

    # --------------------------------------------------- Business Intelligence (BI)
    def get_bi_analytics(self) -> dict:
        """Agrega métricas centralizadas de aprendizaje, dificultad por concepto y actividad multi-usuario."""
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Términos con mayor tasa de olvido (puntos ciegos conceptuales)
                cur.execute(
                    """SELECT t.id AS term_id,
                              t.term,
                              c.macro_area,
                              c.name AS category_name,
                              COUNT(r.id) AS total_reviews,
                              SUM(CASE WHEN r.rating = 1 THEN 1 ELSE 0 END) AS forgotten_count,
                              SUM(CASE WHEN r.rating = 2 THEN 1 ELSE 0 END) AS doubted_count,
                              SUM(CASE WHEN r.rating = 3 THEN 1 ELSE 0 END) AS mastered_count,
                              ROUND((SUM(CASE WHEN r.rating = 1 THEN 1.0 ELSE 0.0 END) / NULLIF(COUNT(r.id), 0)) * 100, 1) AS forgotten_rate_pct
                       FROM terms t
                       JOIN categories c ON c.id = t.category_id
                       LEFT JOIN review_log r ON r.term_id = t.id
                       GROUP BY t.id, t.term, c.macro_area, c.name
                       ORDER BY forgotten_count DESC, total_reviews DESC, t.id ASC
                       LIMIT 15"""
                )
                hardest_terms = [dict(r) for r in cur.fetchall()]

                # 2. Métricas agregadas por Macro-Área
                cur.execute(
                    """SELECT c.macro_area,
                              COUNT(DISTINCT c.id) AS category_count,
                              COUNT(DISTINCT t.id) AS term_count,
                              ROUND(AVG(s.ease_factor)::numeric, 2) AS avg_ease_factor,
                              ROUND(AVG(s.interval_days)::numeric, 1) AS avg_interval_days,
                              SUM(CASE WHEN s.interval_days >= 21 THEN 1 ELSE 0 END) AS canonically_mastered_terms
                       FROM categories c
                       LEFT JOIN terms t ON t.category_id = c.id
                       LEFT JOIN srs_state s ON s.term_id = t.id
                       GROUP BY c.macro_area
                       ORDER BY term_count DESC, c.macro_area ASC"""
                )
                area_breakdown = [dict(r) for r in cur.fetchall()]

                # 3. Actividad y progreso por usuario
                cur.execute(
                    """SELECT u.id AS user_id,
                              u.display_name,
                              u.role,
                              u.telegram_chat_id,
                              u.is_approved,
                              u.daily_ai_limit,
                              u.ai_calls_today,
                              COUNT(DISTINCT us.term_id) AS assigned_terms,
                              SUM(CASE WHEN us.interval_days >= 21 THEN 1 ELSE 0 END) AS mastered_terms,
                              (SELECT COUNT(*) FROM review_log rl WHERE rl.user_id = u.id) AS total_reviews
                       FROM users u
                       LEFT JOIN user_srs_state us ON us.user_id = u.id
                       GROUP BY u.id
                       ORDER BY u.id ASC"""
                )
                user_metrics = [dict(r) for r in cur.fetchall()]

                # 4. Totales globales de auditoría
                cur.execute("SELECT COUNT(*) AS total_reviews FROM review_log")
                total_reviews = int(cur.fetchone()["total_reviews"] or 0)

        return {
            "total_reviews_logged": total_reviews,
            "hardest_terms": hardest_terms,
            "area_breakdown": area_breakdown,
            "user_metrics": user_metrics,
        }

    # --------------------------------------------------- Personalización CoW y Arbitraje Canónico
    def get_user_customization(self, user_id: int, term_id: int) -> Optional[UserTermCustomization]:
        """Obtiene la sobreescritura privada (Copy-on-Write) de un estudiante para un término."""
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM user_term_customizations WHERE user_id = %s AND term_id = %s",
                        (int(user_id), int(term_id)),
                    )
                    r = cur.fetchone()
                    return self._to_user_customization(r) if r else None
        except Exception:
            return None

    def list_user_customizations(self, user_id: int) -> dict[int, UserTermCustomization]:
        """Devuelve un mapa term_id -> UserTermCustomization de todas las notas/analogías privadas del usuario."""
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM user_term_customizations WHERE user_id = %s",
                        (int(user_id),),
                    )
                    rows = cur.fetchall()
                    return {int(r["term_id"]): self._to_user_customization(r) for r in rows}
        except Exception:
            return {}

    def save_user_customization(
        self,
        user_id: int,
        term_id: int,
        custom_definition: Optional[str] = None,
        custom_analogy: Optional[str] = None,
        custom_limits: Optional[str] = None,
        personal_notes: Optional[str] = None,
    ) -> UserTermCustomization:
        """Guarda o actualiza las notas y sobreescrituras privadas del estudiante con latencia sub-milisegundo."""
        clean_def = custom_definition.strip() if custom_definition is not None else None
        clean_ana = custom_analogy.strip() if custom_analogy is not None else None
        clean_lim = custom_limits.strip() if custom_limits is not None else None
        clean_not = personal_notes.strip() if personal_notes is not None else None

        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO user_term_customizations (
                           user_id, term_id, custom_definition, custom_analogy, custom_limits, personal_notes, updated_at
                       )
                       VALUES (%s, %s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (user_id, term_id) DO UPDATE SET
                           custom_definition = COALESCE(EXCLUDED.custom_definition, user_term_customizations.custom_definition),
                           custom_analogy    = COALESCE(EXCLUDED.custom_analogy, user_term_customizations.custom_analogy),
                           custom_limits     = COALESCE(EXCLUDED.custom_limits, user_term_customizations.custom_limits),
                           personal_notes    = COALESCE(EXCLUDED.personal_notes, user_term_customizations.personal_notes),
                           updated_at        = NOW()
                       RETURNING *""",
                    (int(user_id), int(term_id), clean_def, clean_ana, clean_lim, clean_not),
                )
                r = cur.fetchone()
                return self._to_user_customization(r)

    def delete_user_customization(self, user_id: int, term_id: int) -> bool:
        """Restaura la ficha al contenido canónico eliminando cualquier personalización local del usuario."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM user_term_customizations WHERE user_id = %s AND term_id = %s",
                    (int(user_id), int(term_id)),
                )
                return cur.rowcount >= 1

    def submit_canonical_proposal(
        self,
        user_id: int,
        term_id: int,
        proposal_justification: str,
        proposal_source: str,
        custom_definition: Optional[str] = None,
        custom_analogy: Optional[str] = None,
        custom_limits: Optional[str] = None,
    ) -> UserTermCustomization:
        """Eleva una propuesta de mejora epistémica a la cola de arbitraje canónico."""
        clean_def = custom_definition.strip() if custom_definition else None
        clean_ana = custom_analogy.strip() if custom_analogy else None
        clean_lim = custom_limits.strip() if custom_limits else None
        clean_src = (proposal_source or "").strip()
        clean_jus = (proposal_justification or "").strip()

        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """INSERT INTO user_term_customizations (
                           user_id, term_id, custom_definition, custom_analogy, custom_limits,
                           status, proposal_justification, proposal_source, updated_at
                       )
                       VALUES (%s, %s, %s, %s, %s, 'proposed_to_canonical', %s, %s, NOW())
                       ON CONFLICT (user_id, term_id) DO UPDATE SET
                           custom_definition      = COALESCE(EXCLUDED.custom_definition, user_term_customizations.custom_definition),
                           custom_analogy         = COALESCE(EXCLUDED.custom_analogy, user_term_customizations.custom_analogy),
                           custom_limits          = COALESCE(EXCLUDED.custom_limits, user_term_customizations.custom_limits),
                           status                 = 'proposed_to_canonical',
                           proposal_justification = EXCLUDED.proposal_justification,
                           proposal_source        = EXCLUDED.proposal_source,
                           arbitration_score      = NULL,
                           arbitration_verdict    = NULL,
                           updated_at             = NOW()
                       RETURNING *""",
                    (int(user_id), int(term_id), clean_def, clean_ana, clean_lim, clean_jus, clean_src),
                )
                r = cur.fetchone()
                return self._to_user_customization(r)

    def save_proposal_arbitration(
        self,
        user_id: int,
        term_id: int,
        score: float,
        verdict: str,
    ) -> None:
        """Registra el puntaje y dictamen emitido por el linter epistémico / árbitro de IA."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE user_term_customizations
                       SET arbitration_score = %s,
                           arbitration_verdict = %s,
                           updated_at = NOW()
                       WHERE user_id = %s AND term_id = %s""",
                    (float(score), str(verdict).strip(), int(user_id), int(term_id)),
                )

    def list_pending_proposals(self) -> list[dict]:
        """Lista propuestas de estudiantes pendientes de resolución por la administración."""
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """SELECT c.user_id,
                              c.term_id,
                              u.display_name AS user_name,
                              u.role AS user_role,
                              t.term AS term_name,
                              cat.macro_area,
                              cat.name AS category_name,
                              t.technical_definition AS canonical_definition,
                              t.daily_analogy AS canonical_analogy,
                              t.tradeoff_when_not_to_use AS canonical_limits,
                              t.canonical_source,
                              c.custom_definition AS proposed_definition,
                              c.custom_analogy AS proposed_analogy,
                              c.custom_limits AS proposed_limits,
                              c.proposal_justification,
                              c.proposal_source,
                              c.arbitration_score,
                              c.arbitration_verdict,
                              c.status,
                              c.updated_at
                       FROM user_term_customizations c
                       JOIN users u ON u.id = c.user_id
                       JOIN terms t ON t.id = c.term_id
                       JOIN categories cat ON cat.id = t.category_id
                       WHERE c.status IN ('proposed_to_canonical', 'rejected_by_arbitration')
                       ORDER BY c.updated_at DESC"""
                )
                rows = cur.fetchall()
                out = []
                for r in rows:
                    item = dict(r)
                    if isinstance(item.get("updated_at"), datetime):
                        item["updated_at"] = item["updated_at"].isoformat()
                    out.append(item)
                return out

    def resolve_proposal(
        self,
        user_id: int,
        term_id: int,
        action: str,
        admin_notes: Optional[str] = None,
    ) -> bool:
        """Acepta e incorpora la propuesta al Catálogo Canónico Global (Merge) o la rechaza."""
        with self._connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM user_term_customizations WHERE user_id = %s AND term_id = %s",
                    (int(user_id), int(term_id)),
                )
                custom = cur.fetchone()
                if not custom:
                    return False

                if action.lower() == "merge":
                    cur.execute("SET LOCAL glosario.allow_admin_mutation = '1'")
                    cur.execute(
                        """UPDATE terms
                           SET technical_definition     = COALESCE(NULLIF(%s, ''), technical_definition),
                               daily_analogy            = COALESCE(NULLIF(%s, ''), daily_analogy),
                               tradeoff_when_not_to_use = COALESCE(NULLIF(%s, ''), tradeoff_when_not_to_use),
                               canonical_source         = CASE WHEN %s <> '' THEN %s ELSE canonical_source END
                           WHERE id = %s""",
                        (
                            custom.get("custom_definition") or "",
                            custom.get("custom_analogy") or "",
                            custom.get("custom_limits") or "",
                            (custom.get("proposal_source") or "").strip(),
                            (custom.get("proposal_source") or "").strip(),
                            int(term_id),
                        ),
                    )
                    cur.execute(
                        """UPDATE user_term_customizations
                           SET status = 'merged_to_canonical',
                               arbitration_verdict = COALESCE(%s, arbitration_verdict),
                               updated_at = NOW()
                           WHERE user_id = %s AND term_id = %s""",
                        (admin_notes, int(user_id), int(term_id)),
                    )
                    return True
                elif action.lower() == "reject":
                    cur.execute(
                        """UPDATE user_term_customizations
                           SET status = 'rejected_by_arbitration',
                               arbitration_verdict = COALESCE(%s, arbitration_verdict),
                               updated_at = NOW()
                           WHERE user_id = %s AND term_id = %s""",
                        (admin_notes, int(user_id), int(term_id)),
                    )
                    return True
                return False
