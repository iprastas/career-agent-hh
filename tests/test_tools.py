# -*- coding: utf-8 -*-
"""Инструменты агента: ограничение выдачи, следование фильтрам, локальные записи."""
import json
import os
import types

import pytest

from app import config
from app.tools import (search_vacancies, vacancy_details, match_profile, skill_gap,
                       salary_stats, draft_letter, track_response)
from app.tools.match import resolve_profile_id

pytestmark = pytest.mark.skipif(not os.path.exists(config.APP_DB),
                                reason="data/app.db не собран (запустите tools/build_db.py --full)")


class TestSearch:
    def test_bounded_and_shaped(self):
        r = search_vacancies("C# разработчик", limit=3)
        assert isinstance(r["found"], int)
        assert 0 < len(r["vacancies"]) <= 3
        for v in r["vacancies"]:
            assert {"vacancy_id", "name", "employer", "city", "url", "top_skills"} <= set(v)
        assert "метод" in r["method"].lower() or "FTS" in r["method"]

    def test_city_filter_respected(self):
        r = search_vacancies("разработчик", city="Ростов-на-Дону", limit=5)
        assert all(v["city"] == "Ростов-на-Дону" for v in r["vacancies"])

    def test_limit_clamped(self):
        r = search_vacancies("python", limit=100)
        assert len(r["vacancies"]) <= 10

    def test_details_unknown_id(self):
        assert "error" in vacancy_details("999999999999")

    def test_employer_autodetect_filters_company(self):
        """Компания из запроса распознаётся автоматически: в выдаче только её вакансии."""
        r = search_vacancies("вакансии Яндекс", limit=6)
        assert r["employer_filter"] == "яндекс"
        assert len(r["vacancies"]) > 0
        for v in r["vacancies"]:
            assert "яндекс" in v["employer"].lower()

    def test_employer_param(self):
        r = search_vacancies("разработчик", employer="Яндекс", limit=4)
        assert r["employer_filter"].lower() == "яндекс"
        for v in r["vacancies"]:
            assert "яндекс" in v["employer"].lower()

    def test_actual_only_excludes_archive_2023(self):
        """«Актуальные» запросы не возвращают архивный слой 2023 года."""
        r = search_vacancies("актуальные вакансии", limit=8)
        assert r["actual_only"] is True
        assert len(r["vacancies"]) > 0
        for v in r["vacancies"]:
            assert not (v["published_at"] or "").startswith("2023")
        r2 = search_vacancies("вакансии", actual_only=True, limit=8)
        for v in r2["vacancies"]:
            assert not (v["published_at"] or "").startswith("2023")

    def test_details_real(self):
        r = search_vacancies("C# разработчик", limit=1)
        vid = r["vacancies"][0]["vacancy_id"]
        d = vacancy_details(vid)
        assert d["vacancy_id"] == vid and "description" in d and "key_skills" in d


class TestMatchGap:
    def test_match_shape(self):
        m = match_profile("anastasia", top_n=3)
        assert m["shown"] <= 3 and m["candidates"] > 0
        assert "0.45" in m["method"]
        for v in m["vacancies"]:
            assert 0 < v["score"] <= 1.1
            assert "matched_skills" in v and "missing_skills" in v

    def test_unknown_profile(self):
        assert "error" in match_profile("no_such_profile")

    def test_gap_shape(self):
        g = skill_gap("anastasia", top_n=5)
        assert g["universe"] > 0
        assert len(g["skills"]) <= 5
        for s in g["skills"]:
            assert s["priority"] in ("-", "высокий", "средний", "низкий")
            assert s["share_pct"] >= 0
            assert not s["in_profile"]  # skills — только разрыв (чего нет в резюме)
        # навыки резюме не тонут в обрезке top_n: видны в covered с флагом in_profile
        assert len(g["covered"]) <= 5
        assert all(s["in_profile"] for s in g["covered"])
        assert any(s["skill"] == "c#" for s in g["covered"])


class TestProfileIsolation:
    def test_user_profile_from_context(self, tmp_path, monkeypatch):
        """Инструменты берут СОБСТВЕННОЕ резюме пользователя из контекста; у нового
        пользователя без резюме — ошибка с подсказкой, а не чужой профиль."""
        monkeypatch.setattr(config, "PROFILES_DIR", str(tmp_path))
        with open(tmp_path / "u7.json", "w", encoding="utf-8") as f:
            json.dump({"profile_id": "u7", "title": "тест", "location": "Казань",
                       "skills": [{"name": "C#", "weight": "core"}],
                       "preferences": {"salary_net": 100000}}, f)
        ctx = types.SimpleNamespace(user_id="u7")
        assert resolve_profile_id("", ctx) == "u7"
        m = match_profile("", top_n=2, tool_context=ctx)
        assert m.get("profile") == "u7" and "error" not in m
        # у пользователя без резюме — честная ошибка, а не данные anastasia
        ctx2 = types.SimpleNamespace(user_id="u99")
        assert resolve_profile_id("", ctx2) == "u99"
        r = match_profile("", top_n=2, tool_context=ctx2)
        assert "error" in r and "u99" in r["error"]
        assert "error" in skill_gap("", tool_context=ctx2)
        # явный id профиля работает (примеры anastasia/junior/...)
        assert resolve_profile_id("anastasia", ctx2) == "anastasia"


class TestSalary:
    def test_quantiles_ordered(self):
        s = salary_stats(skill="python")
        assert s["n"] > 0
        assert s["p25_net"] <= s["median_net"] <= s["p75_net"]
        assert "квантили" in s["method"] and str(s["n"]) in s["method"]

    def test_empty_selection(self):
        s = salary_stats(skill="python", city="ТакогоГородаНет")
        assert s["n"] == 0 and "note" in s


class TestLocalWrites:
    def test_letter_saved_not_sent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "OUTBOX_DIR", str(tmp_path))
        r = search_vacancies("C# разработчик", limit=1)
        vid = r["vacancies"][0]["vacancy_id"]
        out = draft_letter(vid, "Здравствуйте! Пишу по поводу вакансии, мой опыт — 3 года C#/.NET.")
        assert out["saved_to"].startswith(str(tmp_path))
        assert os.path.exists(out["saved_to"])
        body = open(out["saved_to"], encoding="utf-8").read()
        assert "не отправлено" in body and "Здравствуйте" in body
        assert "НЕ отправлено" in out["note"]

    def test_letter_too_short(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "OUTBOX_DIR", str(tmp_path))
        assert "error" in draft_letter("1", "коротко")

    def test_tracker_validation_and_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TRACKER_DB", str(tmp_path / "t.db"))
        bad = track_response("123", "отправлено роботом")
        assert "error" in bad and "отправлено" in bad["valid"]
        good = track_response("123", "просмотрено", "жду ответа")
        assert good["updated"]["status"] == "просмотрено"
        lst = track_response()
        assert lst["count"] == 1 and lst["responses"][0]["vacancy_id"] == "123"
        assert "вручную" in lst["note"]
