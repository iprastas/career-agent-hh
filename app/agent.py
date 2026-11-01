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

from .tools import (draft_letter, fresh_vacancies, match_profile,  # noqa: E402
                    salary_stats, salary_trend, search_vacancies, semantic_search,
                    similar_vacancies, skill_gap, track_response, vacancy_details)
from .system_prompt import SUB_INSTRUCTIONS, build_instruction  # noqa: E402

AGENT_NAME = "career_agent"
APP_NAME = "career_agent_app"


def build_agent(profile_id="anastasia"):
    """Координатор + search_agent / analytics_agent / writer_agent."""
    model = config.model_name()
    LLMRegistry.resolve(model)  # fail fast: неверный префикс/модель

    search_agent = LlmAgent(
        name="search_agent", model=model,
        instruction=SUB_INSTRUCTIONS["search_agent"],
        description="Поиск и подбор вакансий: FTS и семантический поиск, карточки, "
                    "похожие, свежие, матчинг под профиль",
        tools=[search_vacancies, semantic_search, similar_vacancies, vacancy_details,
               match_profile, fresh_vacancies])
    analytics_agent = LlmAgent(
        name="analytics_agent", model=model,
        instruction=SUB_INSTRUCTIONS["analytics_agent"],
        description="Навыки и разрыв в навыках, статистика и динамика зарплат",
        tools=[skill_gap, salary_stats, salary_trend])
    writer_agent = LlmAgent(
        name="writer_agent", model=model,
        instruction=SUB_INSTRUCTIONS["writer_agent"],
        description="Черновики писем работодателям (локально) и учёт откликов",
        tools=[draft_letter, track_response])

    return LlmAgent(
        name=AGENT_NAME,
        model=model,
        instruction=build_instruction(profile_id),
        description="Карьерный агент: координатор — вакансии hh.ru, навыки и разрыв, "
                    "зарплаты, письма, отклики",
        sub_agents=[search_agent, analytics_agent, writer_agent],
    )
