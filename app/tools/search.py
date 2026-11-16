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
EXP_IDS = ("noExperience", "between1And3", "between3And6", "moreThan6")
SCHEDULE_MAP = {
    "удалённо": "remote", "удаленка": "remote", "remote": "remote",
    "полный день": "fullDay", "fullDay": "fullDay",
    "гибрид": "hybrid", "сменный": "shift", "вахта": "flyInFlyOut",
}
# Формат работы в тексте вакансии: в датасете поле schedule пустое (hh перенёс формат
# работы в work_format), поэтому «удалённо/гибрид» ищется и по тексту вакансии
_SCHEDULE_TEXT = {
    "remote": '"удаленн"* OR "удалённ"* OR "удаленк"* OR "удалёнк"* OR remote OR "дистанционн"*',
    "hybrid": '"гибрид"* OR hybrid',
}
_CITY_ALIASES = {"мск": "Москва", "спб": "Санкт-Петербург", "питер": "Санкт-Петербург",
                 "санкт петербург": "Санкт-Петербург", "екб": "Екатеринбург",
                 "ростов": "Ростов-на-Дону", "нск": "Новосибирск", "нижний": "Нижний Новгород"}
_CITY_CACHE = {}


def _fold(s):
    return re.sub(r"\s+", " ", (s or "").strip().casefold().replace("ё", "е"))


def canon_city(con, city):
    """Город как в датасете: «москва», «МОСКВА», «спб» -> «Москва», «Санкт-Петербург».
    Сравнение в SQLite регистрозависимо для кириллицы (NOCASE — только ASCII)."""
    c = (city or "").strip()
    if not c:
        return ""
    import os
    key = (config.APP_DB, os.path.getmtime(config.APP_DB) if os.path.exists(config.APP_DB) else 0)
    if key not in _CITY_CACHE:
        _CITY_CACHE.clear()
        # самое частое написание побеждает («Москва», а не редкое «москва» из архива)
        _CITY_CACHE[key] = {_fold(r[0]): r[0] for r in con.execute(
            "SELECT city FROM vacancies WHERE city != '' GROUP BY city ORDER BY count(*)")}
    m = _CITY_CACHE[key]
    f = _fold(c)
    return m.get(f) or _CITY_ALIASES.get(f) or m.get(_fold(f.replace(" ", "-"))) or c


def norm_experience(value):
    """Опыт в id hh: «1-3 года», «от 3 до 6», «senior», «без опыта» -> between1And3 и т.д.
    Нераспознанное -> '' (фильтр не применяется, а не «0 вакансий»)."""
    t = (value or "").strip()
    if t in EXP_IDS:
        return t
    t = _fold(t)
    if t in EXP_MAP:
        return EXP_MAP[t]
    if re.search(r"нет опыт|без опыт|junior|джун|стаж|intern|начинающ|no ?experience", t):
        return "noExperience"
    if re.search(r"(более|больше|свыше|от)\s*6|6\s*\+|\blead\b|лид|morethan6", t):
        return "moreThan6"
    if re.search(r"3\D{0,10}6|senior|сеньор|between3and6", t):
        return "between3And6"
    if re.search(r"1\D{0,10}3|\bmid|мидл|middle|between1and3", t):
        return "between1And3"
    return ""


def norm_schedule(value):
    t = _fold(value)
    if not t:
        return ""
    if t in SCHEDULE_MAP:
        return SCHEDULE_MAP[t]
    if re.search(r"удал|remote|дистанц|из дома", t):
        return "remote"
    if re.search(r"гибрид|hybrid", t):
        return "hybrid"
    if re.search(r"офис|на месте|onsite|on_site", t):
        return "onSite"
    if re.search(r"полн\w* день|fullday", t):
        return "fullDay"
    return value.strip()
GROSS_K = 0.87  # gross -> net (та же методика, что в match_gap и salary_agg)

