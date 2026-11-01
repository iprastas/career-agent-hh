# -*- coding: utf-8 -*-
"""
Свежие вакансии под профиль («уведомления о новых вакансиях»): fresh_vacancies.

Датасет пополняется ежедневно (daily_collect.bat) — инструмент показывает вакансии,
опубликованные за последние N дней, отсортированные под профиль тем же прозрачным
скором, что и match_profile (0.45·покрытие + 0.20·название + 0.20·город + 0.15·зарплата).
Только чтение датасета, сети нет.
"""
import sqlite3

from .. import config
from .match import _load_profile, score_vacancy  # единая методика (см. tools/match_gap.py)
from .search import _row_to_card


def fresh_vacancies(profile_id: str = "anastasia", days: int = 7, limit: int = 8):
    """Новые вакансии за последние N дней, отсортированные под профиль соискателя.

    Args:
        profile_id: id профиля ("anastasia", "junior", "mid", "senior").
        days: за сколько последних дней смотреть (1-30).
        limit: сколько вакансий вернуть (1-10).

    Returns:
        dict: found (всего свежих), vacancies (топ-N с score и пояснением), дни, методика.
    """
    prof = _load_profile(profile_id)
    if not prof:
        return {"error": "профиль не найден: " + str(profile_id)}
    days = max(1, min(int(days or 7), 30))
    limit = max(1, min(int(limit or 8), 10))

    con = sqlite3.connect(config.APP_DB, timeout=30)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT v.*, group_concat(s.skill_canon) AS sk
        FROM vacancies v JOIN vacancy_skills s ON s.vacancy_id = v.vacancy_id
        WHERE v.source_layer = 'api_live_2026_10'
          AND v.published_at >= date('now', ?)
        GROUP BY v.vacancy_id""", ("-" + str(days) + " days",)).fetchall()

    scored = []
    for r in rows:
        v = {"vacancy_id": r["vacancy_id"], "name": r["name"], "employer_name": r["employer"],
             "city": r["city"], "schedule": r["schedule"], "experience": r["experience"],
             "salary_from": r["salary_from"], "salary_to": r["salary_to"],
             "salary_currency": r["currency"], "salary_gross": r["gross"],
             "url": r["url"], "published_at": r["published_at"],
             "_skills": set((r["sk"] or "").split(","))}
        score, cov, matched, missing, sf_net, st_net = score_vacancy(v, prof)
        scored.append((score, cov, matched, missing, r))
    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    for score, cov, matched, missing, r in scored[:limit]:
        card = _row_to_card(r, sorted(matched)[:6])
        card["score"] = round(score * 100)
        card["matched_skills"] = sorted(matched)[:6]
        card["missing_skills"] = sorted(missing)[:4]
        out.append(card)
    con.close()
    return {
        "found": len(scored),
        "days": days,
        "vacancies": out,
        "method": f"вакансии за последние {days} дн. (api_live), ранжирование под профиль "
                  f"'{profile_id}': 0.45·покрытие навыков + 0.20·название + 0.20·город/график "
                  f"+ 0.15·зарплата, штраф за опыт; свежих всего {len(scored)}, показано {len(out)}",
    }
