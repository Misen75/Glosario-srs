"""Algoritmo SM-2 (SuperMemo 2) como función PURA: sin I/O, sin fechas implícitas, sin BD.

Mapeo de las 3 calificaciones del usuario a la escala de calidad q (0-5) de SM-2:
    1 = Olvidado  -> q=1  (fallo: reinicia repeticiones, vuelve HOY; EF intacto, como en SM-2 original)
    2 = Dudé      -> q=3  (acierto con esfuerzo: EF baja 0.14)
    3 = Dominado  -> q=5  (acierto limpio: EF sube 0.10)

Intervalos:
    Dominado: 1 -> 6 -> intervalo_anterior * EF          (SM-2 clásico)
    Dudé:     1 -> 3 -> intervalo_anterior * 1.2         ("corto plazo": crece lento hasta afianzarse)
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from src.domain.models import Rating, SRSState

MIN_EASE = 1.3
HARD_GROWTH = 1.2

_QUALITY = {Rating.FORGOTTEN: 1, Rating.DOUBTED: 3, Rating.MASTERED: 5}


def _next_ease(ease: float, q: int) -> float:
    new = ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    return round(max(MIN_EASE, new), 2)


def _next_interval(state: SRSState, rating: Rating, new_reps: int, new_ease: float) -> int:
    if rating == Rating.FORGOTTEN:
        return 0
    if rating == Rating.DOUBTED:
        if new_reps == 1:
            return 1
        if new_reps == 2:
            return 3
        return max(state.interval_days + 1, round(state.interval_days * HARD_GROWTH))
    # MASTERED
    if new_reps == 1:
        return 1
    if new_reps == 2:
        return 6
    return max(state.interval_days + 1, round(state.interval_days * new_ease))


def schedule(state: SRSState, rating: Rating, now: datetime) -> SRSState:
    """Devuelve el NUEVO estado SRS tras calificar. No muta el estado de entrada."""
    rating = Rating(rating)
    q = _QUALITY[rating]
    if rating == Rating.FORGOTTEN:
        new_reps, new_ease = 0, state.ease_factor
    else:
        new_reps, new_ease = state.repetitions + 1, _next_ease(state.ease_factor, q)
    interval = _next_interval(state, rating, new_reps, new_ease)
    return replace(
        state,
        interval_days=interval,
        ease_factor=new_ease,
        repetitions=new_reps,
        next_review_date=now.date() + timedelta(days=interval),
        last_reviewed_at=now,
    )


def preview(state: SRSState, now: datetime) -> dict[Rating, int]:
    """Días hasta el próximo repaso para cada botón (para mostrarlos en la UI)."""
    return {r: schedule(state, r, now).interval_days for r in Rating}


def is_due(state: SRSState, today: date) -> bool:
    return state.next_review_date <= today
