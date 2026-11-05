# -*- coding: utf-8 -*-
"""Новые функции: классификатор тем, семантический поиск, свежие вакансии, тренд
зарплат, напоминания откликов, профиль, evals-проверки."""
import datetime
import json
import os
import shutil

import pytest

from app import config, evals, guardrails, topic_gate
from app.tools.fresh import fresh_vacancies
from app.tools.letters import parse_follow_up, track_response
from app.tools.salary import salary_trend
from app.tools.semantic import semantic_search, similar_vacancies

pytestmark = pytest.mark.skipif(not os.path.exists(config.APP_DB),
                                reason="data/app.db не собран")


class TestTopicGate:
    def test_career_questions_pass(self):
        for q in ["Подбери вакансии C#/.NET в Ростове-на-Дону",
                  "Какие навыки мне стоит подтянуть для роста зарплаты?",
                  "Напиши сопроводительное письмо на вакансию 137311444",
                  "Сколько платят дотнетчикам в Казани на руки?",
                  "Запиши в отклики вакансию 123 статус отправлено",
                  "как подготовиться к техническому собеседованию на junior c#"]:
            label, _ = topic_gate.classify(q)
            assert label != "offtopic", q

    def test_offtopic_blocked(self):
        for q in ["Какая погода завтра в Москве?",
                  "Напиши стих про осень",
                  "Реши уравнение 2x+3=7",
                  "Напиши код на python для сортировки списка",
                  "Как сварить борщ, рецепт?",
                  "Расскажи анекдот про программистов"]:
            assert topic_gate.is_offtopic(q), q

    def test_neutral_short_messages_pass_to_model(self):
        assert topic_gate.classify("привет")[0] == "neutral"
        assert topic_gate.classify("спасибо")[0] == "neutral"

    def test_pre_filter_offtopic_reason(self):
        ok, reason = guardrails.pre_filter("какую игру купить в стиме на распродаже")
        assert not ok and reason == "offtopic"
        ok, _ = guardrails.pre_filter("подбери вакансии джава в казани")
        assert ok


class TestEvalsChecks:
    def test_numbers_in(self):
        assert evals.numbers_in("медиана 160 950 ₽, n=83") == {160950.0, 83.0}
        assert evals.numbers_in("около 108,75 тыс. ₽") == {108750.0}
        assert evals.numbers_in("1,5 млн вакансий") == {1500000.0}

    def test_check_numbers_ok_and_violations(self):
        facts = "p25=121800 median=160950 n=83"
        ok, v = evals.check_numbers("медиана 160 950 ₽ по 83 вакансиям", facts)
        assert ok and not v
        ok, v = evals.check_numbers("средняя зарплата 250 000 ₽", facts)
        assert not ok and v  # цифра не из выдачи инструментов

    def test_small_ints_and_years_allowed(self):
        ok, _ = evals.check_numbers("2–4 абзаца, опыт с 2023 года", "")
        assert ok

    def test_check_tools(self):
        ok, missing = evals.check_tools(["search_vacancies"],
                                       [["search_vacancies", "semantic_search"], ["salary_stats"]])
        assert not ok and missing == [["salary_stats"]]

    def test_score_answer_refusal(self):
        q = {"id": "x", "question": "погода?", "must_refuse": True}
        res = evals.score_answer(q, guardrails.REFUSAL_TOPIC, [], "")
        assert res["pass"]

    def test_benchmark_loads(self):
        b = evals.load_benchmark()
        assert len(b) >= 10
        assert any(x.get("must_refuse") for x in b)


class TestSemantic:
    def test_semantic_search_returns_cards(self):
        r = semantic_search("тестировщик мобильных приложений", limit=3)
        assert "error" not in r or not r.get("error")
        assert r["vacancies"] and r["scores"]

    def test_vacancy_details_full_card(self):
        from app.tools import search_vacancies, vacancy_details
        vid = search_vacancies("C# разработчик", limit=1)["vacancies"][0]["vacancy_id"]
        d = vacancy_details(vid)
        assert "error" not in d
        assert "schedule" in d and "employment" in d
        assert d.get("description") or d.get("requirement")

    def test_similar_vacancies(self):
        r = similar_vacancies("137311444", limit=3)
        if r.get("error"):
            pytest.skip("индекс не собран или вакансия вне индекса")
        assert r["vacancies"] and r["base"] == "137311444"


