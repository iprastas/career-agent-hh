# -*- coding: utf-8 -*-
"""
Инструменты поиска по датасету: search_vacancies, vacancy_details.

Только чтение data/app.db (PRAGMA query_only), сеть недоступна по построению:
инструменты не импортируют HTTP-клиентов. Выдача ограничена (top-N), каждая
содержит пометку методики и размер выборки.
"""
import re
import sqlite3

from .. import config

# RU-подсказки -> канонические id hh (справочник experience/schedule)
EXP_MAP = {
    "нет опыта": "noExperience", "junior": "noExperience", "стажер": "noExperience", "intern": "noExperience",
    "от 1 года до 3 лет": "between1And3", "от 1 до 3 лет": "between1And3", "1-3": "between1And3",
    "mid": "between1And3", "мидл": "between1And3",
    "от 3 до 6 лет": "between3And6", "3-6": "between3And6", "senior": "between3And6", "сеньор": "between3And6",
    "более 6 лет": "moreThan6", "6+": "moreThan6", "lead": "moreThan6",
}
SCHEDULE_MAP = {
    "удалённо": "remote", "удаленка": "remote", "remote": "remote",
    "полный день": "fullDay", "fullDay": "fullDay",
    "гибрид": "flexible", "сменный": "shift", "вахта": "flyInFlyOut",
}
GROSS_K = 0.87  # gross -> net (та же методика, что в match_gap и salary_agg)


def _con_ro():
    con = sqlite3.connect(config.APP_DB, timeout=30)
    con.execute("PRAGMA query_only=ON")  # инструмент физически не может писать в датасет
    con.row_factory = sqlite3.Row
    return con


def _fts_query(text):
    """Безопасный FTS-запрос: каждое слово в кавычках (спецсимволы FTS экранируются)."""
    tokens = re.findall(r"[\w#+.\-]+", (text or "").lower())
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens[:8])


def _net(v, gross):
    if v is None:
        return None
    return int(v * (GROSS_K if gross else 1.0))


def _row_to_card(r, skills=None):
    return {
        "vacancy_id": r["vacancy_id"],
        "name": r["name"],
        "employer": r["employer"],
        "city": r["city"],
        "experience": r["experience"],
        "salary_net_from": _net(r["salary_rur_from"], r["gross"]),
        "salary_net_to": _net(r["salary_rur_to"], r["gross"]),
        "published_at": r["published_at"],
        "url": r["url"],
        "top_skills": skills or [],
    }


