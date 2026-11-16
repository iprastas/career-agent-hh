# -*- coding: utf-8 -*-
"""Регрессии багов, найденных при тестировании 16.11.2026.

- матчинг: роль тимлида/архитектора и стаж из описания учитываются, опыт из
  структурного резюме (PDF -> experience[]) читается, профессия в названии сверяется;
- поиск: город/профессия в запросе не превращаются в фильтр по компании, город и
  опыт нормализуются, «C#» не режется FTS до «c»;
- зарплаты: алиасы навыков («С#» кириллицей, «.NET Core», «Postgres»);
- напоминания и заметки трекера; маршрутизация мультиагента.
"""
import datetime as dt
import os

import pytest

from app import config
from tools import experience_matching as em
from tools import match_gap


def _profile(**kw):
    p = {"id": "t", "title": "C#/.NET-разработчик", "skills": {"c#": "core", ".net": "core"},
         "location": "Казань", "expected_net": 100000, "summary": "", "experience_years": 3}
    p.update(kw)
    return p


def _vac(name, description="", experience="between1And3", **kw):
    v = {"vacancy_id": "1", "name": name, "_skills": {"c#", ".net"}, "city": "Казань",
         "schedule": "", "experience": experience, "salary_from": 150000, "salary_to": None,
         "salary_currency": "RUR", "salary_gross": False, "description": description}
    v.update(kw)
    return v


class TestExperienceMatching:
    def test_team_lead_without_leadership_ranks_below_developer(self):
        prof = _profile()
        lead = _vac("Team lead (C#/.net)", requirement_snippet="Опыт в роли Team Lead от 3х лет")
        dev = _vac("C#/.NET-разработчик")
        assert match_gap.score_vacancy(lead, prof)[0] < 0.6 * match_gap.score_vacancy(dev, prof)[0]
        notes = em.assess(lead, prof)[2]
        assert any("руковод" in n for n in notes)

    def test_team_lead_ok_when_profile_has_leadership(self):
        prof = _profile(experience_years=7, summary="Тимлид команды из 5 разработчиков")
        lead = _vac("Team lead (C#/.net)", requirement_snippet="Опыт в роли Team Lead от 3х лет")
        assert not any("руковод" in n for n in em.assess(lead, prof)[2])

    def test_years_from_description_compared(self):
        v = _vac("C# разработчик", "Требования: опыт коммерческой разработки от 5 лет")
        assert em.required_years(v) == 5
        f_junior = em.assess(v, _profile(experience_years=2))[0]
        f_senior = em.assess(v, _profile(experience_years=6))[0]
        assert f_junior < f_senior

    def test_optional_years_ignored(self):
        v = _vac("C# разработчик", "Будет плюсом: опыт от 5 лет в финтехе", experience="")
        assert em.required_years(v) == 0

    def test_senior_profile_not_penalized_for_senior_vacancy(self):
        prof = _profile(experience_years=8, title="Senior C# разработчик")
        v = _vac("Senior C# разработчик", experience="moreThan6")
        assert em.assess(v, prof)[0] == pytest.approx(1.0)

    def test_other_profession_penalized(self):
        prof = _profile()
        content = _vac("Контент-менеджер сайта (ASP.NET MVC)")
        assert em.assess(content, prof)[0] < 0.7

    def test_structured_experience_is_evidence(self):
        """PDF-резюме хранит опыт в experience[] — раньше он не читался вовсе."""
        prof = _profile(experience=[{"company": "Банк", "role": "Разработчик",
                                     "highlights": ["Разрабатывал микросервисы на .NET"]}])
        v = _vac("C# разработчик", "Требования: опыт разработки микросервисов обязателен")
        ev = em.experience_evidence(v, prof)
        assert [r["category"] for r in ev["matched_requirements"]] == ["microservices"]


needs_db = pytest.mark.skipif(not os.path.exists(config.APP_DB), reason="data/app.db не собран")


