# -*- coding: utf-8 -*-
"""Инструменты агентов. Все функции read-only по отношению к hh: сети здесь нет вообще.
Записи возможны только локально: data/outbox/ (письма) и data/tracker.db (отклики)."""
from .search import search_vacancies, vacancy_details
from .semantic import semantic_search, similar_vacancies
from .match import match_profile, skill_gap
from .salary import salary_stats, salary_trend
from .fresh import fresh_vacancies
from .letters import draft_letter, track_response

ALL_TOOLS = [
    search_vacancies,
    semantic_search,
    similar_vacancies,
    vacancy_details,
    match_profile,
    fresh_vacancies,
    skill_gap,
    salary_stats,
    salary_trend,
    draft_letter,
    track_response,
]
