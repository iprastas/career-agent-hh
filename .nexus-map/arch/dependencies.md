> generated_by: nexus-mapper v2
> verified_at: 2026-11-01
> provenance: AST-backed for absolute imports (Python/JavaScript); edges marked *(manual)* come from source inspection (extract_ast.py does not capture relative `from . x` imports); runtime data dependencies marked *(inferred)*

# Зависимости

## Системный уровень

```mermaid
graph TD
    UI[server-ui<br/>FastAPI + vanilla JS]
    CORE[agent-core<br/>ADK LlmAgent + guardrails]
    TOOLS[agent-tools<br/>7 read-only инструментов]
    PIPE[data-pipeline<br/>сбор и сборка данных]
    EVAL[evals-qa<br/>метрики качества]
    DB[(data/app.db + semantic_*<br/>585k+ вакансий)]

    UI -->|build_agent / stream_turn| CORE
    UI -->|REST писем и откликов| TOOLS
    CORE -->|регистрация 7 инструментов| TOOLS
    EVAL -->|прогон эталона через агента| CORE
    TOOLS -.->|читает данные (inferred)| DB
    CORE -.->|профиль из data/profiles (inferred)| DB
    PIPE -->|build_db / build_semantic| DB
```

Сплошные стрелки — кодовые зависимости (проверены AST + чтением исходников); пунктир — runtime-зависимости от данных, которые строит конвейер (*inferred from manual inspection*).

## Слои внутри app/ (направление импортов вниз)

```
server-ui (app/server/main.py) ──► agent-core (agent.py, event_protocol.py, memory.py)
        │                                │
        ├─► agent-tools (letters и др.)  └─► agent-tools (регистрация) ──► search/semantic/match/salary/fresh/letters
        └─► store.py, config.py, guardrails.py
все модули ──► config.py (уровень 0: пути, .env, имя модели)
```

Факты из импортной матрицы *(manual)*:
- `app/server/main.py` — самый широкий фан-аут ядра: импортирует `config, guardrails, agent, event_protocol, memory, tools, tools.letters, store` (`app/server/main.py:27-39`).
- `app/agent.py` импортирует `config`, все `tools`, `system_prompt` (`app/agent.py:19-29`).
- Обратного импорта `app/ → tools/` (корневой пакет сборщика) **нет** — граница конвейера не нарушена (проверено grep, 2026-11-01).
- `tools/run_evals.py` (evals-qa) импортирует `app.config, app.evals, app.agent, app.event_protocol, app.system_prompt` — единственная связь tools/→app/ (*AST-backed*).
- Циклических импортов не обнаружено (топологический порядок config → guardrails/search → tools → memory/event_protocol/system_prompt → agent → server/runner_cli).

## Парные связи между модулями *(manual)*

- `app/tools/fresh.py` → `app/tools/match.py` (методика скоринга) и `app/tools/search.py` (`_row_to_card`).
- `app/tools/salary.py` → `app/tools/search.py` (`EXP_MAP`, `GROSS_K` — общие константы нормализации).
- `app/tools/semantic.py` → `app/tools/search.py` (`_row_to_card`).
- `app/tools/match.py` ↔ `tools/match_gap.py` — **дублирование логики** канонических навыков и базового скоринга между слоями; поправка по опыту вынесена в общий `tools/experience_matching.py` (`adjust_score`), импортируется `tools/match_gap.py` и через него инструментами. *(обновлено вручную 2026-11-04; комментарий в `app/tools/fresh.py:13`)*

## Внешние зависимости (по импортам AST)

- **Ядро:** `google.adk` (LlmAgent, LLMRegistry), `litellm` (провайдер openai-совместимый).
- **Сервер:** `fastapi`, `uvicorn`, `starlette`, `pydantic`, `sqlalchemy`/`aiosqlite` (сессии ADK), `httpx`.
- **Конвейер:** `requests`, `pandas`, `numpy` (PLAN.md: версии в `.venv`, Python 3.11.9).
- **Семантика:** TF-IDF+LSA (`joblib`-модель в `data/semantic_model.joblib`).

## Поток данных (runtime)

`daily_collect.bat` → `collect_hh.py` (hh API) → `clean_merge.py` (`data/clean/vacancies_unified.csv`, 585 435 строк) → `build_db.py` (`data/app.db`) → `build_semantic.py` (`data/semantic_*`) → инструменты агента читают БД read-only; `app/server/store.py` пишет `users.db`/`sessions.db`, `draft_letter`/`track_response` пишут `data/outbox/` и `data/tracker.db`.
