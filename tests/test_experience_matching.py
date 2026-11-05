"""Deterministic requirement/evidence matching, independent of personal data."""
import json
import sqlite3

import pytest

from tools import match_gap


def profile(**overrides):
    return {"id": "test", "title": "Python разработчик", "skills": {"python": "core", "sql": "core"},
            "location": "Казань", "expected_net": 150000,
            "summary": "Разработчик в Т-Банк: создавал банковские сервисы на Python и SQL.",
            "experience_years": 5, **overrides}


def vacancy(description="", **overrides):
    return {"vacancy_id": "1", "name": "Python разработчик", "_skills": {"python", "sql"},
            "city": "Казань", "schedule": "remote", "experience": "between3And6",
            "salary_from": 180000, "salary_to": None, "salary_currency": "RUR",
            "salary_gross": False, "description": description, **overrides}


def test_demonstrated_banking_outranks_unconfirmed_mandatory_ml():
    bank = vacancy("Требования: опыт разработки банковских сервисов обязателен.")
    ml = vacancy("Требования: обязателен опыт разработки ML моделей в production.")
    bank_score = match_gap.score_vacancy(bank, profile())
    ml_score = match_gap.score_vacancy(ml, profile())
    assert len(bank_score) == len(ml_score) == 6
    assert bank_score[0] > ml_score[0]
