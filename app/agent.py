# -*- coding: utf-8 -*-
"""
Сборка ADK-агента: мультиагентная архитектура «координатор + 3 суб-агента».

- career_agent (координатор/роутер): системный промпт с guardrails, без инструментов,
  передаёт запрос через transfer_to_agent;
- search_agent — поиск/подбор вакансий (FTS + семантика + похожие + свежие);
- analytics_agent — навыки, зарплаты и их динамика;
- writer_agent — письма (локально) и учёт откликов.

Изоляция по темам усилена: каждый суб-агент знает только свои инструменты и отвечает
отказом вне своей области. Слои guardrails (промпт + пост-фильтр) действуют на всё
дерево — тексты всех инструкций собираются в system_prompt.all_instructions().

Порядок важен: config.apply_env() экспортирует ключи провайдера ДО импорта litellm/ADK.
Имя модели передаётся с префиксом провайдера ("openai/<MODEL>") — реестр ADK резолвит
по префиксу, без него падает 'Model <name> not found'.
"""
from . import config

config.apply_env()  # noqa: E402  обязательно до импорта litellm

from google.adk.agents import LlmAgent  # noqa: E402
from google.adk.models import LLMRegistry  # noqa: E402
from google.adk.models.lite_llm import LiteLlm  # noqa: E402

from .tools import (draft_letter, fresh_vacancies, match_profile,  # noqa: E402
                    salary_stats, salary_trend, search_vacancies, semantic_search,
                    similar_vacancies, skill_gap, track_response, vacancy_details)
from .router import ensure_tool_called, require_tool_first, route_callback  # noqa: E402
from .system_prompt import dynamic_instruction, sub_instruction  # noqa: E402

AGENT_NAME = "career_agent"
APP_NAME = "career_agent_app"


def build_agent(profile_id=""):
    """Координатор + search_agent / analytics_agent / writer_agent.

    Инструкция координатора — callable: резюме подставляется из профиля текущего
    пользователя (system_prompt.dynamic_instruction), а не общего «anastasia»."""
    model_id = config.model_name()
    LLMRegistry.resolve(model_id)  # fail fast: неверный префикс/модель
    # явный endpoint: в local-режиме — только llama-server, независимо от окружения/.env
    # таймаут шага: CPU-модель на слабом ноутбуке считает длинный промпт минутами
    model = LiteLlm(model=model_id, timeout=config.llm_timeout(600),
                    **config.agent_gen_kwargs(), **config.llm_kwargs())

    # Суб-агент отвечает и завершает ход; следующий вопрос снова маршрутизирует
    # координатор. Без этого ADK оставлял диалог у последнего суб-агента: вопрос про
    # зарплату после подбора вакансий попадал в search_agent (без salary-инструментов)
    leaf = dict(disallow_transfer_to_parent=True, disallow_transfer_to_peers=True,
                # сначала инструмент, потом ответ: цифры только из выдачи инструментов
                before_model_callback=require_tool_first,
                after_model_callback=ensure_tool_called)
    search_agent = LlmAgent(
        name="search_agent", model=model, **leaf,
        instruction=sub_instruction("search_agent"),
        description="Поиск и подбор вакансий: FTS и семантический поиск, карточки, "
                    "похожие, свежие, матчинг под профиль",
        tools=[search_vacancies, semantic_search, similar_vacancies, vacancy_details,
               match_profile, fresh_vacancies])
    analytics_agent = LlmAgent(
        name="analytics_agent", model=model, **leaf,
        instruction=sub_instruction("analytics_agent"),
        description="Навыки и разрыв в навыках, статистика и динамика зарплат",
        tools=[skill_gap, salary_stats, salary_trend])
    writer_agent = LlmAgent(
        name="writer_agent", model=model, **leaf,
        instruction=sub_instruction("writer_agent"),
        description="Черновики писем работодателям (локально) и учёт откликов",
        tools=[draft_letter, track_response])

    return LlmAgent(
        name=AGENT_NAME,
        model=model,
        instruction=dynamic_instruction,
        description="Карьерный агент: координатор — вакансии hh.ru, навыки и разрыв, "
                    "зарплаты, письма, отклики",
        sub_agents=[search_agent, analytics_agent, writer_agent],
        # однозначные запросы маршрутизируются правилами, остальные — моделью
        before_model_callback=route_callback,
    )
