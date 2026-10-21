"""Entidades del dominio. Sin dependencias de infraestructura (ni SQLite, ni HTTP, ni IA)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum
from typing import Optional


# Descriptores universales de granularidad ontológica. Se usan como valor por defecto cuando una
# Macro-Área todavía no tiene persistidos sus descriptores específicos de disciplina.
DEFAULT_ZOOM_LABELS: tuple[str, str, str, str] = (
    "Micro · Unidad elemental",
    "Medio · Componente",
    "Macro · Estructura",
    "Fundamentos · Doctrina e infraestructura",
)

DEFAULT_LIMITS_LABEL: str = "Compensaciones y Anti-patrones (Trade-offs)"


class ZoomLevel(IntEnum):
    """Nivel de granularidad ontológica en el que actúa una categoría dentro de su disciplina."""
    MICRO = 1           # Unidad elemental indivisible de la disciplina
    MEDIO = 2           # Componente o institución que articula unidades elementales
    MACRO = 3           # Estructura o rama sistémica que integra componentes
    INFRAESTRUCTURA = 4 # Sustrato: fundamentos teóricos, doctrina o infraestructura

    @property
    def label(self) -> str:
        """Descriptor universal (independiente de disciplina)."""
        return DEFAULT_ZOOM_LABELS[int(self) - 1]


class Rating(IntEnum):
    """Calificación del usuario al repasar una tarjeta."""
    FORGOTTEN = 1  # Olvidado  -> repetir hoy
    DOUBTED = 2    # Dudé      -> corto plazo
    MASTERED = 3   # Dominado  -> espaciar

    @property
    def label(self) -> str:
        return {1: "Olvidado", 2: "Dudé", 3: "Dominado"}[int(self)]


@dataclass(frozen=True)
class MacroArea:
    """Disciplina normalizada del conocimiento con sus descriptores de escala y límites propios."""
    id: Optional[int]
    name: str
    description: str = ""
    zoom_labels: tuple[str, str, str, str] = DEFAULT_ZOOM_LABELS
    limits_label: str = DEFAULT_LIMITS_LABEL

    def zoom_label(self, level: int | ZoomLevel) -> str:
        idx = min(4, max(1, int(level))) - 1
        labels = self.zoom_labels if len(self.zoom_labels) == 4 else DEFAULT_ZOOM_LABELS
        return labels[idx] or DEFAULT_ZOOM_LABELS[idx]

    @property
    def has_specific_labels(self) -> bool:
        return tuple(self.zoom_labels) != DEFAULT_ZOOM_LABELS


@dataclass(frozen=True)
class Category:
    id: Optional[int]
    macro_area: str
    name: str
    description: str
    zoom_level: ZoomLevel

    @property
    def simple_explanation(self) -> str:
        return self.description


@dataclass(frozen=True)
class Area:
    """Macro-área (disciplina). Agrupa categorías ordenadas de Micro a Fundamentos."""
    name: str
    categories: tuple[Category, ...] = field(default_factory=tuple)
    description: str = ""
    zoom_labels: tuple[str, str, str, str] = DEFAULT_ZOOM_LABELS
    limits_label: str = DEFAULT_LIMITS_LABEL


@dataclass(frozen=True)
class UserTermCustomization:
    """Sobreescritura personal (Copy-on-Write) y estado de propuesta de mejora canónica."""
    user_id: int
    term_id: int
    custom_definition: Optional[str] = None
    custom_analogy: Optional[str] = None
    custom_limits: Optional[str] = None
    personal_notes: Optional[str] = None
    status: str = "private"  # 'private', 'proposed_to_canonical', 'rejected_by_arbitration', 'merged_to_canonical'
    proposal_justification: Optional[str] = None
    proposal_source: Optional[str] = None
    arbitration_score: Optional[float] = None
    arbitration_verdict: Optional[str] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class Term:
    """Término del glosario. Inmutable por diseño: una vez creado no se edita ni se borra."""
    id: Optional[int]
    category_id: int
    term: str
    technical_definition: str
    daily_analogy: str
    tradeoff_when_not_to_use: Optional[str]
    created_at: Optional[datetime] = None
    other_scopes: tuple[str, ...] = field(default_factory=tuple)
    canonical_source: str = ""
    parent_term: Optional[str] = None
    related_terms: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NewTermDraft:
    """Ficha propuesta (por el seed o por la IA) antes de persistirse."""
    term: str
    macro_area: str
    category_name: str
    category_description: str
    zoom_level: ZoomLevel
    technical_definition: str
    daily_analogy: str
    tradeoff_when_not_to_use: Optional[str]
    other_scopes: tuple[str, ...] = field(default_factory=tuple)
    canonical_source: str = ""
    parent_term: Optional[str] = None
    related_terms: tuple[str, ...] = field(default_factory=tuple)
    macro_area_description: str = ""
    macro_area_zoom_labels: tuple[str, ...] = field(default_factory=tuple)
    macro_area_limits_label: str = DEFAULT_LIMITS_LABEL


@dataclass(frozen=True)
class SRSState:
    """Estado de repetición espaciada de un término (esto SÍ evoluciona en el tiempo)."""
    term_id: int
    interval_days: int = 0
    ease_factor: float = 2.5
    repetitions: int = 0
    next_review_date: date = field(default_factory=date.today)
    last_reviewed_at: Optional[datetime] = None


@dataclass(frozen=True)
class ReviewLog:
    """Registro histórico (append-only) de cada repaso."""
    id: Optional[int]
    term_id: int
    rating: Rating
    reviewed_at: datetime
    interval_before: int
    interval_after: int
    ease_factor_after: float
