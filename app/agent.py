# -*- coding: utf-8 -*-
"""
Сборка ADK-агента: LlmAgent + read-only инструменты + системный промпт (guardrails слой 2).

Порядок важен: config.apply_env() экспортирует ключи провайдера ДО импорта litellm/ADK.
Имя модели передаётся с префиксом провайдера ("openai/<MODEL>") — реестр ADK резолвит
по префиксу, без него падает 'Model <name> not found'.
"""
from . import config

config.apply_env()  # noqa: E402  обязательно до импорта litellm

from google.adk.agents import LlmAgent  # noqa: E402
from google.adk.models import LLMRegistry  # noqa: E402

from .tools import ALL_TOOLS  # noqa: E402
from .system_prompt import build_instruction  # noqa: E402

AGENT_NAME = "career_agent"
APP_NAME = "career_agent_app"


def build_agent(profile_id="anastasia"):
    """LlmAgent с семью инструментами и системным промптом (профиль — в инструкции один раз)."""
    model = config.model_name()
    LLMRegistry.resolve(model)  # fail fast: неверный префикс/модель
    return LlmAgent(
        name=AGENT_NAME,
        model=model,
        instruction=build_instruction(profile_id),
        description="Карьерный агент: вакансии hh.ru, навыки и разрыв, зарплаты, письма, отклики",
        tools=list(ALL_TOOLS),
    )
