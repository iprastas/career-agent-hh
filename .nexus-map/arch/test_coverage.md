> generated_by: nexus-mapper v2
> verified_at: 2026-11-01
> provenance: AST-backed except where explicitly marked inferred; tests were NOT executed (read-only mapping)

# Статический тестовый слой

## Что есть

`tests/` — 6 файлов pytest (AST: 19 тестовых классов, 75 функций, 656 строк). Классы из AST: `TestPreFilter`, `TestPostFilter`, `TestExtractAndTitle`, `TestCompaction`, `TestTopicGate`, `TestEvalsChecks`, `TestSemantic`, `TestFreshAndTrend` и ещё 11.

| Файл | Покрывает (по именам и импортам) |
|---|---|
| `tests/conftest.py` | общие фикстуры *(manual)* |
| `tests/test_guardrails.py` | guardrails: инъекции RU/EN, утечка промпта, запрещённые заявления об отправке |
| `tests/test_memory.py` | сжатие истории, заголовки диалогов (`app/memory.py`) |
| `tests/test_server.py` | сервер: авторизация, изоляция пользователей, SSE-конверт, диалоги, письма (импортирует `app.server.main`, `app.tools.letters`) |
| `tests/test_tools.py` | инструменты: лимиты выдачи, фильтры, квантили, локальные записи |
| `tests/test_new_features.py` | новые функции: семантика, свежие, динамика зарплат, напоминания, профиль, evals (фан-аут 7 внутренних модулей) |

Импортная поверхность тестов (AST): `app`, `app.server`, `app.tools` — т.е. тесты направлены на ядро, инструменты и сервер.

## Заявленное покрытие (не подтверждалось запуском)

README.md и PLAN.md (01.11.2026) заявляют **67 зелёных pytest-тестов**; PLAN.md фиксирует рост 26 → 38 → 50 → 67 по этапам. Команда запуска: `.venv\Scripts\python -m pytest tests/ -q` *(verified_at: 2026-11-01, из документации)*.

## Evidence gaps

- **Тесты не запускались** при генерации карты (read-only-протокол): «67 зелёных» — утверждение README/PLAN.md, а не результат прогона.
- **`tools/` (конвейер данных) не покрыт тестами** — ни один тестовый файл не импортирует `tools.collect_hh`, `clean_merge`, `build_db`, `daily_increment` и др. Их корректность подтверждается только самопроверками в `build_db.py --stats` и живыми прогонами (PLAN.md).
- **`app/evals.py`** покрыт косвенно (`TestEvalsChecks` в `test_new_features.py`), но LLM-as-judge и сам прогон `run_evals.py` — вне pytest-контура.
- Нет измерения line/branch coverage (нет конфигурации coverage в репозитории).
- Evals-эталон (`evals/benchmark.json`) — отдельный от pytest контур качества (см. systems.md, §5).

## Вывод

Тестовый слой сконцентрирован на guardrails, инструментах, сервере и новых функциях агента; конвейер данных (`tools/`) — главный разрыв статического тестового покрытия.
