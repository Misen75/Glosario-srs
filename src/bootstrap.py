"""Composition root: lee .env y ensambla repositorio + enricher + servicio (lo usan ambas interfaces)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def build_service(database_url: str | None = None):
    from src.application.use_cases import GlossaryService
    from src.infrastructure.ai_enricher import GeminiEnricher
    from src.infrastructure.db_repository import PostgresGlossaryRepository

    db_url = database_url or os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError(
            "Falta la variable de entorno DATABASE_URL para conectar a PostgreSQL (Supabase / Render). "
            "Configúrala en tu archivo .env o en el panel de tu proveedor cloud."
        )

    repo = PostgresGlossaryRepository(database_url=db_url)
    repo.init_schema()
    return GlossaryService(repo, GeminiEnricher())

