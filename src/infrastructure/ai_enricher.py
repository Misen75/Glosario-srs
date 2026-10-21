"""Conector con Gemini API: recibe SOLO el nombre de un término y devuelve una ficha completa
(categoría + definición + analogía + trade-off) en el estilo exacto del glosario original.

Este módulo NO toca la base de datos: recibe las categorías y ejemplos de estilo como argumentos.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from src.domain.models import DEFAULT_ZOOM_LABELS, Category, NewTermDraft, Term, ZoomLevel

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


class _AIArbitrationResult(BaseModel):
    """Dictamen del Linter Epistémico y Árbitro de Rigor para una propuesta de mejora canónica."""
    epistemic_score: float = Field(
        description="Calificación de 0.0 a 10.0 que mide el rigor ontológico, precisión de la definición y calidad de la fuente citada."
    )
    verdict: str = Field(
        description="Resultado formal: 'APPROVED_FOR_MERGE' (si score >= 8.5 y perfecciona de manera comprobable la ficha canónica), 'NEEDS_ADMIN_REVIEW' (si score está entre 6.0 y 8.4 o requiere criterio editorial), 'REJECTED' (si contiene errores ontológicos, definiciones tautológicas, fuentes espurias o empeora la versión canónica)."
    )
    critique: str = Field(
        description="Explicación analítica concisa (máximo 120 palabras) evaluando la solidez de la propuesta y la fuente citada contra el conocimiento canónico."
    )


class _AIScopeInterpretation(BaseModel):
    """Una acepción/interpretación individual de un término dentro de un ámbito específico."""
    macro_area: str = Field(description="Macro-área / disciplina canónica fundamentada (ej. Diseño y Arquitectura de Software, Ingeniería de Software, Ciencias de la Computación, Derecho, Economía). Reusa una existente si aplica.")
    macro_area_description: str = Field(default="", description="Descripción formal concisa de la macro-área disciplinar.")
    macro_area_zoom_labels: list[str] = Field(
        default_factory=list,
        description="Exactamente 4 descriptores de granularidad ontológica propios de esta macro-área (Zoom 1 Micro, Zoom 2 Medio, Zoom 3 Macro, Zoom 4 Fundamentos)."
    )
    category: str = Field(description="Nombre de la categoría o sub-disciplina canónica donde se clasifica el término según su ontología real. Cada interpretación DEBE tener una categoría distinta.")
    is_new_category: bool
    category_description: str = Field(description="Explicación sencilla de la categoría y su nivel de zoom.")
    zoom_level: int = Field(description="1=Micro/Detalle/Código/Artículo, 2=Medio/Módulos/Instituciones, 3=Macro/Arquitectura/Rama, 4=Infraestructura/Fundamentos/Doctrina")
    technical_definition: str
    daily_analogy: str
    tradeoff_when_not_to_use: str
    canonical_source: str = Field(
        default="",
        description=(
            "Cita formal obligatoria de la fuente primaria canónica que respalda esta definición y clasificación "
            "(Nivel 1: Estándares ISO/IEC, IEEE, RFCs, W3C, Códigos Oficiales; "
            "Nivel 2: Tratados fundacionales y literatura seminal con Autor y Obra; "
            "Nivel 3: Clasificaciones taxonómicas UNESCO / CDU / ACM CCS). "
            "Prohibido citar diccionarios de lengua general (RAE) o fuentes divulgativas."
        ),
    )
    parent_term: str = Field(
        default="",
        description="Hiperónimo ontológico o concepto contenedor inmediato dentro de la misma disciplina (ej. 'Estilos Arquitectónicos', 'Patrones Estructurales', 'Negocio Jurídico')."
    )
    related_terms: list[str] = Field(
        default_factory=list,
        description="Lista de 2 a 4 conceptos adyacentes, complementarios o contrapuestos dentro de la misma disciplina."
    )


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
            "Lista exhaustiva y completa de TODAS las interpretaciones/acepciones académicas, técnicas, científicas o jurídicas válidas y distintas de este término "
            "a través de las disciplinas formales donde se utiliza (excluyendo acepciones coloquiales o de diccionario de lengua general como la RAE). "
            "NUNCA te limites a 1 sola interpretación si el término también tiene significado formal específico "
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
    macro_area: str = Field(description="Macro-área / disciplina canónica fundamentada (ej. Diseño y Arquitectura de Software, Ingeniería de Software, Ciencias de la Computación, Derecho, Economía). Reusa una existente si aplica.")
    macro_area_description: str = Field(default="", description="Descripción formal concisa de la macro-área disciplinar.")
    macro_area_zoom_labels: list[str] = Field(
        default_factory=list,
        description="Exactamente 4 descriptores de granularidad ontológica propios de esta macro-área (Zoom 1 Micro, Zoom 2 Medio, Zoom 3 Macro, Zoom 4 Fundamentos)."
    )
    category: str = Field(description="Nombre de la categoría o ámbito canónico específico donde se clasifica el término según su ontología real.")
    is_new_category: bool
    category_description: str = Field(description="Solo si is_new_category: explicación sencilla de la categoría y su nivel de zoom. Si no, cadena vacía.")
    zoom_level: int = Field(description="1=Micro/Detalle/Código/Artículo, 2=Medio/Módulos/Instituciones, 3=Macro/Arquitectura/Rama, 4=Infraestructura/Fundamentos/Doctrina")
    technical_definition: str
    daily_analogy: str
    tradeoff_when_not_to_use: str
    canonical_source: str = Field(
        default="",
        description=(
            "Cita formal obligatoria de la fuente primaria canónica que respalda esta definición y clasificación "
            "(Nivel 1: Estándares ISO/IEC, IEEE, RFCs, W3C, Códigos Oficiales; "
            "Nivel 2: Tratados fundacionales y literatura seminal con Autor y Obra; "
            "Nivel 3: Clasificaciones taxonómicas UNESCO / CDU / ACM CCS)."
        ),
    )
    parent_term: str = Field(
        default="",
        description="Hiperónimo ontológico o concepto contenedor inmediato dentro de la misma disciplina."
    )
    related_terms: list[str] = Field(
        default_factory=list,
        description="Lista de 2 a 4 conceptos adyacentes, complementarios o contrapuestos dentro de la misma disciplina."
    )
    other_valid_scopes: list[str] = Field(
        default_factory=list,
        description=(
            "Lista completa y sin límite artificial de todos los demás ámbitos, sub-ramas, disciplinas o niveles de abstracción "
            "verídicos donde este mismo término tiene una interpretación distinta. Vacío solo si tiene una única interpretación universal."
        ),
    )


_SYSTEM = """Eres un Arquitecto de Conocimiento Canónico y redactor experto multidisciplinar (Diseño y Arquitectura de Software, Ingeniería de Software, Ciencias de la Computación, Derecho, Economía, Ciencias Exactas y Ciencias Sociales) que mantiene un glosario de estudio riguroso en español.
Tu tarea: dado el nombre de un término (y opcionalmente un Indicio de Desambiguación Semántica), validar si es un concepto real y producir su ficha en JSON siguiendo estrictamente los estándares oficiales y fuentes primarias de la disciplina.

