# -*- coding: utf-8 -*-
"""
Сборка SQLite-базы агента data/app.db из всех слоёв датасета.

Слои (приоритет дедупликации по vacancy_id — порядок загрузки):
  1. api_live        — data/vacancies_live.jsonl (+ data/vacancy_details.jsonl: описания, key_skills)
  2. kaggle_it_2026  — hh_it_vacancies.csv
  3. kaggle_raw_2023 — Raw_Jobs.csv (chunked)

Таблицы:
  vacancies       — единая схема (salary_rur_* — конвертация курсами ЦБ, gross флаг сохраняется)
  vacancy_details — полные описания (пока только у api-слоя)
  vacancy_skills  — канонические навыки (та же карта алиасов, что в match_gap.py)
  vacancy_fts     — FTS5: name, employer, skills, description
  salary_agg      — агрегаты зарплат (net-медиана/P25/P75 по городу и опыту)
  meta            — дата курсов, дата сборки, счётчики

Режимы:
  py -3.11 tools/build_db.py --full         # полная пересборка с нуля (все слои)
  py -3.11 tools/build_db.py --incremental  # догрузка новых api-записей (ежедневный инкремент)
  py -3.11 tools/build_db.py --stats        # показать счётчики

Самопроверка: на выходе печатает число строк по слоям и сверяет с источниками.
"""
import argparse
import csv
import html
import json
import os
import re
import sqlite3
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)

from match_gap import canon, detect, CANON  # noqa: E402  единая карта навыков
from clean_merge import norm_exp, parse_salary_str, fetch_cb_rates, CB_RATE_DATE  # noqa: E402

PROJECT = os.path.dirname(TOOLS)
DATA = os.path.join(PROJECT, "data")
DB_PATH = os.path.join(DATA, "app.db")
LIVE_JSONL = os.path.join(DATA, "vacancies_live.jsonl")
DETAILS_JSONL = os.path.join(DATA, "vacancy_details.jsonl")
KAGGLE_IT = os.path.join(PROJECT, "hh_it_vacancies.csv")
KAGGLE_RAW = os.path.join(PROJECT, "Raw_Jobs.csv")

TAG_RE = re.compile(r"<[^>]+>")


def clean_html(s):
    return html.unescape(TAG_RE.sub(" ", s or "")).strip()


