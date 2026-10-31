# -*- coding: utf-8 -*-
"""Инструменты агента: ограничение выдачи, следование фильтрам, локальные записи."""
import os

import pytest

from app import config
from app.tools import (search_vacancies, vacancy_details, match_profile, skill_gap,
                       salary_stats, draft_letter, track_response)

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