@needs_db
class TestSearchRegressions:
    def test_city_word_is_not_employer(self):
        from app.tools import search_vacancies
        r = search_vacancies("C# разработчик Ростов", limit=5)
        assert r["employer_filter"] == ""
        assert all(v["city"] == "Ростов-на-Дону" for v in r["vacancies"])

    def test_profession_word_is_not_employer(self):
        from app.tools import search_vacancies
        assert search_vacancies("водитель такси", limit=3)["employer_filter"] == ""
        assert search_vacancies("вакансии аналитика", limit=3)["employer_filter"] == ""

    def test_employer_in_prepositional_case(self):
        from app.tools import search_vacancies
        r = search_vacancies("вакансии в Яндексе", limit=4)
        assert r["employer_filter"] == "яндекс" and r["vacancies"]
        assert all("яндекс" in v["employer"].lower() for v in r["vacancies"])

    def test_city_and_experience_normalized(self):
        from app.tools import search_vacancies
        a = search_vacancies("C# разработчик", city="Москва", experience="от 1 года до 3 лет")
        b = search_vacancies("C# разработчик", city="москва", experience="1-3 года")
        assert a["found"] == b["found"] > 0

    def test_csharp_not_cut_to_c(self):
        from app.tools import search_vacancies
        r = search_vacancies("C# разработчик", limit=5)
        assert all("c#" in v["name"].lower() or "c#" in v["top_skills"] or ".net" in v["name"].lower()
                   for v in r["vacancies"])

    def test_remote_filter_works_without_schedule_column(self):
        from app.tools import search_vacancies
        assert search_vacancies("разработчик", schedule="удаленно")["found"] > 0


@needs_db
class TestSalaryRegressions:
    @pytest.mark.parametrize("alias,canon", [("С#", "C#"), (".NET Core", ".NET"),
                                             ("Postgres", "PostgreSQL")])
    def test_skill_aliases(self, alias, canon):
        from app.tools import salary_stats
        a, b = salary_stats(skill=alias), salary_stats(skill=canon)
        assert a["n"] == b["n"] > 0

    def test_city_case_insensitive(self):
        from app.tools import salary_stats
        assert salary_stats(city="москва")["n"] == salary_stats(city="Москва")["n"] > 0


def test_foreign_profile_id_is_ignored_in_user_context(tmp_path, monkeypatch):
    """Из контекста пользователя нельзя прочитать чужое резюме (u<id>), а «anastasia»,
    подставленная моделью, заменяется собственным резюме пользователя."""
    import types as _t
    from app.tools.match import resolve_profile_id
    monkeypatch.setattr(config, "PROFILES_DIR", str(tmp_path))
    (tmp_path / "u7.json").write_text("{}", encoding="utf-8")
    ctx = _t.SimpleNamespace(user_id="u7")
    assert resolve_profile_id("u1", ctx) == "u7"
    assert resolve_profile_id("anastasia", ctx) == "u7"
    assert resolve_profile_id("выдуманный-id", ctx) == "u7"
    assert resolve_profile_id("junior", ctx) == "junior"


@needs_db
def test_example_profiles_resolve():
    from app.tools import match_profile, skill_gap
    for pid in ("junior", "mid", "senior"):
        assert "error" not in match_profile(pid, top_n=1)
        assert "error" not in skill_gap(pid, top_n=1)


class TestFollowUp:
    def test_words_and_dates(self):
        from app.tools.letters import parse_follow_up
        today = dt.date.today()
        assert parse_follow_up("через две недели") == (today + dt.timedelta(days=14)).isoformat()
        assert parse_follow_up("завтра") == (today + dt.timedelta(days=1)).isoformat()
        assert parse_follow_up("2026-13-45") == ""