Reglas de validación y estándares:
0. FILTRO ANTI-BASURA Y EXCLUSIÓN LÉXICA GENERAL (is_valid_concept):
   - Si el usuario envía un saludo ("Hola", "Buenos días"), una frase conversacional ("¿cómo estás?", "gracias"), números sueltos o texto sin valor conceptual, marca is_valid_concept=false.
   - PROHIBIDO incluir acepciones de diccionario de lengua general (ej. Real Academia Española - RAE) o definiciones coloquiales cuando el término posee acepciones técnicas, científicas, jurídicas o académicas.
1. JERARQUÍA ESTRICTA DE FUENTES PRIMARIAS (`canonical_source` OBLIGATORIO):
   Cada definición y clasificación DEBE estar respaldada por una fuente primaria canónica explícita en `canonical_source`:
   - Nivel 1 (Estándares Normativos y Especificaciones): ISO/IEC, IEEE (ej. SWEBOK, ISO/IEC 25010, ISO/IEC 42010), IETF RFCs, W3C, Códigos Legales Oficiales, Constituciones y Jurisprudencia Vinculante.
   - Nivel 2 (Literatura Fundacional y Tratados Seminales): Obras canónicas con autor y título explícito (ej. Gamma et al. - Design Patterns GoF; Martin Kleppmann - Designing Data-Intensive Applications; Robert C. Martin - Clean Architecture; Eric Evans - Domain-Driven Design; Sam Newman - Building Microservices; Mark Richards & Neal Ford - Fundamentals of Software Architecture; Roy Fielding - Architectural Styles and the Design of Network-based Software Architectures; Hans Kelsen - Teoría Pura del Derecho; Paul Samuelson / Hal Varian en Economía).
   - Nivel 3 (Sistemas de Clasificación Taxonómica): Nomenclatura Internacional de la UNESCO para los campos de la Ciencia y la Tecnología, Clasificación Decimal Universal (CDU) y ACM Computing Classification System (CCS).
