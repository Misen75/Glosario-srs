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
DEF_MIN_WORDS = 25
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


class _AIScopeInterpretation(BaseModel):
    """Una acepción/interpretación individual de un término dentro de un ámbito específico."""
    macro_area: str = Field(description="Macro-área / disciplina fundamentada (ej. Ingeniería de Software, Derecho, Economía). Reusa una existente si aplica.")
    category: str = Field(description="Nombre de la categoría o ámbito específico donde se interpreta el término. Cada interpretación DEBE tener una categoría distinta.")
    is_new_category: bool
    category_description: str = Field(description="Explicación sencilla de la categoría y su nivel de zoom.")
    zoom_level: int = Field(description="1=Micro/Detalle/Código/Artículo, 2=Medio/Módulos/Instituciones, 3=Macro/Arquitectura/Rama, 4=Infraestructura/Fundamentos/Doctrina")
    technical_definition: str
    daily_analogy: str
    tradeoff_when_not_to_use: str


class _AIMultiFicha(BaseModel):
    """Esquema JSON de Captura Total: devuelve TODAS las interpretaciones canónicas del término en una sola llamada."""
    is_valid_concept: bool = Field(
        default=True,
        description="True si es un término o concepto académico, técnico, jurídico, científico o profesional legítimo. False si es un saludo ('Hola'), charla casual, frase sin sentido o mensaje conversacional."
    )
    rejection_reason: str = Field(
        default="",
        description="Si is_valid_concept es False, explicación breve y amable de por qué no es un término almacenable. Si es True, cadena vacía."
    )
    canonical_term: str = Field(description="Nombre canónico limpio del término (sin sufijos de área entre paréntesis, ya que la categoría define el ámbito). Si es sigla, 'SIGLA (Nombre completo)'.")
    interpretations: list[_AIScopeInterpretation] = Field(
        default_factory=list,
        description=(
            "Lista exhaustiva y completa de TODAS las interpretaciones/acepciones válidas y distintas de este término "
            "a través de todas las disciplinas, sub-ramas o niveles de abstracción donde se utiliza formalmente "
            "(por ejemplo: Derecho Civil, Derecho Tributario, Derecho Laboral, Economía y Finanzas, Contabilidad, "
            "Arquitectura de Software, Programación, Sistemas Operativos, Redes, etc.). "
            "NUNCA te limites a 1 sola interpretación si el término también tiene significado o aplicación específica "
            "en otra rama, sub-disciplina o nivel de zoom: genera una ficha independiente en `interpretations` por cada ámbito."
        ),
    )


class _AIFicha(BaseModel):
    """Esquema JSON individual que se exige al LLM (structured output)."""
    is_valid_concept: bool = Field(
        default=True,
        description="True si es un término o concepto académico, técnico, jurídico, científico o profesional legítimo. False si es un saludo ('Hola'), charla casual, frase sin sentido o mensaje conversacional."
    )
    rejection_reason: str = Field(
        default="",
        description="Si is_valid_concept es False, explicación breve y amable de por qué no es un término almacenable. Si es True, cadena vacía."
    )
    canonical_term: str = Field(description="Nombre canónico limpio del término (sin sufijos de área entre paréntesis, ya que la categoría define el ámbito). Si es sigla, 'SIGLA (Nombre completo)'.")
    macro_area: str = Field(description="Macro-área / disciplina fundamentada (ej. Ingeniería de Software, Derecho, Economía). Reusa una existente si aplica.")
    category: str = Field(description="Nombre de la categoría o ámbito específico donde se interpreta el término. Reusa una existente solo si representa exactamente este mismo ámbito.")
    is_new_category: bool
    category_description: str = Field(description="Solo si is_new_category: explicación sencilla de la categoría y su nivel de zoom. Si no, cadena vacía.")
    zoom_level: int = Field(description="1=Micro/Detalle/Código/Artículo, 2=Medio/Módulos/Instituciones, 3=Macro/Arquitectura/Rama, 4=Infraestructura/Fundamentos/Doctrina")
    technical_definition: str
    daily_analogy: str
    tradeoff_when_not_to_use: str
    other_valid_scopes: list[str] = Field(
        default_factory=list,
        description=(
            "Lista completa y sin límite artificial de todos los demás ámbitos, sub-ramas, disciplinas o niveles de abstracción "
            "verídicos donde este mismo término tiene una interpretación distinta (ej. ['Programación de APIs', 'Diseño de Sistemas Distribuidos', "
            "'Redes de Computadoras', 'Seguridad Informática', 'Derecho Procesal']). Vacío solo si tiene una única interpretación universal."
        ),
    )


