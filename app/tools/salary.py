# -*- coding: utf-8 -*-
"""
Статистика зарплат своим датасетом: salary_stats.

Платный банк зарплат hh недоступен (403 user_auth_expected) — считаем сами по свежему
срезу api_live и честно подписываем методику и размер выборки.
"""
import sqlite3

from .. import config
from .search import EXP_MAP, GROSS_K


def _con_ro():
    con = sqlite3.connect(config.APP_DB, timeout=30)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def _quantiles(vals):
    vals = sorted(vals)
    n = len(vals)
    return (vals[int(0.25 * (n - 1))], vals[int(0.50 * (n - 1))], vals[int(0.75 * (n - 1))])


def salary_stats(skill: str = "", city: str = "", experience: str = "", period_days: int = 0):
    """Статистика зарплат (на руки) по датасету: P25 / медиана / P75.

    Args:
        skill: навык (например "C#", ".NET", "python") — вакансии, где он требуется.
        city: город (например "Ростов-на-Дону"), пусто — вся Россия.
        experience: опыт: "нет опыта" | "от 1 года до 3 лет" | "от 3 до 6 лет" | "более 6 лет".
        period_days: только вакансии за последние N дней (0 — весь срез).

    Returns:
        dict: n (вакансий с зарплатой в выборке), p25/median/p75 (рубли на руки),
        методика и размер выборки. При малой выборке (n < 5) добавляется предупреждение.
    """
    where = ["v.source_layer = 'api_live_2026_10'",
             "(v.salary_rur_from IS NOT NULL OR v.salary_rur_to IS NOT NULL)"]
    params = []
    if city:
        where.append("v.city = ?")
        params.append(city)
    if experience:
        where.append("v.experience = ?")
        params.append(EXP_MAP.get(experience.strip().lower(), experience))
    if period_days and int(period_days) > 0:
        where.append("v.published_at >= date('now', ?)")
        params.append("-" + str(int(period_days)) + " days")
    if skill:
        where.append("v.vacancy_id IN (SELECT vacancy_id FROM vacancy_skills "
                     "WHERE skill_canon = ? COLLATE NOCASE)")
        params.append(skill.strip())
    con = _con_ro()
    rows = con.execute(
        f"SELECT v.salary_rur_from, v.salary_rur_to, v.gross FROM vacancies v "
        f"WHERE {' AND '.join(where)}", params).fetchall()
    con.close()

    vals = []
    for r in rows:
        fr, to = r["salary_rur_from"], r["salary_rur_to"]
        mid = (fr + to) / 2.0 if (fr is not None and to is not None) else (fr if fr is not None else to)
        if mid is None:
            continue
        vals.append(mid * (GROSS_K if r["gross"] else 1.0))
    if not vals:
        return {"n": 0, "note": "в выборке нет вакансий с зарплатой — измените фильтры",
                "filters": {"skill": skill, "city": city, "experience": experience,
                            "period_days": period_days}}
    p25, med, p75 = _quantiles(vals)
    out = {
        "n": len(vals),
        "p25_net": int(p25), "median_net": int(med), "p75_net": int(p75),
        "filters": {"skill": skill, "city": city, "experience": experience,
                    "period_days": period_days},
        "method": "net = вилка × 0.87 при gross; квантили по середине вилки (порядковый метод); "
                  "выборка — вакансии с зарплатой из свежего среза api_live (окт 2026); "
                  f"платный банк hh недоступен (403), статистика считается по датасету, n={len(vals)}",
    }
    if len(vals) < 5:
        out["warning"] = "малая выборка (<5 вакансий) — цифры ориентировочные"
    return out


def salary_trend(skill: str = "", city: str = "", months: int = 24):
    """Динамика зарплат по месяцам публикации вакансий (медиана net + размер выборки).

    Использует весь датасет (в т.ч. исторические вакансии 2023 г.) — видно тренд.

    Args:
        skill: навык (например "C#"), пусто — все вакансии с зарплатой.
        city: город, пусто — вся Россия.
        months: сколько последних месяцев вернуть (6-36).

    Returns:
        dict: trend — [{month: "2023-03", median_net, n}] по возрастанию, методика.
    """
    months = max(6, min(int(months or 24), 36))
    where = ["(v.salary_rur_from IS NOT NULL OR v.salary_rur_to IS NOT NULL)",
             "v.published_at IS NOT NULL AND v.published_at != ''"]
    params = []
    if city:
        where.append("v.city = ?")
        params.append(city)
    if skill:
        where.append("v.vacancy_id IN (SELECT vacancy_id FROM vacancy_skills "
                     "WHERE skill_canon = ? COLLATE NOCASE)")
        params.append(skill.strip())
    con = _con_ro()
    rows = con.execute(
        f"SELECT substr(v.published_at, 1, 7) AS m, v.salary_rur_from, v.salary_rur_to, v.gross "
        f"FROM vacancies v WHERE {' AND '.join(where)}", params).fetchall()
    con.close()

    by_month = {}
    for r in rows:
        fr, to = r["salary_rur_from"], r["salary_rur_to"]
        mid = (fr + to) / 2.0 if (fr is not None and to is not None) else (fr if fr is not None else to)
        if mid is None or not r["m"]:
            continue
        by_month.setdefault(r["m"], []).append(mid * (GROSS_K if r["gross"] else 1.0))

    trend = []
    for m in sorted(by_month)[-months:]:
        vals = sorted(by_month[m])
        if len(vals) < 3:  # слишком мало для медианы — пропускаем месяц
            continue
        trend.append({"month": m, "median_net": int(vals[len(vals) // 2]), "n": len(vals)})
    if not trend:
        return {"trend": [], "note": "нет данных по месяцам — измените фильтры",
                "filters": {"skill": skill, "city": city, "months": months}}
    return {
        "trend": trend,
        "filters": {"skill": skill, "city": city, "months": months},
        "method": "медиана вилки по середине (net = gross × 0.87), по месяцам публикации вакансии; "
                  f"выборка по всему датасету, месяцев в выдаче: {len(trend)}",
    }
