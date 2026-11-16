# -*- coding: utf-8 -*-
"""
Детерминированный роутер координатора (before_model_callback) с LLM-фолбэком.

Однозначные запросы («какая зарплата…», «напиши письмо…», «подбери вакансии…»)
передаются суб-агенту без вызова модели координатора: маленькая локальная модель
(Qwen3.5-4B) путалась и отвечала отказом на вопрос о зарплате, а на CPU каждый лишний
вызов — это ~минута. Неоднозначные запросы (совпало несколько тем или ни одной)
решает LLM-координатор, как раньше. Правила прозрачны и покрыты тестами.
"""
import re

_WRITER = re.compile(r"письм|сопроводительн|cover letter|отклик|трекер|напомни|напоминани", re.I)
_SEARCH = re.compile(r"подбер|подобр|найди|найти|поищи|поиск|покажи\s+ваканси|ищу\s+работ|"
                     r"свеж\w*\s+ваканси|похож\w*\s+ваканси|ваканси\w*\s+(?:в|во|от)\s|"
                     r"что\s+(?:\w+\s+)?требу|требу\w*\s+от|требовани", re.I)
_ANALYTICS = re.compile(r"зарплат|зп\b|оклад|медиан|сколько\s+(?:плат|получа|зараб)|доход|"
                        r"навык|skill|разрыв|не\s+хватает|подтянуть|выучить|динамик|тренд", re.I)
_VACANCY = re.compile(r"ваканси", re.I)


def route(text):
    """Имя суб-агента для однозначного запроса или '' (решает LLM-координатор)."""
    t = (text or "").strip()
    if not t:
        return ""
    if _WRITER.search(t):
        return "writer_agent"
    search, analytics = bool(_SEARCH.search(t)), bool(_ANALYTICS.search(t))
    if search:  # «подбери вакансии с зарплатой от 150к» — это подбор, а не статистика
        return "search_agent"
    if analytics:
        return "analytics_agent"
    if _VACANCY.search(t):
        return "search_agent"
    return ""


def _last_user_text(llm_request):
    """Текст последнего сообщения пользователя, если это первый шаг хода
    (а не продолжение после ответа инструмента)."""
    contents = getattr(llm_request, "contents", None) or []
    if not contents:
        return ""
    last = contents[-1]
    if getattr(last, "role", "") != "user":
        return ""
    parts = getattr(last, "parts", None) or []
    if any(getattr(p, "function_response", None) is not None for p in parts):
        return ""
    return " ".join(getattr(p, "text", "") or "" for p in parts).strip()


def require_tool_first(callback_context, llm_request):
    """before_model_callback суб-агентов: первый шаг хода — только вызов инструмента
    (tool_choice=required). Локальная 4B-модель иначе отвечала о зарплатах и разрыве
    навыков «из головы», не вызвав ни одного инструмента, — выдуманные цифры. После
    результата инструмента ограничение снимается и модель пишет ответ."""
    contents = getattr(llm_request, "contents", None) or []
    if not contents or not getattr(llm_request, "tools_dict", None):
        return None
    last = contents[-1]
    for p in getattr(last, "parts", None) or []:
        fr = getattr(p, "function_response", None)
        if fr is not None and getattr(fr, "name", "") != "transfer_to_agent":
            return None  # инструмент уже отработал в этом ходе — свободный ответ
    from google.genai import types
    cfg = llm_request.config or types.GenerateContentConfig()
    cfg.tool_config = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
        mode=types.FunctionCallingConfigMode.ANY))
    llm_request.config = cfg
    inv = getattr(callback_context, "invocation_id", None)
    if inv is not None:  # для after-колбэка: это первый шаг, инструмент обязателен
        _FIRST_STEP[(inv, getattr(callback_context, "agent_name", ""))] = True
        while len(_FIRST_STEP) > 256:
            _FIRST_STEP.pop(next(iter(_FIRST_STEP)))
    return None


_FIRST_STEP = {}


def _user_text(callback_context):
    content = getattr(callback_context, "user_content", None)
    parts = getattr(content, "parts", None) or []
    return " ".join(getattr(p, "text", "") or "" for p in parts).strip()


def fallback_call(agent_name, text):
    """(инструмент, аргументы) по тексту вопроса — если модель не вызвала инструмент.
    Детерминированно: навык и город берутся из текста теми же нормализаторами,
    что и в инструментах. None — подходящего инструмента нет."""
    t = text or ""
    if agent_name == "analytics_agent":
        if re.search(r"навык|skill|разрыв|не\s+хватает|подтянуть|выучить", t, re.I):
            return "skill_gap", {}
        skill, city = _skill_and_city(t)
        if re.search(r"динамик|тренд|менял|по\s+месяц|рост", t, re.I):
            return "salary_trend", {"skill": skill, "city": city}
        return "salary_stats", {"skill": skill, "city": city}
    if agent_name == "search_agent":
        if re.search(r"под\s+(?:мо[её]|мой)|подбер\w*\s+(?:мне|под)|резюме|профил", t, re.I):
            return "match_profile", {}
        return "search_vacancies", {"query": t[:200]}
    if agent_name == "writer_agent" and re.search(r"отклик|трекер|напомин", t, re.I):
        return "track_response", {}
    return None


def _skill_and_city(text):
    import os
    import sqlite3
    import sys
    from . import config
    from .tools.search import _city_in_query
    sys.path.insert(0, os.path.join(config.PROJECT, "tools"))
    import match_gap
    low = (text or "").lower().replace("с#", "c#")
    skills = sorted(match_gap.detect(low))
    skill = next((s for s in skills if s in match_gap.STACK), skills[0] if skills else "")
    tokens = re.findall(r"[\w#+.\-]+", low)
    city = ""
    if os.path.exists(config.APP_DB):
        con = sqlite3.connect(config.APP_DB, timeout=30)
        try:
            city = _city_in_query(con, tokens)[0]
        finally:
            con.close()
    return skill, city


def ensure_tool_called(callback_context, llm_response):
    """after_model_callback суб-агентов: страховка к tool_choice=required (llama-server
    его не всегда соблюдает). Если на первом шаге хода модель ответила текстом без
    инструмента, ответ заменяется вызовом подходящего инструмента — затем модель
    отвечает уже по его выдаче, а не «из головы»."""
    key = (getattr(callback_context, "invocation_id", None),
           getattr(callback_context, "agent_name", ""))
    if not _FIRST_STEP.pop(key, False):
        return None
    content = getattr(llm_response, "content", None)
    parts = getattr(content, "parts", None) or []
    if any(getattr(p, "function_call", None) is not None for p in parts):
        return None
    call = fallback_call(key[1], _user_text(callback_context))
    if not call:
        return None
    from google.adk.models import LlmResponse
    from google.genai import types
    name, args = call
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(name=name, args=args))]))


def route_callback(callback_context, llm_request):
    """before_model_callback координатора: однозначный запрос -> transfer_to_agent
    без вызова модели; иначе None (модель решает сама)."""
    target = route(_last_user_text(llm_request))
    if not target:
        return None
    from google.adk.models import LlmResponse
    from google.genai import types
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(name="transfer_to_agent",
                                         args={"agent_name": target}))]))