class TestFreshAndTrend:
    def test_fresh_vacancies(self):
        r = fresh_vacancies(days=30, limit=3)
        assert "error" not in r
        assert "method" in r and isinstance(r["vacancies"], list)
        if r["vacancies"]:
            assert "score" in r["vacancies"][0]

    def test_salary_trend_months(self):
        r = salary_trend(skill="c#", months=24)
        assert r["trend"]
        months = [p["month"] for p in r["trend"]]
        assert months == sorted(months)
        assert all(p["n"] >= 3 for p in r["trend"])


class TestFollowUp:
    def test_parse_follow_up(self):
        assert parse_follow_up("2026-11-07") == "2026-11-07"
        week = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
        assert parse_follow_up("через неделю") == week
        assert parse_follow_up("через 10 дней") == \
            (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
        assert parse_follow_up("") == ""

    def test_tracker_follow_up_persists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TRACKER_DB", str(tmp_path / "tracker.db"))
        r = track_response("100500", "черновик", note="тест", follow_up="через неделю",
                           tool_context=None)
        assert r["updated"]["follow_up_at"] == \
            (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
        # пустое напоминание не затирает существующее
        r2 = track_response("100500", "отправлено", note="", follow_up="")
        assert r2["updated"]["follow_up_at"] == r["updated"]["follow_up_at"]


class TestProfileAPI:
    def test_profile_isolated_per_user(self, tmp_path, monkeypatch):
        """Каждый пользователь видит только своё резюме: новый аккаунт получает пустую
        форму, резюме другого пользователя (anastasia) не подставляется и не изменяется."""
        from fastapi.testclient import TestClient
        from app.server import store
        import app.server.main as main
        prof_dir = tmp_path / "profiles"
        prof_dir.mkdir()
        shutil.copy(os.path.join(config.PROFILES_DIR, "anastasia.json"),
                    prof_dir / "anastasia.json")
        monkeypatch.setattr(store, "USERS_DB", str(tmp_path / "users.db"))
        monkeypatch.setattr(config, "DATA", str(tmp_path))
        monkeypatch.setattr(config, "PROFILES_DIR", str(prof_dir))
        monkeypatch.setattr(main, "_RUNNER", None)
        client = TestClient(main.app)
        tok = client.post("/api/register", json={"email": "p@test.ru",
                                                 "password": "password123"}).json()["token"]
        h = {"Authorization": "Bearer " + tok}
        # новый аккаунт: пустое СОБСТВЕННОЕ резюме (u1), а не профиль anastasia
        got = client.get("/api/profile", headers=h).json()
        assert got["own"] is True and got["empty"] is True
        assert got["profile_id"] == "u1"
        assert got["data"]["title"] == "" and got["data"]["skills"] == []
        # сохранение собственного резюме (форма)
        upd = client.put("/api/profile", headers=h,
                         json={"name": "Иван", "title": "C# Senior",
                               "location": "Казань", "experience_years": 5,
                               "salary_net": 150000,
                               "skills": ["C#", ".NET", "docker", "c#"]})
        assert upd.status_code == 200
        assert upd.json()["data"]["title"] == "C# Senior"
        assert upd.json()["empty"] is False
        saved = json.load(open(prof_dir / "u1.json", encoding="utf-8"))
        assert saved["location"] == "Казань" and saved["preferences"]["salary_net"] == 150000
        # дубль навыка складывается, порядок сохраняется; имена — канонические (словарь БД)
        assert [s["name"] for s in saved["skills"]] == ["c#", ".net", "docker"]
        # чужое резюме изменить нельзя
        assert client.put("/api/profile", headers=h,
                          json={"profile_id": "anastasia", "title": "x",
                                "location": "", "experience_years": 0,
                                "salary_net": 0}).status_code == 403
        assert client.put("/api/profile", headers=h,
                          json={"profile_id": "../evil", "title": "x",
                                "location": "", "experience_years": 0,
                                "salary_net": 0}).status_code == 403
        # общий пример доступен только для чтения; выхода из каталога нет
        ex = client.get("/api/profile?profile_id=anastasia", headers=h).json()
        assert ex["own"] is False and ex["data"]["title"]
        assert client.get("/api/profile?profile_id=no_such", headers=h).status_code == 404

    def test_parse_resume_text_draft(self, tmp_path, monkeypatch):
        """Текст резюме разбирается в черновик формы (без сохранения)."""
        from fastapi.testclient import TestClient
        from app.server import store
        import app.server.main as main
        monkeypatch.setattr(store, "USERS_DB", str(tmp_path / "users.db"))
        monkeypatch.setattr(config, "DATA", str(tmp_path))
        monkeypatch.setattr(config, "PROFILES_DIR", str(tmp_path / "profiles"))
        monkeypatch.setattr(main, "_RUNNER", None)
        client = TestClient(main.app)
        tok = client.post("/api/register", json={"email": "p2@test.ru",
                                                 "password": "password123"}).json()["token"]
        h = {"Authorization": "Bearer " + tok}
        text = ("Иван Петров — C#/.NET разработчик. Опыт 4 года. Стек: C#, ASP.NET, "
                "PostgreSQL, Docker. Город: Ростов-на-Дону.")
        r = client.post("/api/profile/parse", json={"text": text}, headers=h)
        assert r.status_code == 200
        d = r.json()["draft"]
        assert "c#" in [s.lower() for s in d["skills"]]
        assert d["location"] == "Ростов-на-Дону"
        assert d["experience_years"] == 4
        # слишком короткий текст — отказ
        assert client.post("/api/profile/parse", json={"text": "мало"},
                           headers=h).status_code == 400

    def test_work_places_saved_per_company(self, tmp_path, monkeypatch):
        """Опыт работы хранится по местам: пары «компания + опыт»,
        зеркала companies/summary/work_experience выводятся из них."""
        from fastapi.testclient import TestClient
        from app.server import store
        import app.server.main as main
        monkeypatch.setattr(store, "USERS_DB", str(tmp_path / "users.db"))
        monkeypatch.setattr(config, "DATA", str(tmp_path))
        monkeypatch.setattr(config, "PROFILES_DIR", str(tmp_path / "profiles"))
        monkeypatch.setattr(main, "_RUNNER", None)
        client = TestClient(main.app)
        tok = client.post("/api/register", json={"email": "wp@test.ru",
                                                 "password": "password123"}).json()["token"]
        h = {"Authorization": "Bearer " + tok}
        places = [{"company": "Т-Банк", "experience": "Делал банковские сервисы."},
                  {"company": "Экситон", "experience": "Писал приложения на .NET."}]
        r = client.put("/api/profile", json={"title": "Разработчик",
                                             "work_places": places,
                                             "skills": ["C#"]}, headers=h)
        assert r.status_code == 200
        saved = json.load(open(tmp_path / "profiles" / "u1.json", encoding="utf-8"))
        assert [p["company"] for p in saved["work_places"]] == ["Т-Банк", "Экситон"]
        assert saved["companies"] == ["Т-Банк", "Экситон"]
        assert "банковские сервисы" in saved["summary"] and ".NET" in saved["summary"]
        assert saved["work_experience"] == saved["summary"]  # зеркало
        # старый формат (одиночное summary + companies) тоже принимается
        r = client.put("/api/profile", json={"title": "Разработчик",
                                             "summary": "Один текст опыта.",
                                             "companies": ["ООО Пример"]}, headers=h)
        assert r.status_code == 200
        saved = json.load(open(tmp_path / "profiles" / "u1.json", encoding="utf-8"))
        assert saved["work_places"] == [] and saved["summary"] == "Один текст опыта."