# Слова запроса, которые не могут быть названием компании (общие и технические)
_STOPWORDS = {
    "вакансия", "вакансии", "вакансию", "вакансий", "вакансиях", "вакансиям",
    "работа", "работу", "работы", "работе", "работах", "поиск", "ищу", "найти",
    "найди", "найдите", "поищи", "подбор", "подбери", "покажи", "показать",
    "свежие", "свежий", "свежих", "актуальные", "актуальных", "актуальны",
    "новые", "новых", "новый", "новая", "новое", "последние", "последних",
    "сегодня", "сейчас", "вчера", "зарплата", "зарплаты", "зарплату", "зп",
    "опыт", "года", "лет", "дней", "дня", "неделю", "недели", "месяц", "месяца",
    "компания", "компании", "компанию", "работодатель", "работодателя",
    "разработчик", "разработчика", "разработчику", "программист", "программиста",
    "инженер", "инженера", "developer", "стажёр", "стажер", "junior", "middle",
    "senior", "remote", "удалённо", "удаленно", "удалёнки", "стек", "навыки",
    "навыков", "python", "java", "javascript", "typescript", "golang", "linux",
    "docker", "kubernetes", "sql", "mysql", "postgresql", "redis", "kafka",
    "frontend", "backend", "fullstack", "full", "stack", "team", "digital",
    "group", "holding", "company", "tech", "technologies", "solutions", "soft",
}
# Слова, по которым запрос считается просьбой об АКТУАЛЬНЫХ (не архивных) вакансиях
_FRESH_HINTS = ("актуальн", "свеж", "новы", "новый", "новая", "новое", "новых",
                "новым", "новыми", "последн", "сегодня", "вчер", "сейчас",
                "текущ", "действующ")


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


def _fts_quote(text):
    return '"' + (text or "").replace('"', '""') + '"'


def _employer_fts(emp):
    """FTS-условие на колонку employer: фраза целиком ИЛИ все слова запроса.
    Сравнение идёт по токенам FTS (unicode61) — регистр кириллицы не важен."""
    words = re.findall(r"[\w#+.\-]+", (emp or "").lower())
    if not words:
        return ""
    phrase = " ".join(words)
    if len(words) == 1:
        return "{employer}: " + _fts_quote(words[0])
    return "{employer}: (" + _fts_quote(phrase) + " OR (" + \
        " AND ".join(_fts_quote(w) for w in words) + "))"


# Служебные слова запроса: не несут смысла для FTS (иначе «вакансии в Яндексе» требовал
# слова «вакансии» в тексте, а расширение «любое слово» тянуло десятки тысяч строк)
_NOISE = {
    "вакансия", "вакансии", "вакансию", "вакансий", "вакансиях", "вакансиям", "работа",
    "работу", "работы", "поиск", "ищу", "найти", "найди", "найдите", "поищи", "подбор",
    "подбери", "покажи", "показать", "свежие", "свежих", "актуальные", "актуальных",
    "новые", "новых", "последние", "последних", "сегодня", "сейчас", "вчера", "в", "во",
    "на", "для", "с", "со", "по", "от", "до", "из", "и", "или", "мне", "меня", "я", "какие",
    "есть", "компании", "компания", "компанию", "работодатель", "работодателя", "город",
    "городе", "г", "г.",
}
_BIG_CITY_MIN = 100  # город из текста запроса — только заметные (иначе «Анна» и т.п.)
_BIG_CITIES = {}


_RU_ENDING = re.compile(r"(?:ами|ями|ого|его|ому|ему|ыми|ими|ой|ей|ий|ый|ая|яя|ое|ее|ые|ие|"
                        r"ов|ев|ам|ям|ах|ях|ом|ем|а|я|ы|и|у|ю|е|о|ь)$")


def _stem(token):
    """Лёгкий стемминг для FTS (unicode61 без морфологии): «аналитика» -> «аналитик»*,
    чтобы запрос в любом падеже находил названия вакансий."""
    if len(token) >= 5 and re.fullmatch(r"[а-яё\-]+", token):
        s = _RU_ENDING.sub("", token)
        if len(s) >= 4:
            return s
    return token


