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


class InvalidTermError(EnrichmentError):
    """Se lanza cuando el texto ingresado es un saludo, charla casual o no es un concepto válido."""
    pass


# Filtro rápido determinista para saludos y mensajes conversacionales cortos (ahorra tokens de IA)
_CONVERSATIONAL_NOISE = {
    "hola", "ola", "hello", "hi", "hey", "buenas", "buenos dias", "buenas tardes",
    "buenas noches", "gracias", " muchas gracias", "ok", "okay", "vale", "dale",
    "si", "no", "bien", "super", "genial", "perfecto", "listo", "ya", "test",
    "prueba", "probando", "123", "1234", "ayuda", "help", "start", "menu",
    "que tal", "como estas", "quien eres", "jaja", "jeje", "lol", "xd",
}


class _AIFicha(BaseModel):
    """Esquema JSON que se exige al LLM (structured output)."""
    is_valid_concept: bool = Field(
        default=True,
        description="True si es un término o concepto académico, técnico, jurídico, científico o profesional legítimo. False si es un saludo ('Hola'), charla casual, frase sin sentido o mensaje conversacional."
    )
    rejection_reason: str = Field(
        default="",
        description="Si is_valid_concept es False, explicación breve y amable de por qué no es un término almacenable. Si es True, cadena vacía."
    )
    canonical_term: str = Field(description="Nombre canónico del término. Si es sigla, 'SIGLA (Nombre completo)'. Si es polisémico y pertenece a un área específica fuera del software base, desambigua con 'Término (Área)'.")
    macro_area: str = Field(description="Macro-área / disciplina fundamentada (ej. Ingeniería de Software, Derecho, Economía). Reusa una existente si aplica.")
    category: str = Field(description="Nombre de la categoría o sub-disciplina específica. Reusa EXACTAMENTE una existente si encaja.")
    is_new_category: bool
    category_description: str = Field(description="Solo si is_new_category: explicación sencilla de la categoría y su nivel de zoom. Si no, cadena vacía.")
    zoom_level: int = Field(description="1=Micro/Detalle/Código/Artículo, 2=Medio/Módulos/Instituciones, 3=Macro/Arquitectura/Rama, 4=Infraestructura/Fundamentos/Doctrina")
    technical_definition: str
    daily_analogy: str
    tradeoff_when_not_to_use: str


_SYSTEM = """Eres un Arquitecto de Conocimiento Canónico y redactor experto multidisciplinar (Ingeniería de Software, Ciencias de la Computación, Derecho, Economía y Ciencias Exactas) que mantiene un glosario de estudio riguroso en español.
Tu tarea: dado el nombre de un término (con o sin área entre paréntesis), validar si es un concepto real y producir su ficha en JSON siguiendo estándares oficiales de la industria o academia.

Reglas de validación y estándares:
0. FILTRO ANTI-BASURA (is_valid_concept): Si el usuario envía un saludo ("Hola", "Buenos días"), una frase conversacional ("¿cómo estás?", "gracias"), números sueltos o texto sin valor conceptual, marca is_valid_concept=false, explica en rejection_reason que eres un glosario conceptual y rellena el resto con valores neutros breves.
1. ESTÁNDARES CANÓNICOS: Valida el significado contra las fuentes cero de la disciplina:
   - En Software/Computación: IEEE SWEBOK, ACM CCS, GoF (Design Patterns), Martin Kleppmann (DDIA), Robert C. Martin (Clean Architecture), Eric Evans (DDD), RFCs/ISO.
   - En Derecho: Doctrina jurídica estándar, Teoría General del Derecho, Derecho Constitucional, Civil, Procesal, Penal o Administrativo.
   - En otras disciplinas: Nomenclatura Internacional de la UNESCO / CDU.
2. POLISEMIA Y DESAMBIGUACIÓN: Si un término tiene varios significados según el área (ej. "Proceso", "Contrato", "Dominio", "Actor"):
   - Si el usuario especificó el área (ej. "Contrato (Derecho)" o "Derecho: Contrato"), enfócate 100% en esa disciplina y nombra canonical_term como "Contrato (Derecho Civil)".
   - Si no especificó el área y el término es nativo de Ingeniería de Software, prioriza Ingeniería de Software. Si es propio de otra ciencia (ej. "Habeas Corpus", "Usucapión", "Inflación"), clasifícalo en su macro_area correspondiente ("Derecho", "Economía", etc.).
3. technical_definition: entre 40 y 65 palabras. Rigurosa y validada. Empieza con un sustantivo clasificador ("Patrón que...", "Principio jurídico que...", "Institución procesal que...", "Mecanismo que..."). Explica QUÉ es, PARA QUÉ sirve y qué problema o ambigüedad resuelve. Sin markdown, sin viñetas, en prosa.
4. daily_analogy: máximo 2 oraciones. Analogía física o cotidiana directa y memorable. Formato preferido: "Objeto o escena concreta: explicación de por qué se parece."
5. tradeoff_when_not_to_use: exactamente 1 oración con el límite, excepción, costo oculto o escenario en que NO aplica o resulta contraproducente.
6. Categoría: reutiliza EXACTAMENTE el nombre y la macro-área de una categoría existente si el término encaja ahí. Solo si pertenece a otra disciplina o sub-rama no listada, propone una nueva (is_new_category=true) con category_description clara.
7. zoom_level: 1=Micro (código/regla/artículo), 2=Medio (módulo/patrón/contrato), 3=Macro (arquitectura/sistema/rama), 4=Infraestructura/Fundamentos/Doctrina.
8. Español neutro. Conserva en inglés o latín los términos técnicos/jurídicos consagrados cuando corresponda.
"""


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÀ-ÿ/.-]+\b", text))


def _sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def _normalize_check(text: str) -> str:
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^\w\s]", "", no_accents.casefold())
    return " ".join(cleaned.split())


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
        if not f.is_valid_concept:
            return []
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
            raise InvalidTermError("El término está vacío.")

        norm_check = _normalize_check(term)
        if norm_check in _CONVERSATIONAL_NOISE or len(norm_check) < 2:
            raise InvalidTermError(
                f"«{term}» parece un saludo o mensaje conversacional, no un concepto técnico o académico para el glosario."
            )

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
                        temperature=0.3,
                    ),
                )
            except Exception as e:  # red, cuota, clave inválida...
                raise EnrichmentError(f"Error llamando a Gemini: {e}") from e
            ficha = resp.parsed if isinstance(resp.parsed, _AIFicha) else _AIFicha.model_validate_json(resp.text)
            if not ficha.is_valid_concept:
                reason = ficha.rejection_reason.strip() or f"«{term}» no se reconoce como un concepto académico o técnico válido."
                raise InvalidTermError(reason)
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
