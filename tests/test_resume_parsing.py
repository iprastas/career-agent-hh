# -*- coding: utf-8 -*-
"""Offline resume parsing regressions; all resume content here is synthetic."""
import pytest

from tools import parse_resume as parser


@pytest.mark.parametrize("residence, expected", [
    ("Проживает: Томск", "Томск"),
    ("Место проживания: Обнинск, Калужская область", "Обнинск"),
    ("ПРОЖИВАЕТ:\nг. Тверь\nГражданство: Россия", "Тверь"),
    ("Место проживания — Санкт-Петербург, м. Пионерская", "Санкт-Петербург"),
])
def test_explicit_residence_wins_over_work_and_education_cities(residence, expected):
    text = (
        "Python разработчик\n" + residence + "\n"
        "Опыт работы — 3 года\nООО Пример, Москва\n"
        "Образование\nУфа, университет\n"
    )
    assert parser.detect_city(text) == expected


HH_STYLE = (
    "Стоянова Иван\nПроживает: Ростов-на-Дону\nЖелаемая должность и зарплата\n"
    "C#/.NET-разработчик\nОпыт работы — 3 года\n"
    "Февраль 2023 —\nЯнварь 2026\n3 года\nЭкситон - автоматика, НПФ ООО\nУфа\n"
    "Инженер-программист\nРазрабатывал приложения на .NET.\nРезюме обновлено 1 апреля 2026\n"
    "Образование\nМагистр\nЮжный федеральный университет, Ростов-на-Дону\n"
)


def test_job_city_does_not_replace_residence():
    """Город места работы (Уфа) не должен подменять город проживания."""
    assert parser.detect_city(HH_STYLE) == "Ростов-на-Дону"


def test_companies_and_work_experience_section():
    assert parser.detect_companies(HH_STYLE) == ["Экситон - автоматика, НПФ ООО"]
    exp = parser.detect_work_experience(HH_STYLE)
    assert "Экситон - автоматика, НПФ ООО" in exp
    assert "Образование" not in exp and "Резюме обновлено" not in exp


def test_work_places_split_per_company():
    """У каждого места работы — свой текст опыта (по блокам дат секции hh)."""
    text = (
        "Иван\nПроживает: Томск\nОпыт работы — 8 лет\n"
        "Март 2019 —\nФевраль 2025\n5 лет 11 месяцев\nООО «Ромашка»\nТомск\nРазработчик\n"
        "Делал сервисы на Python.\n"
        "Март 2015 —\nФевраль 2019\n3 года 11 месяцев\nАО «Ромашка-сервис»\nОмск\nТестировщик\n"
        "Писал автотесты.\nОбразование\n"
    )
    places = parser.detect_work_places(text)
    assert [p["company"] for p in places] == ["ООО «Ромашка»", "АО «Ромашка-сервис»"]
    assert "Python" in places[0]["experience"]
    assert "Тестировщик" in places[1]["experience"] and "автотесты" in places[1]["experience"]
    assert "Томск" not in places[0]["experience"]  # город работы — не часть опыта