2. DESACOPLAMIENTO ENTRE INDICIO DE DESAMBIGUACIÓN SEMÁNTICA Y CLASIFICACIÓN TAXONÓMICA CANÓNICA:
   - Cuando el usuario proporciona un contexto o ámbito junto al término (ej. un área aproximada), dicho valor actúa como un INDICIO DE DESAMBIGUACIÓN SEMÁNTICA para filtrar el dominio disciplinar y descartar homónimos ajenos, NUNCA como una imposición literal de categoría si el indicio es impreciso.
   - Aun cuando el usuario indique un ámbito aproximado o vecino, DEBES clasificar el término en su `macro_area`, `category` y `zoom_level` verdaderos y canónicos según la ontología oficial de la disciplina (salvo que el ámbito indicado ya coincida exactamente con una categoría canónica distinta que se esté desambiguando).
3. DESCRIPTORES DE GRANULARIDAD ONTOLÓGICA POR MACRO-ÁREA (`macro_area_zoom_labels`):
   - Para la `macro_area` del término, proporciona siempre en `macro_area_zoom_labels` los 4 descriptores específicos de esa disciplina:
     [Zoom 1 Micro, Zoom 2 Medio, Zoom 3 Macro, Zoom 4 Fundamentos].
4. TOPOLOGÍA ONTOLÓGICA (`parent_term` y `related_terms`):
   - `parent_term`: Indica el concepto padre o hiperónimo ontológico inmediato que contiene a este término dentro de su disciplina.
   - `related_terms`: Indica de 2 a 4 conceptos hermanos, complementarios o contrapuestos para construir el árbol relacional del concepto.
