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
from typing import Sequence

from src.domain.models import Rating, SRSState

MIN_EASE = 1.3
HARD_GROWTH = 1.2
MATURITY_THRESHOLD_DAYS = 21

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


def term_maturity_score(state: SRSState) -> int:
    """Puntaje cuantitativo de madurez cognitiva de una ficha (0, 30, 70 o 100)."""
    if state.repetitions <= 0 or state.interval_days <= 0:
        return 0
    if state.interval_days < 7:
        return 30
    if state.interval_days < MATURITY_THRESHOLD_DAYS:
        return 70
    return 100


def term_maturity_stage(state: SRSState) -> str:
    """Etapa de madurez de una ficha: new | learning | consolidating | mastered."""
    if state.repetitions <= 0 or state.interval_days <= 0:
        return "new"
    if state.interval_days < 7:
        return "learning"
    if state.interval_days < MATURITY_THRESHOLD_DAYS:
        return "consolidating"
    return "mastered"


def compute_mastery_metrics(states: Sequence[SRSState], today: date) -> dict:
    """Calcula el indicador cuantitativo de maestría (0-100%) para un conjunto de fichas."""
    total = len(states)
    if total == 0:
        return {
            "total_terms": 0,
            "mastery_score": 0.0,
            "new_count": 0,
            "learning_count": 0,
            "consolidating_count": 0,
            "mastered_count": 0,
            "due_today": 0,
            "is_session_cleared": False,
            "is_canonically_mastered": False,
            "status_label": "Sin términos",
        }

    scores = [term_maturity_score(s) for s in states]
    stages = [term_maturity_stage(s) for s in states]
    due_count = sum(1 for s in states if is_due(s, today))
    mastered_count = sum(1 for st in stages if st == "mastered")
    consolidating_count = sum(1 for st in stages if st == "consolidating")
    learning_count = sum(1 for st in stages if st == "learning")
    new_count = sum(1 for st in stages if st == "new")

    mastery_score = round(sum(scores) / total, 1)
    is_session_cleared = due_count == 0
    is_canonically_mastered = mastered_count == total

    if is_canonically_mastered and is_session_cleared:
        status_label = "🏆 Maestría Canónica"
    elif is_canonically_mastered and not is_session_cleared:
        status_label = "🔄 Refuerzo de Maestría"
    elif is_session_cleared:
        status_label = "✅ Al día (En Consolidación)"
    elif mastery_score >= 70.0:
        status_label = "⚡ Consolidando"
    elif mastery_score > 0.0:
        status_label = "🌱 En Adquisición"
    else:
        status_label = "🆕 Por Iniciar"

    return {
        "total_terms": total,
        "mastery_score": mastery_score,
        "new_count": new_count,
        "learning_count": learning_count,
        "consolidating_count": consolidating_count,
        "mastered_count": mastered_count,
        "due_today": due_count,
        "is_session_cleared": is_session_cleared,
        "is_canonically_mastered": is_canonically_mastered,
        "status_label": status_label,
    }