def search_vacancies(query: str, city: str = "", experience: str = "", salary_min: int = 0,
                     schedule: str = "", limit: int = 5):
    """Поиск вакансий в датасете hh (585k вакансий, приоритет свежего среза api_live).

    Args:
        query: ключевые слова (например "C# ASP.NET разработчик").
        city: город (например "Ростов-на-Дону"), пусто — любые.
        experience: опыт: "нет опыта" | "от 1 года до 3 лет" | "от 3 до 6 лет" | "более 6 лет".
        salary_min: желаемая зарплата НА РУКИ (рубли) — вакансии с меньшей вилкой отбрасываются.
        schedule: график: "удалённо" | "полный день" | "гибрид".
        limit: сколько вакансий вернуть (1-10).

    Returns:
        dict: найдено (всего по фильтрам), вакансии (топ-N: id, название, работодатель, город,
        вилка net, опыт, ссылка hh.ru), методика.
    """
    limit = max(1, min(int(limit or 5), 10))
    filters, params = [], []
    if city:
        filters.append("v.city = ?")
        params.append(city)
    if experience:
        filters.append("v.experience = ?")
        params.append(EXP_MAP.get(experience.strip().lower(), experience))
    if schedule:
        filters.append("v.schedule = ?")
        params.append(SCHEDULE_MAP.get(schedule.strip().lower(), schedule))
    if salary_min and int(salary_min) > 0:
        filters.append("max(coalesce(v.salary_rur_from, 0) * (CASE WHEN v.gross = 1 THEN 0.87 ELSE 1 END), "
                       "coalesce(v.salary_rur_to, 0) * (CASE WHEN v.gross = 1 THEN 0.87 ELSE 1 END)) >= ?")
        params.append(int(salary_min))

    tokens = re.findall(r"[\w#+.\-]+", (query or "").lower())[:8]
    con = _con_ro()

    def run(match_q, order="date", fetch=None):
        cond = "v.vacancy_id IN (SELECT vacancy_id FROM vacancy_fts WHERE vacancy_fts MATCH ?)"
        p = [match_q] + params
        if filters:
            cond += " AND " + " AND ".join(filters)
        total = con.execute(f"SELECT count(*) FROM vacancies v WHERE {cond}", p).fetchone()[0]
        order_sql = ("f.rank" if order == "rank"
                     else "(v.source_layer = 'api_live_2026_10') DESC, v.published_at DESC")
        join = ("JOIN vacancy_fts f ON f.vacancy_id = v.vacancy_id" if order == "rank" else "")
        rows = con.execute(
            f"""SELECT v.* FROM vacancies v {join} WHERE {cond}
                ORDER BY {order_sql}
                LIMIT ?""", p + [fetch or limit]).fetchall()
        return total, rows

    def rerank(rows):
        """Детерминированный реранжинг: слова в названии (×3) и в навыках (×1),
        затем свежий api-срез и дата. Объяснимо и воспроизводимо."""
        def key(r):
            name = (r["name"] or "").lower()
            sk = " ".join(s[0] for s in con.execute(
                "SELECT skill_canon FROM vacancy_skills WHERE vacancy_id=?", (r["vacancy_id"],)))
            score = 3 * sum(1 for t in tokens if t in name) + sum(1 for t in tokens if t in sk)
            return (score, 1 if r["source_layer"] == "api_live_2026_10" else 0, r["published_at"])
        return sorted(rows, key=key, reverse=True)

    if tokens:
        quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
        fetch = max(limit * 8, 40)
        total, rows = run(" AND ".join(quoted), fetch=fetch)
        used = "FTS: все слова запроса"
        if total < limit:  # строгий запрос дал мало — расширяем до «любое слово» по релевантности
            total2, rows2 = run(" OR ".join(quoted), order="rank", fetch=fetch)
            if total2 > total:
                seen = {r["vacancy_id"] for r in rows}
                rows = rows + [r for r in rows2 if r["vacancy_id"] not in seen]
                total, used = total2, "FTS: расширенный поиск (любое слово)"
        rows = rerank(rows)[:limit]
        used += ", реранжинг: слова в названии×3 + навыки×1"
    else:
        like = "%" + (query or "") + "%"
        cond = "v.name LIKE ?" + ((" AND " + " AND ".join(filters)) if filters else "")
        p = [like] + params
        total = con.execute(f"SELECT count(*) FROM vacancies v WHERE {cond}", p).fetchone()[0]
        rows = con.execute(
            f"""SELECT v.* FROM vacancies v WHERE {cond}
                ORDER BY (v.source_layer = 'api_live_2026_10') DESC, v.published_at DESC
                LIMIT ?""", p + [limit]).fetchall()
        used = "поиск по названию"
    out = []
    for r in rows:
        sk = [s[0] for s in con.execute(
            "SELECT skill_canon FROM vacancy_skills WHERE vacancy_id=? LIMIT 6", (r["vacancy_id"],))]
        out.append(_row_to_card(r, sk))
    con.close()
    return {
        "found": total,
        "vacancies": out,
        "method": f"{used}; фильтры: город/опыт/зарплата/график; "
                  f"сортировка: свежий срез api_live, затем дата публикации; "
                  f"показано {len(out)} из {total}",
    }


def vacancy_details(vacancy_id: str):
    """Полная карточка вакансии: описание, ключевые навыки, вилка, ссылка.

    Args:
        vacancy_id: идентификатор вакансии (из search_vacancies / match_profile).

    Returns:
        dict: карточка вакансии с обрезанным описанием и списком навыков.
    """
    con = _con_ro()
    r = con.execute("SELECT * FROM vacancies WHERE vacancy_id=?", (str(vacancy_id),)).fetchone()
    if not r:
        con.close()
        return {"error": "вакансия не найдена: " + str(vacancy_id)}
    det = con.execute("SELECT * FROM vacancy_details WHERE vacancy_id=?", (str(vacancy_id),)).fetchone()
    skills = [{"canon": s[0], "raw": s[1]} for s in con.execute(
        "SELECT skill_canon, skill_raw FROM vacancy_skills WHERE vacancy_id=?", (str(vacancy_id),))]
    con.close()
    desc = (det["description"] if det else "") or r["requirement_snippet"] or ""
    card = _row_to_card(r, [s["canon"] for s in skills])
    card.update({
        "description": desc[:1500],
        "requirement": (det["requirement"] if det else r["requirement_snippet"])[:600],
        "responsibility": (det["responsibility"] if det else r["responsibility_snippet"])[:600],
        "key_skills": skills,
        "schedule": r["schedule"],
        "employment": r["employment"],
        "published_at": r["published_at"],
    })
    return card
