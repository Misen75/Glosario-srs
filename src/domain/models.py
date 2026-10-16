"""Entidades del dominio. Sin dependencias de infraestructura (ni SQLite, ni HTTP, ni IA)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum
from typing import Optional


class ZoomLevel(IntEnum):
    """Nivel de 'zoom' del software en el que actúa una categoría."""
    MICRO = 1           # Código: clases, funciones, objetos
    MEDIO = 2           # Módulos, componentes, contratos entre piezas
    MACRO = 3           # Arquitectura del sistema completo
    INFRAESTRUCTURA = 4 # Operación, red, hardware, despliegue

    @property
    def label(self) -> str:
        return {
            1: "Micro · Código",
            2: "Medio · Módulos",
            3: "Macro · Arquitectura",
            4: "Infraestructura · Operación",
        }[int(self)]


class Rating(IntEnum):
    """Calificación del usuario al repasar una tarjeta."""
    FORGOTTEN = 1  # Olvidado  -> repetir hoy
    DOUBTED = 2    # Dudé      -> corto plazo
    MASTERED = 3   # Dominado  -> espaciar

    @property
    def label(self) -> str:
        return {1: "Olvidado", 2: "Dudé", 3: "Dominado"}[int(self)]


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
    """Macro-área (disciplina). Agrupa categorías ordenadas de Micro a Macro."""
    name: str
    categories: tuple[Category, ...] = field(default_factory=tuple)


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
