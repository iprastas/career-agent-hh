"""
Сборщик вакансий с hh.ru API (только чтение публичных данных).
Этап 1: поиск вакансий -> data/vacancies_live.jsonl + data/vacancies_live.csv
Этап 2 (--details N): обогащение N вакансий полным описанием -> data/vacancy_details.jsonl

Использование:
  py -3.11 tools/collect_hh.py                 # полный сбор списков
  py -3.11 tools/collect_hh.py --smoke         # быстрая проверка: 2 запроса x 1 страница
  py -3.11 tools/collect_hh.py --details 3000  # догрузить описания для 3000 вакансий
"""
import argparse, csv, html, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
RAW = os.path.join(DATA, "raw")

QUERIES = ["c# разработчик", ".net разработчик", "python разработчик", "java разработчик",
           "php разработчик", "javascript разработчик", "react разработчик", "frontend разработчик",
           "backend разработчик", "devops", "data scientist", "инженер данных", "аналитик данных",
           "системный аналитик", "qa инженер", "тестировщик", "android разработчик",
           "ios разработчик", "golang", "c++ разработчик", "1с разработчик", "asp.net"]
AREAS = [1, 2, 113]  # Москва, Санкт-Петербург, вся Россия
EXPERIENCE = ["noExperience", "between1And3", "between3And6", "moreThan6"]
PER_PAGE = 100
MAX_PAGES = 20  # лимит hh: не более 2000 результатов на запрос


def parse_env():
    env = {}
    p = os.path.join(PROJECT, ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env


ENV = parse_env()
TOKEN = ENV["HH_ACCESS_TOKEN"]
UA = ENV.get("HH_USER_AGENT", "HH_agent_nlp/1.0")


def api_get(path, params=None, retries=4):
    url = "https://api.hh.ru/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + TOKEN,
            "HH-User-Agent": UA,
        })
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                wait = 30 * (attempt + 1)
                print(f"  HTTP {e.code}, пауза {wait}с...", flush=True)
                time.sleep(wait)
            else:
                body = e.read().decode(errors="replace")[:200]
                raise RuntimeError(f"HTTP {e.code}: {body}")
        except (TimeoutError, urllib.error.URLError):
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("Исчерпаны повторы: " + url)


def norm_item(it, query):
    sal = it.get("salary") or {}
    return {
        "vacancy_id": it["id"],
        "query": query,
        "name": it.get("name", ""),
        "employer_id": (it.get("employer") or {}).get("id", ""),
        "employer_name": (it.get("employer") or {}).get("name", ""),
        "area_id": (it.get("area") or {}).get("id", ""),
        "city": (it.get("area") or {}).get("name", ""),
        "salary_from": sal.get("from"),
        "salary_to": sal.get("to"),
        "salary_currency": sal.get("currency", ""),
        "salary_gross": sal.get("gross"),
        "experience": (it.get("experience") or {}).get("id", ""),
        "employment": (it.get("employment") or {}).get("id", ""),
        "schedule": (it.get("schedule") or {}).get("id", ""),
        "published_at": it.get("published_at", ""),
        "requirement_snippet": ((it.get("snippet") or {}).get("requirement") or ""),
        "responsibility_snippet": ((it.get("snippet") or {}).get("responsibility") or ""),
        "url": "https://hh.ru/vacancy/" + str(it["id"]),
    }


def load_seen():
    seen = set()
    p = os.path.join(DATA, "vacancies_live.jsonl")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                seen.add(json.loads(line)["vacancy_id"])
            except Exception:
                pass
    return seen