_SYSTEM = """Eres un Arquitecto de Conocimiento Canónico y redactor experto multidisciplinar (Ingeniería de Software, Ciencias de la Computación, Derecho, Economía y Ciencias Exactas) que mantiene un glosario de estudio riguroso en español.
Tu tarea: dado el nombre de un término (y opcionalmente un ámbito/perspectiva objetivo), validar si es un concepto real y producir su ficha en JSON siguiendo estrictamente los estándares oficiales de la industria o academia.

Reglas de validación y estándares:
0. FILTRO ANTI-BASURA (is_valid_concept): Si el usuario envía un saludo ("Hola", "Buenos días"), una frase conversacional ("¿cómo estás?", "gracias"), números sueltos o texto sin valor conceptual, marca is_valid_concept=false, explica en rejection_reason que eres un glosario conceptual y rellena el resto con valores neutros breves.
1. ESTÁNDARES CANÓNICOS (FUENTES CERO): Valida el significado y utiliza la terminología técnica/académica exacta de las fuentes cero de la disciplina:
   - En Software/Computación: IEEE SWEBOK, ACM CCS, GoF (Design Patterns), Martin Kleppmann (DDIA), Robert C. Martin (Clean Architecture), Eric Evans (DDD), Roy Fielding (REST), RFCs/ISO.
   - En Derecho: Doctrina jurídica estándar, Teoría General del Derecho, Derecho Constitucional, Civil, Procesal, Penal o Administrativo.
   - En otras disciplinas: Nomenclatura Internacional de la UNESCO / CDU.
2. MÚLTIPLES INTERPRETACIONES POR ÁMBITO (1:N SIN LÍMITES):
   - Un mismo término (ej. "REST", "Proceso", "Interfaz", "Contrato", "Estado", "Proxy") puede tener distintas interpretaciones según el ámbito o nivel de zoom.
   - Mantén `canonical_term` con el nombre limpio del término, porque `category` y `macro_area` ya identifican bajo qué ámbito se está interpretando.
   - Si se solicita un ÁMBITO OBJETIVO específico, redacta la definición, analogía y trade-off 100% desde ese ámbito y ubícalo en la categoría que corresponda a ese ámbito (nunca en las categorías excluidas).
   - En `other_valid_scopes`, enumera TODOS los demás ámbitos, sub-disciplinas o áreas reales donde este mismo término tenga otra interpretación valiosa para estudiar, sin omitir ninguno.
3. technical_definition: SIN LÍMITE MÁXIMO DE PALABRAS. No sacrifiques precisión por brevedad: emplea toda la extensión y terminología formal necesaria para que la definición sea 100% rigurosa según los estándares establecidos. Empieza con el sustantivo ontológico clasificador exacto ("Estilo arquitectónico que...", "Patrón de diseño estructural que...", "Principio jurídico que...", "Mecanismo de concurrencia que..."). Explica con rigor técnico QUÉ es en ese ámbito, cómo opera su mecanismo/estructura formal, PARA QUÉ sirve y qué problema resuelve. Sin markdown, sin viñetas, en prosa.
4. daily_analogy: Analogía física o cotidiana directa, clara y memorable que mapee con fidelidad el mecanismo del concepto. Formato preferido: "Objeto o escena concreta: explicación de por qué se parece."
5. tradeoff_when_not_to_use: Explica con precisión técnica el límite, excepción, costo oculto, antipatrón o escenario en que NO aplica o resulta contraproducente.
6. Categoría: reutiliza el nombre y la macro-área de una categoría existente SOLO si corresponde exactamente al ámbito que estás redactando y no está en la lista de categorías excluidas. De lo contrario, crea una nueva categoría específica para ese ámbito (is_new_category=true).
7. zoom_level: 1=Micro (código/implementación/artículo), 2=Medio (módulo/patrón/contrato), 3=Macro (arquitectura/sistema/rama), 4=Infraestructura/Fundamentos/Doctrina.
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
    def _build_prompt(
        term: str,
        categories: Sequence[Category],
        examples: Sequence[tuple[Term, Category]],
        feedback: str = "",
        target_scope: Optional[str] = None,
        exclude_categories: Sequence[str] = (),
    ) -> str:
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
        if target_scope:
            prompt += (
                f'\nÁMBITO / PERSPECTIVA OBLIGATORIA: Interpreta y redacta "{term}" '
                f'EXCLUSIVAMENTE desde el ámbito de "{target_scope}".'
            )
        if exclude_categories:
            excl = ", ".join(f'"{c}"' for c in exclude_categories)
            prompt += (
                f"\nCATEGORÍAS YA OCUPADAS POR OTRAS INTERPRETACIONES DE ESTE TÉRMINO "
                f"(PROHIBIDO elegir cualquiera de estas; elige o crea una categoría distinta para este nuevo ámbito): {excl}"
            )
        if feedback:
            prompt += f"\n\nCORRIGE ESTO DE TU RESPUESTA ANTERIOR: {feedback}"
        return prompt

    # -------------------------------------------------------------- validation
    @staticmethod
    def _problems(f: _AIFicha, exclude_categories: Sequence[str] = ()) -> list[str]:
        if not f.is_valid_concept:
            return []
        p = []
        n = _word_count(f.technical_definition)
        if n < DEF_MIN_WORDS:
            p.append(f"technical_definition es demasiado breve ({n} palabras); desarrolla con rigor técnico (mínimo {DEF_MIN_WORDS} palabras).")
        if not f.daily_analogy.strip():
            p.append("daily_analogy es obligatoria.")
        if not f.tradeoff_when_not_to_use.strip():
            p.append("tradeoff_when_not_to_use es obligatorio.")
        if f.zoom_level not in (1, 2, 3, 4):
            p.append("zoom_level debe ser 1, 2, 3 o 4.")
        if not f.category.strip() or not f.macro_area.strip():
            p.append("category y macro_area son obligatorios.")
        excl_norm = {c.strip().casefold() for c in exclude_categories}
        if f.category.strip().casefold() in excl_norm:
            p.append(f"La categoría '{f.category}' ya tiene registrada otra acepción de este término; debes usar o crear una categoría diferente que represente el nuevo ámbito.")
        return p

    # ------------------------------------------------------------------ public
    def enrich(
        self,
        term: str,
        categories: Sequence[Category],
        examples: Sequence[tuple[Term, Category]] = (),
        target_scope: Optional[str] = None,
        exclude_categories: Sequence[str] = (),
    ) -> NewTermDraft:
        from google.genai import types

        term = " ".join(term.split())
        if not term:
            raise InvalidTermError("El término está vacío.")

        if len(term) > 120:
            raise InvalidTermError(
                f"El término es demasiado extenso ({len(term)} caracteres). El límite máximo es de 120 caracteres."
            )

        if target_scope and len(target_scope) > 60:
            raise InvalidTermError(
                f"El ámbito/scope es demasiado extenso ({len(target_scope)} caracteres). El límite máximo es de 60 caracteres."
            )

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
                    contents=self._build_prompt(
                        term, categories, examples, feedback,
                        target_scope=target_scope,
                        exclude_categories=exclude_categories,
                    ),
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
            problems = self._problems(ficha, exclude_categories=exclude_categories)
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
        scopes = tuple(s.strip() for s in (f.other_valid_scopes or []) if s and s.strip())
        return NewTermDraft(
            term=f.canonical_term.strip(), macro_area=macro, category_name=name, category_description=desc,
            zoom_level=zoom, technical_definition=" ".join(f.technical_definition.split()),
            daily_analogy=" ".join(f.daily_analogy.split()),
            tradeoff_when_not_to_use=" ".join(f.tradeoff_when_not_to_use.split()),
            other_scopes=scopes,
        )

    def enrich_all(
        self,
        term: str,
        categories: Sequence[Category],
        examples: Sequence[tuple[Term, Category]] = (),
        target_scope: Optional[str] = None,
        exclude_categories: Sequence[str] = (),
    ) -> list[NewTermDraft]:
        """Genera en una sola llamada TODAS las acepciones canónicas válidas del término (sin botones manuales)."""
        if target_scope:
            return [self.enrich(term, categories, examples, target_scope=target_scope, exclude_categories=exclude_categories)]

        from google.genai import types

        term = " ".join(term.split())
        if not term:
            raise InvalidTermError("El término está vacío.")
        if len(term) > 120:
            raise InvalidTermError(
                f"El término es demasiado extenso ({len(term)} caracteres). El límite máximo es de 120 caracteres."
            )
        norm_check = _normalize_check(term)
        if norm_check in _CONVERSATIONAL_NOISE or len(norm_check) < 2:
            raise InvalidTermError(
                f"«{term}» parece un saludo o mensaje conversacional, no un concepto técnico o académico para el glosario."
            )

        client = self._get_client()
        multi_prompt = (
            self._build_prompt(term, categories, examples, exclude_categories=exclude_categories)
            + "\n\nINSTRUCCIÓN OBLIGATORIA DE CAPTURA MULTIDISCIPLINAR COMPLETA: "
              "El usuario NO tiene botones para pedir otros ámbitos manualmente. Por lo tanto, DEBES generar de inmediato "
              "en el arreglo `interpretations` TODAS las interpretaciones/acepciones formales de este término en cada "
              "disciplina, sub-rama o nivel de abstracción donde aplique (ej. si es un término jurídico-económico como "
              "'Contraprestativo', incluye sus fichas independientes en Derecho Civil/Obligaciones, Derecho Tributario/Tasas, "
              "Derecho Laboral y Economía/Finanzas; si es de Software como 'REST' o 'Proceso', incluye Arquitectura, "
              "Programación de APIs, Sistemas Operativos, Derecho Procesal, etc.). "
              "Cada elemento de `interpretations` debe ubicarse en su propia `category` y `macro_area` (sin repetir `category`)."
        )

        feedback, multi = "", None
        excl_norm = {c.strip().casefold() for c in exclude_categories}
        for _ in range(MAX_ATTEMPTS):
            try:
                resp = client.models.generate_content(
                    model=self.model,
                    contents=multi_prompt + (f"\n\nCORRIGE: {feedback}" if feedback else ""),
                    config=types.GenerateContentConfig(
                        system_instruction=_SYSTEM,
                        response_mime_type="application/json",
                        response_schema=_AIMultiFicha,
                        temperature=0.3,
                    ),
                )
            except Exception as e:
                raise EnrichmentError(f"Error llamando a Gemini: {e}") from e

            multi = resp.parsed if isinstance(resp.parsed, _AIMultiFicha) else _AIMultiFicha.model_validate_json(resp.text)
            if not multi.is_valid_concept:
                reason = multi.rejection_reason.strip() or f"«{term}» no se reconoce como un concepto académico o técnico válido."
                raise InvalidTermError(reason)

            problems = []
            if not multi.interpretations:
                problems.append("Debes incluir al menos 1 interpretación en `interpretations`.")
            seen_cats: set[str] = set()
            for idx, item in enumerate(multi.interpretations, 1):
                cat_key = item.category.strip().casefold()
                if cat_key in excl_norm:
                    problems.append(f"Interpretación #{idx}: la categoría '{item.category}' ya está ocupada.")
                if cat_key in seen_cats:
                    problems.append(f"Interpretación #{idx}: categoría duplicada '{item.category}'; cada acepción debe tener su propio ámbito/categoría.")
                seen_cats.add(cat_key)
                if _word_count(item.technical_definition) < DEF_MIN_WORDS:
                    problems.append(f"Interpretación #{idx}: technical_definition demasiado breve (mínimo {DEF_MIN_WORDS} palabras).")
                if not item.daily_analogy.strip() or not item.tradeoff_when_not_to_use.strip():
                    problems.append(f"Interpretación #{idx}: daily_analogy y tradeoff_when_not_to_use son obligatorios.")
            if not problems:
                break
            feedback = " ".join(problems)

        if multi is None or not multi.interpretations:
            raise EnrichmentError("Gemini no devolvió interpretaciones válidas.")

        all_cat_names = [it.category.strip() for it in multi.interpretations if it.category.strip()]
        drafts: list[NewTermDraft] = []
        seen_final: set[str] = set()
        for it in multi.interpretations:
            ckey = it.category.strip().casefold()
            if ckey in seen_final or ckey in excl_norm:
                continue
            seen_final.add(ckey)
            match = next((c for c in categories if c.name.casefold() == ckey), None)
            if match:
                macro, name, desc, zoom = match.macro_area, match.name, match.description, match.zoom_level
            else:
                macro, name = it.macro_area.strip(), it.category.strip()
                desc = it.category_description.strip() or f"Categoría de {macro}."
                zoom = ZoomLevel(min(4, max(1, it.zoom_level if it.zoom_level in (1, 2, 3, 4) else 2)))
            sibling_scopes = tuple(s for s in all_cat_names if s.casefold() != ckey)
            drafts.append(
                NewTermDraft(
                    term=multi.canonical_term.strip(),
                    macro_area=macro,
                    category_name=name,
                    category_description=desc,
                    zoom_level=zoom,
                    technical_definition=" ".join(it.technical_definition.split()),
                    daily_analogy=" ".join(it.daily_analogy.split()),
                    tradeoff_when_not_to_use=" ".join(it.tradeoff_when_not_to_use.split()),
                    other_scopes=sibling_scopes,
                )
            )
        return drafts

