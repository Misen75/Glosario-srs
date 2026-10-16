"""Conector con Gemini API: recibe SOLO el nombre de un término y devuelve una ficha completa
(categoría + definición + analogía + trade-off) en el estilo exacto del glosario original.

Este módulo NO toca la base de datos: recibe las categorías y ejemplos de estilo como argumentos.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from src.domain.models import Category, NewTermDraft, Term, ZoomLevel

DEFAULT_MODEL = "gemini-2.5-flash"
DEF_MIN_WORDS, DEF_MAX_WORDS = 40, 65
MAX_ATTEMPTS = 3


class EnrichmentError(Exception):
    pass


class _AIFicha(BaseModel):
    """Esquema JSON que se exige al LLM (structured output)."""
    canonical_term: str = Field(description="Nombre canónico del término. Si es sigla, 'SIGLA (Nombre completo)'.")
    macro_area: str = Field(description="Macro-área / disciplina. Reusa una existente si aplica.")
    category: str = Field(description="Nombre de la categoría. Reusa EXACTAMENTE una existente si encaja.")
    is_new_category: bool
    category_description: str = Field(description="Solo si is_new_category: explicación sencilla de la categoría y su nivel de zoom. Si no, cadena vacía.")
    zoom_level: int = Field(description="1=Micro/Código, 2=Medio/Módulos, 3=Macro/Arquitectura, 4=Infraestructura/Operación")
    technical_definition: str
    daily_analogy: str
    tradeoff_when_not_to_use: str


_SYSTEM = """Eres un redactor técnico experto en ingeniería de software que mantiene un glosario de estudio en español.
Tu tarea: dado SOLO el nombre de un término técnico, producir su ficha en JSON imitando EXACTAMENTE el estilo de los ejemplos.

Reglas de redacción:
1. technical_definition: entre 40 y 65 palabras. Rigurosa. Empieza con un sustantivo que clasifique el concepto
   ("Patrón que...", "Principio que...", "Propiedad que...", "Mecanismo que..."). Explica QUÉ es y PARA QUÉ sirve,
   y el problema que evita. Sin markdown, sin viñetas, en prosa.
2. daily_analogy: máximo 2 oraciones. Analogía física o cotidiana directa y memorable. Formato preferido:
   "Objeto o escena concreta: explicación de por qué se parece." (como los ejemplos).
3. tradeoff_when_not_to_use: exactamente 1 oración con el costo oculto o el escenario en que conviene evitarlo.
4. Categoría: reutiliza EXACTAMENTE el nombre y la macro-área de una categoría existente si el término encaja ahí.
   Solo si pertenece a otra disciplina o a ninguna categoría existente, propone una nueva (is_new_category=true)
   con una category_description en lenguaje sencillo que explique qué agrupa y en qué nivel de zoom del software actúa.
