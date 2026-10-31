# -*- coding: utf-8 -*-
"""Инструменты агента. Все функции read-only по отношению к hh: сети здесь нет вообще.
Записи возможны только локально: data/outbox/ (письма) и data/tracker.db (отклики)."""
from .search import search_vacancies, vacancy_details
from .match import match_profile, skill_gap
from .salary import salary_stats
from .letters import draft_letter, track_response

ALL_TOOLS = [
    search_vacancies,
    vacancy_details,
    match_profile,
    skill_gap,
    salary_stats,
    draft_letter,
    track_response,
]
