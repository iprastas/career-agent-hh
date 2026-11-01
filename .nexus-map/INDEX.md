> generated_by: nexus-mapper v2
> verified_at: 2026-11-01
> provenance: AST-backed for Python/JavaScript (absolute imports); relative-import edges and runtime data dependencies marked "inferred from manual inspection"; git hotspots non-discriminative (5 commits) — see hotspots/git_forensics.md

# INDEX.md — карьерный AI-агент (вакансии hh.ru)

Экзаменационный проект: чат с мультиагентом ADK, который подбирает вакансии из локального датасета (585k+ вакансий, SQLite FTS5), считает матчинг профиля и разрыв навыков, зарплатную статистику, готовит письма и ведёт трекер откликов. Только чтение hh.ru (application-токен), никакой автоматической отправки.

**Стек:** Python 3.11 (`.venv`), Google ADK + litellm, FastAPI + uvicorn, SQLite (FTS5, WAL), vanilla JS, pytest. **Запуск:** `run_server.bat` → http://127.0.0.1:8000.

## Системы

| Система | Где | Что делает |
|---|---|---|
| agent-core | `app/` (без server/, tools/) | Сборка LlmAgent (координатор + суб-агенты), guardrails-слои 1/1.5/3, сжатие памяти, конверт событий |
| agent-tools | `app/tools/` | 7 read-only инструментов: поиск, семантика, матчинг, skill_gap, зарплаты, свежие, письма/отклики |
| server-ui | `app/server/` | FastAPI: auth (PBKDF2), SSE-чат, REST, изоляция по user_id; vanilla JS фронтенд |
| data-pipeline | `tools/` (кроме run_evals.py) | Сбор hh API → склейка слоёв → SQLite+FTS5 → семантический индекс; `daily_collect.bat` |
| evals-qa | `app/evals.py` + `tools/run_evals.py` + `evals/` | Метрики качества (отказы, инструменты, анти-галлюцинация), LLM-as-judge |

Ключевые точки входа: `app/server/main.py` (REST+SSE), `app/agent.py` (сборка агента), `tools/daily_increment.py` (обновление датасета), `tools/run_evals.py` (прогон эталона).

## Данные (не в git, восстанавливаются сборщиком)

`data/app.db` — 585k+ вакансий + FTS5 + salary_agg; `data/semantic_*` — TF-IDF+LSA; `data/profiles/` — профиль соискателя (PDF-резюме офлайн, `tools/parse_resume.py`); `data/users.db`, `data/sessions.db`, `data/tracker.db`, `data/outbox/<user>/`; слои датасета: `kaggle_raw_2023` + `api_live_2026_10` + `kaggle_it_2026_09`.

## Evidence gaps

- **git-история: всего 5 коммитов, 1 автор** — ранги хотспотов и coupling-пар неразличимы (все файлы менялись вместе), границы систем построены по AST и исходникам, не по git.
- **Относительные импорты** (`from . import config` и т.п.) AST-экстрактор не фиксирует — внутренние зависимости app/ проверены чтением исходников (`inferred from manual inspection` в dependencies.md).
- **Тесты не запускались** при картировании (read-only): заявлено 67 pytest-тестов (README, PLAN.md от 01.11.2026) — см. `arch/test_coverage.md`.
- **Статус этапов** из PLAN.md (этапы 1–5 выполнены 31.10–01.11.2026) — сверено с документом на 2026-11-01, не является текущим фактом позже этой даты.

## [操作指南] 强制执行步骤

> 本节是对所有读取本文件的 AI 发出的硬性操作指令，不是建议。

### 步骤1 — 必须先读完以下所有文件（顺序不限）

读完本文件后，在执行任何任务之前，必须依次 read 以下文件完整内容：

- `.nexus-map/arch/systems.md` — 系统边界与代码位置
- `.nexus-map/arch/dependencies.md` — 系统间依赖关系与 Mermaid 图
- `.nexus-map/arch/test_coverage.md` — 测试面与证据缺口
- `.nexus-map/hotspots/git_forensics.md` — Git 热点与耦合风险
- `.nexus-map/concepts/domains.md` — 核心领域概念

> 这些文件均为高密度摘要，总量通常 < 5000 tokens，是必要的上下文成本。
> 不得以"任务简单"或"只改一个文件"为由跳过。

### 步骤2 — 按任务类型追加操作（步骤1 完成后执行）

- 若任务涉及**接口修改、新增跨模块调用、删除/重命名公共函数**：
  → 必须运行 `query_graph.py --impact <目标文件>` 确认影响半径后再写代码。
- 若任务需要**判断某文件被谁引用**：
  → 运行 `query_graph.py --who-imports <模块名>`。
- 若仓库结构已发生重大变化（新增系统、重构模块边界）：
  → 任务完成后评估是否需要重新运行 nexus-mapper 更新知识库。