5. zoom_level: 1=Micro/Código, 2=Medio/Módulos, 3=Macro/Arquitectura, 4=Infraestructura/Operación.
6. Español neutro. Conserva en inglés los nombres propios de patrones o tecnologías (como en los ejemplos).
"""


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÀ-ÿ/.-]+\b", text))


def _sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


class GeminiEnricher:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if not self.api_key:
            raise EnrichmentError("Falta GEMINI_API_KEY en el entorno (.env).")
        if self._client is None:
            from google import genai  # import diferido: el resto del sistema funciona sin el SDK
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    # ------------------------------------------------------------------ prompt
    @staticmethod
    def _build_prompt(term: str, categories: Sequence[Category],
                      examples: Sequence[tuple[Term, Category]], feedback: str = "") -> str:
        cats = "\n".join(
            f'- macro_area="{c.macro_area}" | category="{c.name}" | zoom={int(c.zoom_level)} | {c.description}'
            for c in categories
        )
        shots = "\n\n".join(
            f'TÉRMINO: {t.term}\nCATEGORÍA: {c.name}\nDEFINICIÓN: {t.technical_definition}\n'
            f'ANALOGÍA: {t.daily_analogy}\nTRADE-OFF: {t.tradeoff_when_not_to_use or "-"}'
            for t, c in examples
        )
        prompt = (f"CATEGORÍAS EXISTENTES:\n{cats}\n\n"
                  f"EJEMPLOS DE ESTILO (imítalos):\n{shots}\n\n"
                  f'NUEVO TÉRMINO A DOCUMENTAR: "{term}"')
        if feedback:
            prompt += f"\n\nCORRIGE ESTO DE TU RESPUESTA ANTERIOR: {feedback}"
        return prompt

    # -------------------------------------------------------------- validation
    @staticmethod
    def _problems(f: _AIFicha) -> list[str]:
        p = []
        n = _word_count(f.technical_definition)
        if not DEF_MIN_WORDS <= n <= DEF_MAX_WORDS:
            p.append(f"technical_definition tiene {n} palabras; debe tener entre {DEF_MIN_WORDS} y {DEF_MAX_WORDS}.")
        if _sentence_count(f.daily_analogy) > 2:
            p.append("daily_analogy debe tener como máximo 2 oraciones.")
        if _sentence_count(f.tradeoff_when_not_to_use) > 1:
            p.append("tradeoff_when_not_to_use debe ser exactamente 1 oración.")
        if f.zoom_level not in (1, 2, 3, 4):
            p.append("zoom_level debe ser 1, 2, 3 o 4.")
        if not f.category.strip() or not f.macro_area.strip():
            p.append("category y macro_area son obligatorios.")
        return p

    # ------------------------------------------------------------------ public
    def enrich(self, term: str, categories: Sequence[Category],
               examples: Sequence[tuple[Term, Category]] = ()) -> NewTermDraft:
        from google.genai import types

        term = " ".join(term.split())
        if not term:
            raise EnrichmentError("El término está vacío.")
        client = self._get_client()

        feedback, ficha = "", None
        for _ in range(MAX_ATTEMPTS):
            try:
                resp = client.models.generate_content(
                    model=self.model,
                    contents=self._build_prompt(term, categories, examples, feedback),
                    config=types.GenerateContentConfig(
                        system_instruction=_SYSTEM,
                        response_mime_type="application/json",
                        response_schema=_AIFicha,
                        temperature=0.4,
                    ),
                )
            except Exception as e:  # red, cuota, clave inválida...
                raise EnrichmentError(f"Error llamando a Gemini: {e}") from e
            ficha = resp.parsed if isinstance(resp.parsed, _AIFicha) else _AIFicha.model_validate_json(resp.text)
            problems = self._problems(ficha)
            if not problems:
                break
            feedback = " ".join(problems)
        if ficha is None:
            raise EnrichmentError("Gemini no devolvió respuesta.")

        return self._to_draft(ficha, categories)

    @staticmethod
    def _to_draft(f: _AIFicha, categories: Sequence[Category]) -> NewTermDraft:
        # Si el LLM eligió una categoría existente, se usan SUS datos canónicos (no los inventados).
        match = next((c for c in categories if c.name.casefold() == f.category.strip().casefold()), None)
        if match:
            macro, name, desc, zoom = match.macro_area, match.name, match.description, match.zoom_level
        else:
            macro, name = f.macro_area.strip(), f.category.strip()
            desc = f.category_description.strip() or f"Categoría de {macro}."
            zoom = ZoomLevel(min(4, max(1, f.zoom_level)))
        return NewTermDraft(
            term=f.canonical_term.strip(), macro_area=macro, category_name=name, category_description=desc,
            zoom_level=zoom, technical_definition=" ".join(f.technical_definition.split()),
            daily_analogy=" ".join(f.daily_analogy.split()),
            tradeoff_when_not_to_use=" ".join(f.tradeoff_when_not_to_use.split()),
        )