def collect_lists(queries, areas, smoke=False, period=0):
    os.makedirs(RAW, exist_ok=True)
    seen = load_seen()
    print(f"Уже собрано уникальных вакансий: {len(seen)}", flush=True)
    out_path = os.path.join(DATA, "vacancies_live.jsonl")
    new_count = 0
    with open(out_path, "a", encoding="utf-8") as out:
        for q in queries:
            for area in areas:
                # адаптивное разбиение: если результатов > лимита выдачи — дробим по опыту
                probe_params = {"text": q, "area": area, "per_page": 1}
                if period:
                    probe_params["period"] = period
                probe = api_get("vacancies", probe_params)
                found = probe.get("found", 0)
                exp_variants = EXPERIENCE if found > 1900 else [None]
                if smoke:
                    exp_variants = [None]
                print(f"[{q} | area={area}] найдено: {found}", flush=True)
                for exp in exp_variants:
                    pages = 1 if smoke else MAX_PAGES
                    for page in range(pages):
                        params = {"text": q, "area": area, "per_page": PER_PAGE,
                                  "page": page, "clusters": "false"}
                        if period:
                            params["period"] = period
                        if exp:
                            params["experience"] = exp
                        d = api_get("vacancies", params)
                        items = d.get("items", [])
                        for it in items:
                            vid = it["id"]
                            if vid not in seen:
                                seen.add(vid)
                                rec = norm_item(it, q)
                                rec["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                                new_count += 1
                        total_pages = d.get("pages", 1)
                        print(f"  page={page+1}/{total_pages} (exp={exp or '-'}), "
                              f"новых: {new_count}", flush=True)
                        time.sleep(0.7)
                        if smoke or page + 1 >= total_pages:
                            break
    print(f"Готово. Новых вакансий: {new_count}. Всего уникальных: {len(seen)}", flush=True)
    build_csv()


def build_csv():
    """Дедупликация jsonl -> плоский CSV."""
    seen, rows = set(), []
    p = os.path.join(DATA, "vacancies_live.jsonl")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r["vacancy_id"] in seen:
            continue
        seen.add(r["vacancy_id"])
        rows.append(r)
    fields = list(rows[0].keys()) if rows else []
    with open(os.path.join(DATA, "vacancies_live.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"CSV обновлён: {len(rows)} уникальных вакансий -> data/vacancies_live.csv", flush=True)


TAG_RE = re.compile(r"<[^>]+>")


def clean_html(s):
    return html.unescape(TAG_RE.sub(" ", s or "")).strip()


def collect_details(limit, queries=None):
    """Этап 2: полные описания + key_skills для N вакансий (с приоритетом по зарплате).
    queries — если передан список, догружаются только вакансии из этих поисковых запросов
    (например, только C#/.NET: --details 2000 --queries "c# разработчик" ".net разработчик" "asp.net")."""
    rows = [json.loads(l) for l in open(os.path.join(DATA, "vacancies_live.jsonl"), encoding="utf-8")]
    uniq = {}
    for r in rows:
        uniq.setdefault(r["vacancy_id"], r)
    done = set()
    det_path = os.path.join(DATA, "vacancy_details.jsonl")
    if os.path.exists(det_path):
        for line in open(det_path, encoding="utf-8"):
            try:
                done.add(json.loads(line)["vacancy_id"])
            except Exception:
                pass
    todo = [r for r in uniq.values() if r["vacancy_id"] not in done]
    if queries:
        qset = set(queries)
        todo = [r for r in todo if r.get("query") in qset]
    # приоритет: с зарплатой, чередование по запросам
    todo.sort(key=lambda r: (r["salary_from"] is None and r["salary_to"] is None, r["query"]))
    todo = todo[:limit]
    print(f"Детали: уже {len(done)}, к догрузке {len(todo)}", flush=True)
    with open(det_path, "a", encoding="utf-8") as out:
        for i, r in enumerate(todo):
            try:
                d = api_get("vacancies/" + str(r["vacancy_id"]))
            except RuntimeError as e:
                print(f"  пропуск {r['vacancy_id']}: {e}", flush=True)
                continue
            rec = {
                "vacancy_id": r["vacancy_id"],
                "key_skills": [s["name"] for s in d.get("key_skills") or []],
                "description": clean_html(d.get("description")),
                "requirement": clean_html((d.get("snippet") or {}).get("requirement")),
                "responsibility": clean_html((d.get("snippet") or {}).get("responsibility")),
                "employer_name": r["employer_name"],
                "name": r["name"],
                "query": r["query"],
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if (i + 1) % 100 == 0:
                print(f"  детали: {i+1}/{len(todo)}", flush=True)
            time.sleep(0.7)
    print(f"Готово. Детальных записей добавлено: {len(todo)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="быстрая проверка")
    ap.add_argument("--details", type=int, default=0, help="догрузить N описаний")
    ap.add_argument("--queries", nargs="*", default=QUERIES)
    ap.add_argument("--areas", nargs="*", type=int, default=AREAS)
    ap.add_argument("--period", type=int, default=0, help="вакансии за последние N дней (1-30)")
    args = ap.parse_args()
    if args.details:
        collect_details(args.details, queries=args.queries)
    else:
        collect_lists(args.queries, args.areas, smoke=args.smoke, period=args.period)


if __name__ == "__main__":
    main()
