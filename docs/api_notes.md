# hh.ru API — рабочие заметки (проверено живыми запросами 30.10.2026)

Источник: https://api.hh.ru/openapi/redoc (спецификация: https://api.hh.ru/openapi/specification/public)

## Транспорт
- `https://api.hh.ru/`, JSON, даты ISO 8601, gzip.
- Обязательный заголовок `HH-User-Agent` (или `User-Agent`), формат `MyApp/1.0 (feedback@example.com)` —
  без него `400`. При наличии обоих обрабатывается `HH-User-Agent`.
- Авторизация: `Authorization: Bearer <token>`. Наш токен — `auth_type=application` (см. `GET /me`).

## Доступно с нашим токеном (HTTP 200)
| Путь | Заметки |
|---|---|
| `GET /vacancies` | `text`, `area`, `experience`, `employment`, `schedule`, `work_format`, `professional_role`, `salary`+`currency`, `label=with_salary`, `period=N` **или** `date_from`+`date_to` (вместе нельзя), `order_by=publication_time`, `clusters=true` (+`per_page=0` → только кластеры), `describe_arguments=true`, `excluded_text`, `per_page≤100` |
| `GET /vacancies/{id}` | карточка: `description`, `key_skills`, `snippet`, `salary`, `employer`, `published_at` |
| `GET /vacancies/{id}/related_vacancies` | похожие (пути именно `related_vacancies` / `similar_vacancies`) |
| `GET /vacancies/{id}/similar_vacancies` | то же, второй тип выдачи |
| `GET /dictionaries` | все справочники (`experience`, `employment`, `schedule`, `work_format`, `vacancy_search_order`, `vacancy_label`…) |
| `GET /areas`, `/areas/{id}` | регионы (Москва = 1, СПб = 2, Россия = 113) |
| `GET /industries`, `/professional_roles` | индустрии и профроли |
| `GET /employers/{id}` | карточка работодателя |
| `GET /suggests/skill_set?text=C%23` | id ключевых навыков (C# → 230) |
| `GET /skills?id=230&id=1413` | навыки **по id**, ≤50 за запрос; без `id` → 400 |
| `GET /locales`, `/metro` | справочники для UI |

## Недоступно (и нам не нужно) — HTTP 403 `user_auth_expected`
- `GET /resumes?...` — поиск резюме (персональные данные)
- `GET /negotiations`, `/chats`, `POST /resumes/*/apply` — отклики и переписка
- `GET /salary_statistics/paid/salary_evaluation/{area_id}` — платный банк зарплат (требует user-токен
  с доступом к отчётам salary.hh.ru). Статистику считаем сами по своему датасету.

## Не использовать в принципе (по заданию проекта)
Все `POST`/`PUT`/`DELETE`: публикация и редактирование вакансий, черновики, отклики, сообщения в чаты,
комментарии, вебхуки, «отметить прочитанными».

## Ограничения выдачи
- `page` нумеруется с 0, `per_page ≤ 100`, дефолт 20.
- **Глубина ≤ 2000**: `per_page=10&page=199` — OK, `page=200` — ошибка. Дробить по `experience`/`area`/`period`.
- Без токена на `/vacancies` после первого запроса приходит капча — у нас токен есть.
- `500/503/429` возможны → повторы с растущей паузой (реализовано в `tools/collect_hh.py`).
- Числовых rate-лимитов в спецификации нет — держим `sleep` между запросами и не шлём пачки.

## Хелпер
`tools/hh_client.py` — тонкая обёртка (только GET, токен из `.env`, ретраи) для бэкенда и скриптов.