5. `technical_definition`: SIN LÍMITE MÁXIMO DE PALABRAS. No sacrifiques precisión por brevedad: emplea toda la extensión y terminología formal necesaria para que la definición sea 100% rigurosa según la fuente primaria citada. Empieza con el sustantivo ontológico clasificador exacto. Explica con rigor técnico QUÉ es en ese ámbito, cómo opera su mecanismo/estructura formal, PARA QUÉ sirve y qué problema resuelve. Sin markdown, sin viñetas, en prosa.
6. `daily_analogy`: Analogía física o cotidiana directa, clara y memorable que mapee con fidelidad el mecanismo del concepto.
7. `tradeoff_when_not_to_use`: Explica con precisión técnica el límite, excepción, costo oculto, antipatrón o escenario en que NO aplica o resulta contraproducente.
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
                f'\nINDICIO DE DESAMBIGUACIÓN SEMÁNTICA / CONTEXTO PROPORCIONADO POR EL USUARIO: "{target_scope}". '
                f'Utiliza este indicio para identificar el dominio disciplinar y descartar acepciones de otros dominios o de lengua general; '
                f'sin embargo, clasifica "{term}" en su `macro_area` y `category` CANÓNICAS verdaderas según la ontología oficial de esa disciplina '
                f'(no fuerces "{target_scope}" como nombre literal de categoría si es solo una aproximación del usuario).'
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
    def _normalize_zoom_labels(raw_labels: Sequence[str] | None) -> tuple[str, str, str, str]:
        cleaned = [str(x).strip() for x in (raw_labels or ()) if x and str(x).strip()]
        if len(cleaned) == 4:
            return (cleaned[0], cleaned[1], cleaned[2], cleaned[3])
        return DEFAULT_ZOOM_LABELS

    @classmethod
    def _to_draft(cls, f: _AIFicha, categories: Sequence[Category]) -> NewTermDraft:
        # Si el LLM eligió una categoría existente, se usan SUS datos canónicos (no los inventados).
        match = next((c for c in categories if c.name.casefold() == f.category.strip().casefold()), None)
        if match:
            macro, name, desc, zoom = match.macro_area, match.name, match.description, match.zoom_level
        else:
            macro, name = f.macro_area.strip(), f.category.strip()
            desc = f.category_description.strip() or f"Categoría de {macro}."
            zoom = ZoomLevel(min(4, max(1, f.zoom_level)))
        scopes = tuple(s.strip() for s in (f.other_valid_scopes or []) if s and s.strip())
        related = tuple(r.strip() for r in (f.related_terms or []) if r and r.strip())
        parent_val = f.parent_term.strip() if f.parent_term and f.parent_term.strip() else None
        canonical_src = (f.canonical_source or "").strip() or f"Estándar y literatura canónica de {macro}"
        return NewTermDraft(
            term=f.canonical_term.strip(),
            macro_area=macro,
            category_name=name,
            category_description=desc,
            zoom_level=zoom,
            technical_definition=" ".join(f.technical_definition.split()),
            daily_analogy=" ".join(f.daily_analogy.split()),
            tradeoff_when_not_to_use=" ".join(f.tradeoff_when_not_to_use.split()),
            other_scopes=scopes,
            canonical_source=canonical_src,
            parent_term=parent_val,
            related_terms=related,
            macro_area_description=(f.macro_area_description or "").strip(),
            macro_area_zoom_labels=cls._normalize_zoom_labels(f.macro_area_zoom_labels),
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
              "disciplina, sub-rama o nivel de abstracción donde aplique (excluyendo acepciones coloquiales o de diccionario de lengua general). "
              "Cada elemento de `interpretations` debe ubicarse en su propia `category` y `macro_area` (sin repetir `category`), "
              "e incluir obligatoriamente `canonical_source`, `parent_term`, `related_terms` y `macro_area_zoom_labels`."
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
            related = tuple(r.strip() for r in (it.related_terms or []) if r and r.strip())
            parent_val = it.parent_term.strip() if it.parent_term and it.parent_term.strip() else None
            canonical_src = (it.canonical_source or "").strip() or f"Estándar y literatura canónica de {macro}"
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
                    canonical_source=canonical_src,
                    parent_term=parent_val,
                    related_terms=related,
                    macro_area_description=(it.macro_area_description or "").strip(),
                    macro_area_zoom_labels=self._normalize_zoom_labels(it.macro_area_zoom_labels),
                )
            )
        return drafts

    def arbitrate_proposal(
        self,
        term_name: str,
        macro_area: str,
        category_name: str,
        canonical_definition: str,
        canonical_analogy: str,
        canonical_limits: Optional[str],
        canonical_source: str,
        proposed_definition: Optional[str],
        proposed_analogy: Optional[str],
        proposed_limits: Optional[str],
        proposal_source: str,
        proposal_justification: str,
    ) -> dict:
        """Audita y califica epistémicamente una propuesta de estudiante frente al texto canónico."""
        if not self._client:
            return {
                "score": 7.0,
                "verdict": "NEEDS_ADMIN_REVIEW",
                "critique": "Árbitro IA en modo offline; derivado a revisión manual del administrador.",
            }

        prompt = f"""Actúa como un Auditor Epistémico y Catedrático Principal de {macro_area}.
Evalúa con máxima rigurosidad académica la siguiente propuesta de modificación sometida al Compendio Canónico.

TÉRMINO: "{term_name}"
MACRO-ÁREA: {macro_area}
CATEGORÍA: {category_name}

ESTADO CANÓNICO ACTUAL:
- Definición técnica: {canonical_definition}
- Analogía didáctica: {canonical_analogy}
- Límites / Trade-offs: {canonical_limits or "(Ninguno)"}
- Fuente canónica registrada: {canonical_source or "(Sin registrar)"}

PROPUESTA DE MEJORA DEL ESTUDIANTE:
- Definición propuesta: {proposed_definition or "(Sin cambios)"}
- Analogía propuesta: {proposed_analogy or "(Sin cambios)"}
- Límites propuestos: {proposed_limits or "(Sin cambios)"}
- Justificación epistémica del estudiante: {proposal_justification}
- Fuente primaria citada por el estudiante: {proposal_source}

CRITERIOS DE ARBITRAJE Y LINTER ONTOLÓGICO:
1. Rigor Epistemológico: ¿La propuesta corrige una imprecisión real o profundiza la comprensión? ¿O introduce errores / sesgos?
2. Calidad de la Fuente: ¿La fuente citada es una Fuente Primaria (ISO, IEEE, RFC, tratadista canónico, ley positiva) o es una fuente informal/blog/Wikipedia?
3. Fidelidad Ontológica: ¿La definición propuesta preserva o eleva el lenguaje formal de la disciplina?

Emite tu dictamen en `_AIArbitrationResult` con:
- epistemic_score (0.0 a 10.0)
- verdict ('APPROVED_FOR_MERGE', 'NEEDS_ADMIN_REVIEW', 'REJECTED')
- critique (análisis técnico objetivo)
"""
        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_AIArbitrationResult,
                    temperature=0.1,
                ),
            )
            result = _AIArbitrationResult.model_validate_json(resp.text)
            return {
                "score": float(result.epistemic_score),
                "verdict": str(result.verdict).strip(),
                "critique": str(result.critique).strip(),
            }
        except Exception as exc:
            logger.warning("Error en arbitraje epistémico IA: %s", exc)
            return {
                "score": 7.0,
                "verdict": "NEEDS_ADMIN_REVIEW",
                "critique": f"Error en inferencia de arbitraje ({exc}); derivado a revisión manual del administrador.",
            }