def _fts_term(token):
    s = _stem(token)
    return _fts_quote(s) + ("*" if s != token else "")


def _skill_token(token):
    """«c#», «c++», «.net»: FTS режет символы (c# -> «c» совпадает с C++/Java/JS) —
    такие токены ищем по каноническому навыку и названию, а не через FTS."""
    if not re.search(r"[#+]|^\.", token):
        return ""
    import os
    import sys
    sys.path.insert(0, os.path.join(config.PROJECT, "tools"))
    import match_gap
    c = match_gap.canon(token)
    return c if c in match_gap.CANON else token


def _stems(word):
    """Падежные формы -> кандидаты основы: «яндексе» -> яндекс, «москве» -> москва."""
    out = [word]
    if re.fullmatch(r"[а-яё\-]{5,}", word):
        out += [word[:-1], word[:-1] + "а", word[:-1] + "ь", word[:-2], word[:-2] + "а"]
    return out


def _city_in_query(con, tokens):
    """(город, слово запроса) — город, названный прямо в тексте запроса."""
    import os
    key = (config.APP_DB, os.path.getmtime(config.APP_DB) if os.path.exists(config.APP_DB) else 0)
    if key not in _BIG_CITIES:
        _BIG_CITIES.clear()
        _BIG_CITIES[key] = {_fold(r[0]): r[0] for r in con.execute(
            "SELECT city FROM vacancies WHERE city != '' GROUP BY city HAVING count(*) >= ? "
            "ORDER BY count(*)", (_BIG_CITY_MIN,))}
    big = _BIG_CITIES[key]
    for t in tokens:
        if len(t) < 3 or not re.search(r"[а-яё]", t):
            continue
        for s in _stems(t):
            f = _fold(s)
            city = big.get(f) or _CITY_ALIASES.get(f)
            if city:
                return city, t
    return "", ""


def _detect_employer(con, tokens):
    """(компания, слово запроса): слово считается работодателем, только если оно чаще
    встречается в поле «работодатель», чем в названиях вакансий («Яндекс», «Сбер», «МТС»),
    а не профессия/город («водитель», «аналитика», «москва» — раньше они превращались
    в фильтр по компании и выдача становилась мусорной). Падежи: «в Яндексе» -> яндекс.
    Технические токены («.net», «c#») пропускаются — FTS токенизирует их иначе."""
    candidates = [t for t in tokens
                  if len(t) >= 3 and t not in _STOPWORDS and t not in _NOISE
                  and "." not in t and "#" not in t and not t.isdigit()][:4]

    def count(col, w):
        return con.execute("SELECT count(*) FROM vacancy_fts WHERE vacancy_fts MATCH ?",
                           ("{" + col + "}: " + _fts_quote(w),)).fetchone()[0]

    for t in sorted(set(candidates), key=len, reverse=True):
        for s in _stems(t):
            if len(s) < (3 if s == t else 5):  # усечённая основа — не короче 5 букв
                continue
            e = count("employer", s)
            if e >= 20 and e >= count("name", s):
                return s, t
    return "", ""


def _looks_fresh(tokens):
    """Запрос просит актуальные (не архивные) вакансии?"""
    return any(t.startswith(_FRESH_HINTS) for t in tokens)