# ---------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS vacancies (
    vacancy_id      TEXT PRIMARY KEY,
    source_layer    TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    employer        TEXT NOT NULL DEFAULT '',
    city            TEXT NOT NULL DEFAULT '',
    area_id         TEXT NOT NULL DEFAULT '',
    employer_id     TEXT NOT NULL DEFAULT '',
    salary_from     REAL,
    salary_to       REAL,
    currency        TEXT NOT NULL DEFAULT '',
    salary_rur_from REAL,
    salary_rur_to   REAL,
    gross           INTEGER,
    experience      TEXT NOT NULL DEFAULT '',
    employment      TEXT NOT NULL DEFAULT '',
    schedule        TEXT NOT NULL DEFAULT '',
    published_at    TEXT NOT NULL DEFAULT '',
    collected_at    TEXT NOT NULL DEFAULT '',
    url             TEXT NOT NULL DEFAULT '',
    query           TEXT NOT NULL DEFAULT '',
    requirement_snippet      TEXT NOT NULL DEFAULT '',
    responsibility_snippet   TEXT NOT NULL DEFAULT '',
    has_details     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS vacancy_details (
    vacancy_id     TEXT PRIMARY KEY,
    description    TEXT NOT NULL DEFAULT '',
    requirement    TEXT NOT NULL DEFAULT '',
    responsibility TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS vacancy_skills (
    vacancy_id  TEXT NOT NULL,
    skill_canon TEXT NOT NULL,
    skill_raw   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (vacancy_id, skill_canon)
);
CREATE VIRTUAL TABLE IF NOT EXISTS vacancy_fts USING fts5(
    vacancy_id UNINDEXED, name, employer, skills, description
);
CREATE TABLE IF NOT EXISTS salary_agg (
    city        TEXT NOT NULL,
    experience  TEXT NOT NULL,
    n_vacancies INTEGER,
    n_with_salary INTEGER,
    p25_net     REAL,
    median_net  REAL,
    p75_net     REAL,
    PRIMARY KEY (city, experience)
);
CREATE INDEX IF NOT EXISTS idx_vac_layer   ON vacancies(source_layer);
CREATE INDEX IF NOT EXISTS idx_vac_city    ON vacancies(city);
CREATE INDEX IF NOT EXISTS idx_vac_exp     ON vacancies(experience);
CREATE INDEX IF NOT EXISTS idx_vac_salary  ON vacancies(salary_rur_from);
CREATE INDEX IF NOT EXISTS idx_vac_pub     ON vacancies(published_at);
CREATE INDEX IF NOT EXISTS idx_skills_sk   ON vacancy_skills(skill_canon);
"""

INSERT_VAC = """INSERT OR IGNORE INTO vacancies (
    vacancy_id, source_layer, name, employer, city, area_id, employer_id,
    salary_from, salary_to, currency, salary_rur_from, salary_rur_to, gross,
    experience, employment, schedule, published_at, collected_at, url, query,
    requirement_snippet, responsibility_snippet, has_details
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def fnum(v):
    """NaN / '' / None -> None, иначе float."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return None if f != f else f  # NaN
    except (TypeError, ValueError):
        return None


def fstr(v):
    """None/NaN -> '' (NaN в sqlite3 превращается в NULL и молча роняет строку
    при INSERT OR IGNORE из-за NOT NULL колонок)."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:
        return ""
    return v if isinstance(v, str) else str(v)


def fgross(v):
    if v in (1, "1", True, "True", "true"):
        return 1
    if v in (0, "0", False, "False", "false"):
        return 0
    return None


def load_rates(con):
    """Курсы ЦБ: кэш в meta, при отсутствии — загрузка с фиксированной датой CB_RATE_DATE."""
    row = con.execute("SELECT value FROM meta WHERE key='rates_json'").fetchone()
    if row:
        return json.loads(row[0])
    try:
        rates = fetch_cb_rates(CB_RATE_DATE)
    except Exception as e:
        print("  ВНИМАНИЕ: курсы ЦБ недоступны (" + str(e)[:80] + ") — RUR-конвертация = 1:1", flush=True)
        rates = {"RUR": 1.0, "RUB": 1.0}
    con.execute("INSERT OR REPLACE INTO meta VALUES ('rates_json', ?)", (json.dumps(rates),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('rates_date', ?)", (CB_RATE_DATE,))
    con.commit()
    return rates


def conv(amount, cur, rates):
    if amount is None:
        return None
    try:
        r = rates.get(str(cur or "RUR").upper())
        return round(float(amount) * r) if r else None
    except (TypeError, ValueError):
        return None


def skills_for_text(text_low, raw_list):
    """Канонические навыки: та же логика, что match_gap.load_vacancies."""
    out = {}
    for s in raw_list:
        c = canon(s)
        if c in CANON:
            out.setdefault(c, set()).add(s)
    from match_gap import _PATTERNS
    for c, pat in _PATTERNS.items():
        if pat.search(text_low):
            out.setdefault(c, set())
    return out  # canon -> {raw...}


# ---------------------------------------------------------------- импорт слоёв
def import_api(con, rates):
    """Слой 1: vacancies_live.jsonl (полная загрузка, INSERT OR IGNORE — первый слой побеждает)."""
    details = {}
    if os.path.exists(DETAILS_JSONL):
        for line in open(DETAILS_JSONL, encoding="utf-8"):
            try:
                d = json.loads(line)
                details[d["vacancy_id"]] = d
            except Exception:
                pass
    rows, skills_rows = [], []
    seen = set()
    for line in open(LIVE_JSONL, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        vid = str(r["vacancy_id"])
        if vid in seen:
            continue
        seen.add(vid)
        det = details.get(vid)
        sf, st = fnum(r.get("salary_from")), fnum(r.get("salary_to"))
        cur = fstr(r.get("salary_currency"))
        rows.append((
            vid, "api_live_2026_10", fstr(r.get("name")), fstr(r.get("employer_name")),
            fstr(r.get("city")), fstr(r.get("area_id")), fstr(r.get("employer_id")),
            sf, st, cur, conv(sf, cur, rates), conv(st, cur, rates), fgross(r.get("salary_gross")),
            norm_exp(r.get("experience")), fstr(r.get("employment")), fstr(r.get("schedule")),
            fstr(r.get("published_at"))[:19], fstr(r.get("collected_at")), fstr(r.get("url")),
            fstr(r.get("query")), clean_html(r.get("requirement_snippet")),
            clean_html(r.get("responsibility_snippet")), 1 if det else 0,
        ))
        text = " ".join([r.get("name", ""), clean_html(r.get("requirement_snippet")),
                         clean_html(r.get("responsibility_snippet")),
                         (det or {}).get("description") or ""]).lower()
        for c, raws in skills_for_text(text, (det or {}).get("key_skills") or []).items():
            skills_rows.append((vid, c, "|".join(sorted(raws))))
    con.executemany(INSERT_VAC, rows)
    det_rows = [(v, d.get("description", ""), d.get("requirement", ""), d.get("responsibility", ""))
                for v, d in details.items()]
    con.executemany("INSERT OR REPLACE INTO vacancy_details VALUES (?,?,?,?)", det_rows)
    con.executemany("INSERT OR REPLACE INTO vacancy_skills VALUES (?,?,?)", skills_rows)
    con.commit()
    print(f"Слой api_live: {len(rows)} вакансий, {len(det_rows)} с деталями, "
          f"{len(skills_rows)} записей навыков", flush=True)
    return len(rows), len(det_rows)


def import_kaggle_it(con, rates):
    """Слой 2: hh_it_vacancies.csv (Kaggle, сен 2026)."""
    import pandas as pd
    df = pd.read_csv(KAGGLE_IT)
    rows, skills_rows = [], []
    for r in df.to_dict("records"):
        vid = str(r.get("vacancy_id"))
        sf, st = fnum(r.get("salary_from")), fnum(r.get("salary_to"))
        cur = fstr(r.get("salary_currency"))
        rows.append((
            vid, "kaggle_it_2026_09", fstr(r.get("name")), fstr(r.get("employer_name")),
            fstr(r.get("city")), "", "",
            sf, st, cur, conv(sf, cur, rates), conv(st, cur, rates), fgross(r.get("salary_gross")),
            norm_exp(r.get("experience")), "", "",
            fstr(r.get("published_at"))[:19], "", fstr(r.get("url")),
            fstr(r.get("search_query")), "", "", 0,
        ))
    con.executemany(INSERT_VAC, rows)
    con.commit()
    print(f"Слой kaggle_it: {len(rows)} вакансий", flush=True)
    return len(rows)


def import_kaggle_raw(con, rates):
    """Слой 3: Raw_Jobs.csv (Kaggle 2023), chunked."""
    import pandas as pd
    total, sk_total = 0, 0
    for chunk in pd.read_csv(KAGGLE_RAW, sep=";", chunksize=100000,
                             usecols=["id", "title", "salary", "experience", "key_skills",
                                      "company", "location", "date_of_post"]):
        rows, skills_rows = [], []
        for r in chunk.to_dict("records"):
            vid = str(r.get("id"))
            sf, st, cur = parse_salary_str(r.get("salary"))
            sf, st = fnum(sf), fnum(st)
            loc = r.get("location")
            city = str(loc).split(",")[0].strip() if isinstance(loc, str) else ""
            dt = r.get("date_of_post")
            try:
                iso = pd.to_datetime(dt, format="%d.%m.%Y").strftime("%Y-%m-%d")
            except Exception:
                iso = ""
            ks = r.get("key_skills")
            raw_skills = [s.strip() for s in str(ks).split(",")] if isinstance(ks, str) else []
            rows.append((
                vid, "kaggle_raw_2023", fstr(r.get("title")), fstr(r.get("company")),
                city, "", "",
                sf, st, cur, conv(sf, cur, rates), conv(st, cur, rates), None,
                norm_exp(r.get("experience")), "", "", iso, "", "", "", "", "", 0,
            ))
            for s in raw_skills:
                c = canon(s)
                if c in CANON:
                    skills_rows.append((vid, c, s))
        con.executemany(INSERT_VAC, rows)
        con.executemany("INSERT OR REPLACE INTO vacancy_skills VALUES (?,?,?)", skills_rows)
        con.commit()
        total += len(rows)
        sk_total += len(skills_rows)
        print(f"  Raw_Jobs: {total} обработано", flush=True)
    print(f"Слой kaggle_raw: {total} вакансий, {sk_total} записей навыков", flush=True)
    return total


# ---------------------------------------------------------------- FTS и агрегаты
def rebuild_fts(con):
    print("Пересборка FTS...", flush=True)
    con.execute("DELETE FROM vacancy_fts")
    con.execute("""INSERT INTO vacancy_fts (vacancy_id, name, employer, skills, description)
        SELECT v.vacancy_id, v.name, v.employer,
               COALESCE((SELECT group_concat(s.skill_canon, ' ') FROM vacancy_skills s
                         WHERE s.vacancy_id = v.vacancy_id), ''),
               COALESCE(d.description, '') || ' ' || v.requirement_snippet || ' '
                 || ' ' || v.responsibility_snippet
        FROM vacancies v LEFT JOIN vacancy_details d ON d.vacancy_id = v.vacancy_id""")
    con.commit()
    n = con.execute("SELECT count(*) FROM vacancy_fts").fetchone()[0]
    print(f"FTS: {n} строк", flush=True)
    return n


def rebuild_salary_agg(con):
    """Агрегаты: net-пересчёт (gross x0.87), квантили по середине вилки; методика фиксируется в meta."""
    print("Пересборка salary_agg...", flush=True)
    con.execute("DELETE FROM salary_agg")
    con.execute("""INSERT INTO salary_agg
        SELECT city, experience, count(*),
               sum(CASE WHEN salary_rur_from IS NOT NULL OR salary_rur_to IS NOT NULL THEN 1 ELSE 0 END),
               NULL, NULL, NULL
        FROM vacancies GROUP BY city, experience""")
    # квантили — через Python (SQLite без percentile), по net-середине вилки
    rows = con.execute("""
        SELECT city, experience,
               CASE WHEN salary_rur_from IS NOT NULL AND salary_rur_to IS NOT NULL
                    THEN (salary_rur_from + salary_rur_to) / 2.0
                    ELSE COALESCE(salary_rur_from, salary_rur_to) END
                 * CASE WHEN gross = 1 THEN 0.87 ELSE 1.0 END AS mid_net
        FROM vacancies
        WHERE salary_rur_from IS NOT NULL OR salary_rur_to IS NOT NULL""").fetchall()
    buckets = {}
    for city, exp, mid in rows:
        buckets.setdefault((city, exp), []).append(mid)
    upd = []
    for key, vals in buckets.items():
        vals.sort()
        n = len(vals)
        upd.append((vals[int(0.25 * (n - 1))], vals[int(0.50 * (n - 1))],
                    vals[int(0.75 * (n - 1))], key[0], key[1]))
    con.executemany("UPDATE salary_agg SET p25_net=?, median_net=?, p75_net=? "
                    "WHERE city=? AND experience=?", upd)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('salary_method', ?)",
                ("net = RUR x 0.87 если gross; квантили по середине вилки "
                 "(P25/P50/P75, порядковый метод); курсы ЦБ на дату в meta.rates_date",))
    con.commit()
    print(f"salary_agg: {len(upd)} групп (город x опыт)", flush=True)


# ---------------------------------------------------------------- режимы
def cmd_full():
    t0 = time.time()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")  # читатели (инструменты/сервер) не блокируются писателем
    con.executescript(SCHEMA)
    rates = load_rates(con)

    n_api, n_det = import_api(con, rates)
    n_it = import_kaggle_it(con, rates)
    n_raw = import_kaggle_raw(con, rates)
    rebuild_fts(con)
    rebuild_salary_agg(con)

    total = con.execute("SELECT count(*) FROM vacancies").fetchone()[0]
    per_layer = dict(con.execute(
        "SELECT source_layer, count(*) FROM vacancies GROUP BY source_layer").fetchall())
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    con.commit()
    con.close()

    print("\n=== ИТОГ (полная сборка) ===")
    print("Слои:", per_layer, "| всего:", total)
    print(f"Детали: {n_det} | ожидание: api 20002 / kaggle_it 5692 / kaggle_raw 559741")
    ok = per_layer.get("api_live_2026_10") == n_api
    print("Самопроверка:", "OK" if ok else "РАСХОЖДЕНИЕ, проверьте источники!")
    print(f"Время: {time.time() - t0:.0f} с | БД: {DB_PATH} "
          f"({os.path.getsize(DB_PATH) / 1e6:.0f} МБ)")


def cmd_incremental():
    """Ежедневный инкремент: только api-слой из JSONL, FTS обновляется точечно."""
    t0 = time.time()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    rates = load_rates(con)

    have = {r[0] for r in con.execute("SELECT vacancy_id FROM vacancies")}
    have_det = {r[0] for r in con.execute("SELECT vacancy_id FROM vacancy_details")}

    details = {}
    if os.path.exists(DETAILS_JSONL):
        for line in open(DETAILS_JSONL, encoding="utf-8"):
            try:
                d = json.loads(line)
                details[d["vacancy_id"]] = d
            except Exception:
                pass

    new_rows, det_rows, skills_rows, touched = [], [], [], []
    for line in open(LIVE_JSONL, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        vid = str(r["vacancy_id"])
        det = details.get(vid)
        if vid not in have:
            sf, st = fnum(r.get("salary_from")), fnum(r.get("salary_to"))
            cur = fstr(r.get("salary_currency"))
            new_rows.append((
                vid, "api_live_2026_10", fstr(r.get("name")), fstr(r.get("employer_name")),
                fstr(r.get("city")), fstr(r.get("area_id")), fstr(r.get("employer_id")),
                sf, st, cur, conv(sf, cur, rates), conv(st, cur, rates), fgross(r.get("salary_gross")),
                norm_exp(r.get("experience")), fstr(r.get("employment")), fstr(r.get("schedule")),
                fstr(r.get("published_at"))[:19], fstr(r.get("collected_at")), fstr(r.get("url")),
                fstr(r.get("query")), clean_html(r.get("requirement_snippet")),
                clean_html(r.get("responsibility_snippet")), 1 if det else 0,
            ))
            have.add(vid)
            touched.append(vid)
        if det and vid not in have_det:
            det_rows.append((vid, det.get("description", ""), det.get("requirement", ""),
                             det.get("responsibility", "")))
            have_det.add(vid)
            if vid not in touched:
                touched.append(vid)

    con.executemany(INSERT_VAC, new_rows)
    con.executemany("INSERT OR REPLACE INTO vacancy_details VALUES (?,?,?,?)", det_rows)
    for vid in det_rows:
        con.execute("UPDATE vacancies SET has_details=1 WHERE vacancy_id=?", (vid[0],))

    # точечное обновление навыков и FTS по затронутым вакансиям
    for vid in touched:
        row = con.execute("SELECT name, requirement_snippet, responsibility_snippet "
                          "FROM vacancies WHERE vacancy_id=?", (vid,)).fetchone()
        det = details.get(vid) or {}
        text = " ".join([row[0] or "", row[1] or "", row[2] or "",
                         det.get("description") or ""]).lower()
        sk = skills_for_text(text, det.get("key_skills") or [])
        con.execute("DELETE FROM vacancy_skills WHERE vacancy_id=?", (vid,))
        skills_rows += [(vid, c, "|".join(sorted(raws))) for c, raws in sk.items()]
        con.execute("DELETE FROM vacancy_fts WHERE vacancy_id=?", (vid,))
        con.execute("INSERT INTO vacancy_fts VALUES (?,?,?,?,?)", (
            vid, row[0] or "", "", " ".join(sorted(sk)),
            (det.get("description") or "") + " " + (row[1] or "") + " " + (row[2] or "")))
    con.executemany("INSERT OR REPLACE INTO vacancy_skills VALUES (?,?,?)", skills_rows)
    rebuild_salary_agg(con)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    con.commit()

    total = con.execute("SELECT count(*) FROM vacancies").fetchone()[0]
    total_det = con.execute("SELECT count(*) FROM vacancy_details").fetchone()[0]
    print("\n=== ИТОГ (инкремент) ===")
    print(f"Новых вакансий: {len(new_rows)} | новых деталей: {len(det_rows)} | "
          f"всего в БД: {total} (деталей {total_det})")
    print(f"Время: {time.time() - t0:.0f} с")
    con.close()


def cmd_stats():
    con = sqlite3.connect(DB_PATH)
    print("meta:")
    for k, v in con.execute("SELECT key, value FROM meta"):
        print(f"  {k}: {v[:100]}")
    print("Слои:", dict(con.execute(
        "SELECT source_layer, count(*) FROM vacancies GROUP BY source_layer")))
    print("Всего:", con.execute("SELECT count(*) FROM vacancies").fetchone()[0],
          "| деталей:", con.execute("SELECT count(*) FROM vacancy_details").fetchone()[0],
          "| навыков:", con.execute("SELECT count(DISTINCT skill_canon) FROM vacancy_skills").fetchone()[0],
          "| FTS:", con.execute("SELECT count(*) FROM vacancy_fts").fetchone()[0])
    print("С зарплатой (RUR):", con.execute(
        "SELECT count(*) FROM vacancies WHERE salary_rur_from IS NOT NULL "
        "OR salary_rur_to IS NOT NULL").fetchone()[0])
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="полная пересборка с нуля")
    ap.add_argument("--incremental", action="store_true", help="догрузка новых api-записей")
    ap.add_argument("--stats", action="store_true", help="показать счётчики")
    args = ap.parse_args()
    if args.full:
        cmd_full()
    elif args.incremental:
        cmd_incremental()
    elif args.stats:
        cmd_stats()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
