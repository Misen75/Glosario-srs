"""Actualiza los registros existentes en PostgreSQL con la nueva estructura:
1. Asegura la columna `other_scopes TEXT[] NOT NULL DEFAULT '{}'` y actualiza el trigger PL/pgSQL.
2. Utiliza Gemini 2.5 Flash (sin límite de ámbitos) para identificar todos los ámbitos alternativos
   verídicos de cada uno de los términos existentes en la base de datos.
3. Persiste `other_scopes` de forma no destructiva en cada registro de PostgreSQL.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.infrastructure.db_repository import PostgresGlossaryRepository  # noqa: E402


class _TermScopesItem(BaseModel):
    term_id: int
    term: str
    other_scopes: list[str] = Field(
        description=(
            "Lista completa y sin límite artificial de todos los demás ámbitos, sub-ramas, disciplinas "
            "o niveles de abstracción donde este mismo término o concepto tiene una interpretación o aplicación distinta."
        )
    )


class _BatchScopesResponse(BaseModel):
    items: list[_TermScopesItem]


def migrate_existing_records() -> None:
    repo = PostgresGlossaryRepository()
    print("1. Actualizando esquema y triggers en PostgreSQL (columna other_scopes)...")
    repo.init_schema()

    cats = {c.id: c for c in repo.list_categories()}
    terms = repo.list_terms()
    pending = [t for t in terms if not t.other_scopes]
    print(f"Total términos en BD: {len(terms)} · Sin other_scopes aún: {len(pending)}")

    if not pending:
        print("Todos los términos ya cuentan con sus ámbitos alternativos registrados.")
        return

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Falta GEMINI_API_KEY en .env")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    batch_size = 50
    updated_count = 0

    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        lines = [
            f'- term_id={t.id} | term="{t.term}" | ámbito_actual="{cats[t.category_id].macro_area} -> {cats[t.category_id].name}"'
            for t in batch
        ]
        prompt = (
            "Analiza los siguientes términos de nuestro glosario y, para cada uno, devuelve TODOS los demás ámbitos, "
            "sub-disciplinas, niveles de abstracción (ej. Programación a nivel de código, Bases de Datos, Sistemas Operativos, "
            "Redes, Seguridad, Compiladores, Hardware, Derecho, Economía, Ingeniería Industrial, etc.) donde ese mismo término "
            "tenga una interpretación técnica, científica o académica diferenciada respecto a su ámbito_actual.\n"
            "No impongas un límite artificial de 4 ámbitos: incluye todos los ámbitos legítimos que correspondan a cada término.\n\n"
            + "\n".join(lines)
        )
        print(f"2. Consultando a Gemini ({model}) para lote {i // batch_size + 1} ({len(batch)} términos)...")
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_BatchScopesResponse,
                temperature=0.2,
            ),
        )
        parsed = (
            resp.parsed
            if isinstance(resp.parsed, _BatchScopesResponse)
            else _BatchScopesResponse.model_validate_json(resp.text)
        )
        for item in parsed.items:
            if item.other_scopes:
                ok = repo.fill_missing_scopes(item.term_id, item.other_scopes)
                if ok:
                    updated_count += 1
                    print(f"   [OK] [{item.term_id}] {item.term}: {len(item.other_scopes)} ambitos")

    print(f"\nMigracion completada! Se actualizaron {updated_count} registros en PostgreSQL.")


if __name__ == "__main__":
    migrate_existing_records()