def search_vacancies(query: str, city: str = "", experience: str = "", salary_min: int = 0,
                     schedule: str = "", employer: str = "", actual_only: bool = False,
                     limit: int = 5):
    """Поиск вакансий в датасете hh (585k вакансий, приоритет свежего среза api_live).

    Args:
        query: ключевые слова (например "C# ASP.NET разработчик").
        city: город (например "Ростов-на-Дону"), пусто — любые.
        experience: опыт: "нет опыта" | "от 1 года до 3 лет" | "от 3 до 6 лет" | "более 6 лет".
        salary_min: желаемая зарплата НА РУКИ (рубли) — вакансии с меньшей вилкой отбрасываются.
        schedule: график: "удалённо" | "полный день" | "гибрид".
        employer: компания-работодатель (например "Яндекс") — вакансии других компаний
            не показываются. Если параметр пуст, компания распознаётся из текста запроса.
        actual_only: только актуальные вакансии — архивный слой 2023 года
            (kaggle_raw_2023) исключается. Также включается автоматически, если в
            запросе есть слова «актуальные», «свежие», «новые» и т.п.
        limit: сколько вакансий вернуть (1-10).

    Returns:
        dict: найдено (всего по фильтрам), вакансии (топ-N: id, название, работодатель, город,
        вилка net, опыт, ссылка hh.ru), методика.
    """
    limit = max(1, min(int(limit or 5), 10))
    tokens = re.findall(r"[\w#+.\-]+", (query or "").lower())[:8]
    con = _con_ro()

    filters, params = [], []
    notes = []
    fresh_query = _looks_fresh(tokens)
    if not (city or "").strip():
        q_city, city_word = _city_in_query(con, tokens)
        if q_city:  # «C# разработчик Ростов» -> фильтр по городу, а не слово в FTS
            city = q_city
            tokens = [t for t in tokens if t != city_word]
            notes.append(f"город «{q_city}» взят из запроса")
    emp = (employer or "").strip()
    if not emp:
        emp, emp_word = _detect_employer(con, tokens)
        if emp_word:
            tokens = [t for t in tokens if t != emp_word]
    tokens = [t for t in tokens if t not in _NOISE]
    skill_terms = []  # (каноническое имя, исходный токен) для c#/c++/.net
    for t in list(tokens):
        sk = _skill_token(t)
        if sk:
            skill_terms.append((sk, t))
            tokens.remove(t)
            filters.append("(v.vacancy_id IN (SELECT vacancy_id FROM vacancy_skills "
                           "WHERE skill_canon = ?) OR lower(v.name) LIKE ?)")
            params += [sk, "%" + t + "%"]
    if emp:
        filters.append("v.vacancy_id IN (SELECT vacancy_id FROM vacancy_fts "
                       "WHERE vacancy_fts MATCH ?)")
        params.append(_employer_fts(emp))
    if actual_only or fresh_query:
        actual_only = True
        filters.append("v.source_layer != 'kaggle_raw_2023'")  # архив 2023 не используем
    if city:
        city = canon_city(con, city)
        filters.append("v.city = ?")
        params.append(city)
    if experience:
        exp_id = norm_experience(experience)
        if exp_id:
            filters.append("v.experience = ?")
            params.append(exp_id)
        else:
            notes.append(f"опыт «{experience}» не распознан — фильтр по опыту не применён")
    if schedule:
        sch = norm_schedule(schedule)
        if sch in _SCHEDULE_TEXT:
            filters.append("(v.schedule = ? OR v.vacancy_id IN (SELECT vacancy_id FROM vacancy_fts "
                           "WHERE vacancy_fts MATCH ?))")
            params += [sch, "{name description}: (" + _SCHEDULE_TEXT[sch] + ")"]
            notes.append("формат работы определён по тексту вакансии")
        elif con.execute("SELECT 1 FROM vacancies WHERE schedule = ? LIMIT 1", (sch,)).fetchone():
            filters.append("v.schedule = ?")
            params.append(sch)
        else:
            notes.append(f"график «{schedule}» в датасете не размечен — фильтр не применён")
    if salary_min and int(salary_min) > 0:
        filters.append("max(coalesce(v.salary_rur_from, 0) * (CASE WHEN v.gross = 1 THEN 0.87 ELSE 1 END), "
                       "coalesce(v.salary_rur_to, 0) * (CASE WHEN v.gross = 1 THEN 0.87 ELSE 1 END)) >= ?")
        params.append(int(salary_min))

    def run(match_q, fetch=None):
        """Один проход поиска: сколько всего + окно строк (свежий срез api_live первым).
        FTS-rank не используется: вычисление rank без ограничений по индексу дорого,
        а итоговый порядок задаёт детерминированный rerank ниже."""
        cond = "v.vacancy_id IN (SELECT vacancy_id FROM vacancy_fts WHERE vacancy_fts MATCH ?)"
        p = [match_q] + params
        if filters:
            cond += " AND " + " AND ".join(filters)
        total = con.execute(f"SELECT count(*) FROM vacancies v WHERE {cond}", p).fetchone()[0]
        rows = con.execute(
            f"""SELECT v.* FROM vacancies v WHERE {cond}
                ORDER BY (v.source_layer = 'api_live_2026_10') DESC, v.published_at DESC
                LIMIT ?""", p + [fetch or limit]).fetchall()
        return total, rows

    def rerank(rows):
        """Детерминированный реранжинг: слова в названии и в компании (×3), в навыках (×1),
        затем свежий api-срез и дата. Объяснимо и воспроизводимо."""
        def key(r):
            name = (r["name"] or "").lower()
            employer_low = (r["employer"] or "").lower()
            sk = " ".join(s[0] for s in con.execute(
                "SELECT skill_canon FROM vacancy_skills WHERE vacancy_id=?", (r["vacancy_id"],)))
            words = [_stem(t) for t in tokens] + [t for _, t in skill_terms]

            def hits(text):  # начало слова, а не подстрока: «водител» ≠ «руководитель»
                ws = re.findall(r"[\w#+.]+", text)
                return sum(1 for t in words if any(w.startswith(t) for w in ws))
            score = 3 * hits(name) + 3 * hits(employer_low) + hits(sk)
            return (score, 1 if r["source_layer"] == "api_live_2026_10" else 0, r["published_at"])
        return sorted(rows, key=key, reverse=True)

    if tokens:
        quoted = [_fts_term(t) for t in tokens]
        fetch = max(limit * 8, 40)
        total, rows = run(" AND ".join(quoted), fetch=fetch)
        used = "FTS: все слова запроса"
        if total < limit:  # строгий запрос дал мало — расширяем до «любое слово» по релевантности
            total2, rows2 = run(" OR ".join(quoted), fetch=max(fetch, 60))
            if total2 > total:
                seen = {r["vacancy_id"] for r in rows}
                rows = rows + [r for r in rows2 if r["vacancy_id"] not in seen]
                total, used = total2, "FTS: расширенный поиск (любое слово)"
        rows = rerank(rows)[:limit]
        used += ", реранжинг: слова в названии и компании×3 + навыки×1"
    else:
        # в запросе только компания/город/служебные слова — выдача по фильтрам
        cond = " AND ".join(filters) if filters else "1"
        total = con.execute(f"SELECT count(*) FROM vacancies v WHERE {cond}", params).fetchone()[0]
        rows = con.execute(
            f"""SELECT v.* FROM vacancies v WHERE {cond}
                ORDER BY (v.source_layer = 'api_live_2026_10') DESC, v.published_at DESC
                LIMIT ?""", params + [limit]).fetchall()
        used = "выдача по фильтрам (ключевых слов в запросе нет)"
    out = []
    for r in rows:
        sk = [s[0] for s in con.execute(
            "SELECT skill_canon FROM vacancy_skills WHERE vacancy_id=? LIMIT 6", (r["vacancy_id"],))]
        out.append(_row_to_card(r, sk))
    con.close()
    extra = list(notes)
    if emp:
        extra.append(f"работодатель: «{emp}» (вакансии других компаний не показаны)")
    if actual_only:
        extra.append("архивный слой 2023 года (kaggle_raw_2023) исключён — только актуальные")
    return {
        "found": total,
        "vacancies": out,
        "employer_filter": emp,
        "actual_only": actual_only,
        "method": f"{used}; фильтры: город/опыт/зарплата/график"
                  + ("; " + "; ".join(extra) if extra else "") + "; "
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