def test_tracker_status_change_keeps_note(tmp_path, monkeypatch):
    from app.tools import letters
    monkeypatch.setattr(config, "TRACKER_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "APP_DB", str(tmp_path / "none.db"))
    import sqlite3
    sqlite3.connect(config.APP_DB).execute(
        "CREATE TABLE vacancies (vacancy_id TEXT, name TEXT)").connection.commit()
    letters.track_response("42", "отправлено", "писал рекрутеру")
    rec = letters.track_response("42", "просмотрено", "")["updated"]
    assert rec["status"] == "просмотрено" and rec["note"] == "писал рекрутеру"


def test_claim_filter_allows_quoting_tool_output():
    """Заметка из трекера «письмо отправлено работодателю» — цитата, не заявление агента;
    то же заявление от самой модели по-прежнему блокируется."""
    from app import guardrails
    ans = "Статус: отправлено. Заметка: письмо отправлено работодателю."
    tool = "{'responses': [{'note': 'письмо отправлено работодателю'}]}"
    assert guardrails.post_filter(ans, "", tool) == ans
    assert guardrails.post_filter(ans, "", "") != ans


def test_leak_filter_ignores_resume_text():
    """Цитата опыта из резюме в письме — не «утечка промпта»."""
    from app import guardrails
    from app.system_prompt import all_instructions, profile_summary
    letter = profile_summary("anastasia")
    assert guardrails.post_filter(letter, all_instructions()) == letter


@pytest.mark.parametrize("text,agent", [
    ("Какая медианная зарплата C# разработчика в Москве?", "analytics_agent"),
    ("Какие навыки мне стоит подтянуть?", "analytics_agent"),
    ("Подбери мне 3 подходящие вакансии под моё резюме", "search_agent"),
    ("Подбери вакансии с зарплатой от 150 000", "search_agent"),
    ("Что работодатели требуют от junior-разработчика .NET?", "search_agent"),
    ("Напиши сопроводительное письмо на вакансию 138490740", "writer_agent"),
    ("Отметь отклик на вакансию 1 как отправленный", "writer_agent"),
    ("Привет! Что ты умеешь?", ""),
])
def test_router(text, agent):
    from app.router import route
    assert route(text) == agent


def test_router_callback_transfers_without_model():
    from google.adk.models import LlmRequest
    from google.genai import types
    from app.router import route_callback
    req = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="Какая зарплата у python?")])])
    resp = route_callback(None, req)
    fc = resp.content.parts[0].function_call
    assert fc.name == "transfer_to_agent" and fc.args == {"agent_name": "analytics_agent"}
    # продолжение после ответа инструмента — решает модель
    req2 = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(name="x", response={}))])])
    assert route_callback(None, req2) is None


@needs_db
def test_fallback_tool_when_model_answers_without_tool():
    """llama-server не всегда соблюдает tool_choice=required — текстовый ответ
    первого шага заменяется вызовом инструмента."""
    from types import SimpleNamespace
    from google.adk.models import LlmRequest, LlmResponse
    from google.genai import types
    from app import router
    assert router.fallback_call("analytics_agent", "Какая зарплата C# в Москве?") == \
        ("salary_stats", {"skill": "c#", "city": "Москва"})
    assert router.fallback_call("analytics_agent", "Каких навыков не хватает?")[0] == "skill_gap"
    assert router.fallback_call("search_agent", "Подбери вакансии под моё резюме")[0] == "match_profile"
    ctx = SimpleNamespace(invocation_id="i1", agent_name="analytics_agent",
                          user_content=types.Content(role="user", parts=[types.Part(
                              text="Какая медианная зарплата python?")]))
    req = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="x")])])
    req.tools_dict = {"salary_stats": object()}
    router.require_tool_first(ctx, req)
    text_only = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="165 000 ₽")]))
    fc = router.ensure_tool_called(ctx, text_only).content.parts[0].function_call
    assert fc.name == "salary_stats" and fc.args["skill"] == "python"
    # второй шаг (после инструмента) — ответ не трогаем
    assert router.ensure_tool_called(ctx, text_only) is None


def test_profile_summary_reads_structured_pdf_experience(tmp_path, monkeypatch):
    """Опыт из PDF-резюме (experience[]) попадает в промпт — без него модель
    придумывала проекты в письмах."""
    import json
    from app import system_prompt
    monkeypatch.setattr(config, "PROFILES_DIR", str(tmp_path))
    (tmp_path / "u5.json").write_text(json.dumps({
        "name": "Стоянова Анастасия", "title": "Dev", "experience": [{"company": "Экситон", "role": "Инженер-программист",
                                        "highlights": ["Миграция Oracle → PostgreSQL"]}]},
        ensure_ascii=False), encoding="utf-8")
    s = system_prompt.profile_summary("u5")
    assert "Экситон" in s and "Миграция Oracle" in s and "Анастасия" in s


def test_subagent_instruction_has_profile_and_letter_rules():
    from types import SimpleNamespace
    from app.system_prompt import sub_instruction
    w = sub_instruction("writer_agent")(SimpleNamespace(user_id="anastasia"))
    assert "Правила письма" in w and "Профиль пользователя" in w
    s = sub_instruction("search_agent")(SimpleNamespace(user_id="nobody-x"))
    assert "Правила письма" not in s and "не заполнен" in s


def test_subagents_return_control_to_coordinator(monkeypatch):
    """Следующий вопрос маршрутизирует координатор, а не последний суб-агент."""
    monkeypatch.setattr(config, "llm_mode", lambda: config.LLM_MODE_LOCAL)
    from app.agent import build_agent
    root = build_agent()
    for sub in root.sub_agents:
        assert sub.disallow_transfer_to_parent and sub.disallow_transfer_to_peers
