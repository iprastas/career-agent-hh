> generated_by: nexus-mapper v2
> verified_at: 2026-11-01
> provenance: AST-backed except where explicitly marked inferred

# Системы и границы

## 1. agent-core — ядро агента

**Граница:** `app/` без `app/server/` и `app/tools/`: `config.py`, `agent.py`, `guardrails.py`, `system_prompt.py`, `topic_gate.py`, `memory.py`, `event_protocol.py`, `letter_writing.py`, `runner_cli.py`, `__init__.py`. *(дополнено вручную 2026-11-04: letter_writing.py — промпты писем, политика отделена от данных)*

**Ответственность:** сборка ADK `LlmAgent` (координатор + суб-агенты search/analytics/writer — PLAN.md, этап «Расширение 01.11.2026»), guardrails в три слоя (1 — детерминированный пред-фильтр инъекций `guardrails.py`; 1.5 — классификатор тем на TF-IDF-центроидах `topic_gate.py`; 2 — системный промпт с белым списком тем `system_prompt.py`; 3 — пост-фильтр утечек промпта и заявлений об отправке `guardrails.py`), сжатие истории длинных диалогов в сводку (`memory.py`, детерминированный фолбэк без модели), конверт событий refusal/status/tool_call/think/delta/answer/done (`event_protocol.py`).

**Точки входа:** `agent.py: build_agent, APP_NAME`; консольный прогон `python -m app.runner_cli`.

**Факты:** `config.py` — базовый модуль уровня 0, импортируется почти всеми (проверено чтением исходников); модель резолвится реестром ADK только с префиксом `openai/<MODEL>` (PLAN.md, проверено живьём).

## 2. agent-tools — инструменты агента

**Граница:** `app/tools/` (7 модулей + `__init__.py`).

**Ответственность:** 7 read-only инструментов поверх `data/app.db` (`PRAGMA query_only`, сети нет): `search_vacancies`/`vacancy_details` (FTS5), `semantic_search`/`similar_vacancies` (TF-IDF+LSA, гибрид с лексикой), `match_profile`/`skill_gap`, `salary_stats`/`salary_trend` (P25/медиана/P75 net + динамика по месяцам), `fresh_vacancies` (свежие за N дней под профиль), `draft_letter` (→ `data/outbox/`), `track_response` (→ `data/tracker.db`).

**Факты:** `fresh.py` переиспользует методику скоринга `match.py` (комментарий в исходнике ссылается на `tools/match_gap.py` — общая карта алиасов навыков между app/ и tools/ — *inferred from manual inspection*); `salary.py` импортирует константы `EXP_MAP, GROSS_K` из `search.py`.

## 3. server-ui — сервер и интерфейс

**Граница:** `app/server/` (`main.py`, `store.py`, `static/app.js`, `static/index.html`, `static/style.css`).

**Ответственность:** FastAPI (async): auth/регистрация (PBKDF2-HMAC-SHA256+соль, в БД только хэш токенов — `store.py`: `users.db`, `sessions.db`), SSE-чат с событиями хода рассуждений и вызовов инструментов, REST: диалоги (переименование/удаление, пустые не сохраняются), письма (правка/регенерация/скачивание), отклики с напоминаниями и экспортом CSV, резюме соискателя (per-user `u<id>.json`: форма + разбор PDF/TXT, изоляция по user_id), словарь навыков (`GET /api/skills` из `vacancy_skills`; навыки в профиль — только из словаря, иначе 422). *(дополнено вручную 2026-11-04)* Изоляция данных по `user_id` из токена. Фронтенд — vanilla JS: вкладки Вакансии/Зарплаты/Навыки/Письма/Отклики в шапке (десктоп) и бургер-меню (мобильные), выдвижная панель диалогов, модальные окна, комбобоксы городов.

**Точка входа:** `uvicorn app.server.main:app` (`run_server.bat`, порт 8000).

## 4. data-pipeline — конвейер данных

**Граница:** `tools/` кроме `run_evals.py`: `hh_client.py`, `collect_hh.py`, `clean_merge.py`, `build_db.py`, `build_semantic.py`, `daily_increment.py`, `match_gap.py`, `experience_matching.py`, `parse_resume.py`, `stats_preview.py`, `check_token.py`; точки запуска `daily_collect.bat`. *(дополнено вручную 2026-11-04: experience_matching.py — общий счёт «требования вакансии ↔ опыт работы»)*

**Ответственность:** сбор вакансий с hh API (application-токен, только GET; списки → JSONL/CSV, `--details` — описания, дедуп, ретраи на 429/5xx), склейка трёх слоёв Kaggle+API в `data/clean/vacancies_unified.csv` (курсы ЦБ на фиксированную дату, канонизация опыта, chunked-чтение 1.5 ГБ CSV), сборка `data/app.db` (FTS5, `vacancy_skills`, `salary_agg`; `--full`/`--incremental`), семантический индекс (`data/semantic_model.joblib`, `semantic_tfidf.npz`, `semantic_index.npz`), офлайн-разбор PDF-резюме в профиль, ежедневная цепочка `daily_increment.py` → `data/collect.log`.

**Факты:** `check_token.py` читает/дозаписывает `.env`, токен не печатает (PLAN.md). Автозапуск в Планировщике Windows **не настроен** (расхождение из PLAN.md от 30.10.2026, актуально на 2026-11-01) — сбор вручную.

## 5. evals-qa — оценка качества

**Граница (несколько локаций):** `app/evals.py` (детерминированные метрики, без внешних зависимостей), `tools/run_evals.py` (раннер, импортирует ядро агента), `evals/benchmark.json` (эталонные вопросы).

**Ответственность:** три офлайн-метрики (отказ по теме на off-topic/инъекции; использование ожидаемых инструментов; анти-галлюцинация — каждое число ответа из выдачи инструментов или профиля) + опциональный LLM-as-judge (`--judge`, рубрика 1–5). Отчёт `data/reports/eval_<дата>.md`; сессии строятся во временной БД, рабочие данные не трогаются.

## Вне систем

`tests/` — статический тестовый слой (см. `arch/test_coverage.md`); `docs/api_notes.md` — шпаргалка по API hh.ru; `PLAN.md` — план и журнал решений (статусы на даты 30.10–01.11.2026); корневые CSV/PDF — данные, исключённые из git (`.gitignore`: `data/`, `*.pdf`, `*.db`, `.env`).
