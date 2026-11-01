"""
Модуль агента: матчинг вакансий под профиль + разрыв в навыках (skill gap).
Офлайн-расчёт по data/vacancies_live.jsonl + data/vacancy_details.jsonl.
Выход: data/reports/match_<profile>.csv (топ-50 с объяснением),
       data/reports/skill_gap_<profile>.csv (навыки рынка, которых нет в профиле).

Использование:
  py -3.11 tools/match_gap.py            # все профили
  py -3.11 tools/match_gap.py anastasia  # один профиль
"""
import csv, html, json, os, re, sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
REPORTS = os.path.join(DATA, "reports")

# ---------- канонические навыки и алиасы ----------
ALIASES = {
    "c#": "c#", "c sharp": "c#", ".net": ".net", ".net core": ".net", ".net framework": ".net",
    ".net 8": ".net", ".net 6": ".net", ".net 5": ".net",
    "asp.net": "asp.net", "asp.net core": "asp.net", "asp.net mvc": "asp.net",
    "asp.net web api": "asp.net", "razor pages": "asp.net",
    "entity framework": "entity framework", "ef core": "entity framework",
    "dapper": "dapper", "linq": "linq", "nhibernate": "dapper",
    "wpf": "wpf", "winforms": "winforms", "blazor": "blazor", "maui": "maui",
    "wcf": "wcf", "signalr": "signalr",
    "sql": "sql", "t-sql": "mssql", "ms sql": "mssql", "mssql": "mssql", "sql server": "mssql",
    "postgresql": "postgresql", "postgres": "postgresql", "npgsql": "postgresql",
    "mysql": "mysql", "oracle": "oracle", "mongodb": "mongodb", "redis": "redis",
    "docker": "docker", "docker compose": "docker", "kubernetes": "kubernetes", "k8s": "kubernetes",
    "rabbitmq": "rabbitmq", "kafka": "kafka", "grpc": "grpc",
    "rest": "rest", "rest api": "rest", "soap": "soap", "microservices": "microservices",
    "микросервисы": "microservices", "микросервисная архитектура": "microservices",
    "git": "git", "ci/cd": "ci/cd", "jenkins": "jenkins", "teamcity": "teamcity", "gitlab": "gitlab",
    "unit-тесты": "unit-тесты", "юнит-тесты": "unit-тесты", "xunit": "unit-тесты",
    "nunit": "unit-тесты", "moq": "unit-тесты", "tdd": "tdd",
    "jwt": "jwt", "oauth": "oauth", "saml": "saml",
    "javascript": "javascript", "js": "javascript", "typescript": "typescript",
    "jquery": "jquery", "ajax": "ajax", "html": "html", "css": "css",
    "react": "react", "angular": "angular", "vue": "vue", "node.js": "node.js",
    "python": "python", "java": "java", "c++": "c++", "golang": "golang",
    "linux": "linux", "windows server": "windows server", "nginx": "nginx",
    "elasticsearch": "elasticsearch", "prometheus": "prometheus", "grafana": "grafana",
    "azure": "azure", "aws": "aws", "gcp": "gcp",
    "agile": "agile", "scrum": "scrum", "kanban": "kanban", "jira": "jira",
    "solid": "solid", "oop": "oop", "ооп": "oop", "ооп/солид": "oop",
    "многопоточность": "многопоточность", "async/await": "многопоточность",
    "мвс/mvc": "asp.net", "mvc": "asp.net",
}
CANON = sorted(set(ALIASES.values()))
STACK = {"c#", ".net", "asp.net", "entity framework", "dapper", "linq",
         "wpf", "winforms", "blazor", "wcf", "signalr", "maui"}

# один regex на канонический навык (все алиасы, сначала длинные)
_PATTERNS = {}
for _c in CANON:
    al = sorted([a for a, c in ALIASES.items() if c == _c], key=len, reverse=True)
    _PATTERNS[_c] = re.compile(
        r"(?<![a-zа-я0-9])(?:" + "|".join(re.escape(a) for a in al) + r")(?![a-zа-я0-9])")

TAG_RE = re.compile(r"<[^>]+>")


def canon(name):
    """Строковое имя навыка -> канонический id (или исходная строка)."""
    s = (name or "").strip().lower()
    if not s:
        return ""
    if s in ALIASES:
        return ALIASES[s]
    for a in sorted(ALIASES, key=len, reverse=True):
        if len(a) >= 3 and a in s:
            return ALIASES[a]
    return s


def detect(text_low, skill_list=()):
    """Канонические навыки: из списка key_skills + поиск по тексту."""
    found = set()
    for s in skill_list:
        c = canon(s)
        if c in CANON:
            found.add(c)
    for c, pat in _PATTERNS.items():
        if pat.search(text_low):
            found.add(c)
    return found


# ---------- профили ----------
def load_profiles(pdir=None):
    """Все профили из <pdir>/*.json (по умолчанию data/profiles): одиночные
    (anastasia, u<id> — резюме пользователей) и наборы (synthetic.json — junior/mid/senior)."""
    out = []
    pdir = pdir or os.path.join(DATA, "profiles")
    if not os.path.isdir(pdir):
        return out
    for fname in sorted(os.listdir(pdir)):
        if not fname.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(pdir, fname), encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d.get("profiles"), list):  # synthetic.json — набор профилей
            for p in d["profiles"]:
                skills = {}
                for s in p["skills"]:
                    c = canon(s)
                    if c in CANON:
                        skills[c] = "core"
                out.append({"id": p["profile_id"], "title": p.get("title", ""),
                            "skills": skills, "expected_net": p["preferences"]["salary_net"],
                            "location": "Ростов-на-Дону"})
            continue
        skills = {}
        for s in d.get("skills", []):
            name = s["name"] if isinstance(s, dict) else s
            c = canon(name)
            if c in CANON:
                skills[c] = s.get("weight", "extra") if isinstance(s, dict) else "extra"
        out.append({"id": d.get("profile_id") or fname[:-5], "title": d.get("title", ""),
                    "skills": skills,
                    "expected_net": (d.get("preferences") or {}).get("salary_net", 0),
                    "location": d.get("location", "") or "Ростов-на-Дону"})
    return out


# ---------- вакансии ----------
def load_vacancies():
    details = {}
    p = os.path.join(DATA, "vacancy_details.jsonl")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                d = json.loads(line)
                details[d["vacancy_id"]] = d
            except Exception:
                pass
    rows = []
    for line in open(os.path.join(DATA, "vacancies_live.jsonl"), encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        det = details.get(r["vacancy_id"])
        text = " ".join([
            r.get("name", ""),
            html.unescape(TAG_RE.sub(" ", r.get("requirement_snippet") or "")),
            html.unescape(TAG_RE.sub(" ", r.get("responsibility_snippet") or "")),
            html.unescape(TAG_RE.sub(" ", (det or {}).get("description") or "")),
        ]).lower()
        r["_skills"] = detect(text, (det or {}).get("key_skills") or [])
        rows.append(r)
    return rows


def score_vacancy(v, prof):
    """Прозрачный счёт: покрытие навыков + название + город/формат + зарплата, штраф за опыт."""
    core = {s for s, w in prof["skills"].items() if w == "core"}
    allsk = set(prof["skills"])
    rec = v["_skills"]
    matched = rec & allsk
    missing = rec - allsk
    # делитель >= 2: иначе вакансии с 1-2 распознанными навыками раздувают оценку
    cov = len(matched) / max(2, len(rec))

    name_low = (v.get("name") or "").lower()
    name_bonus = 1.0 if any(p.search(name_low) for p in
                            (_PATTERNS[s] for s in core if s in _PATTERNS)) else 0.0

    schedule = v.get("schedule") or ""
    city = v.get("city") or ""
    if schedule == "remote" or city == prof["location"]:
        city_score = 1.0
    elif city in ("Москва", "Санкт-Петербург"):
        city_score = 0.7
    else:
        city_score = 0.5

    exp = v.get("experience") or ""
    penalty = {"moreThan6": 0.5, "between3And6": 0.95}.get(exp, 1.0)

    sf, st, cur, gross = v.get("salary_from"), v.get("salary_to"), v.get("salary_currency"), v.get("salary_gross")
    if (sf is None and st is None) or cur not in ("RUR", "RUB"):
        sal_score, sf_net, st_net = 0.5, None, None
    else:
        k = 0.87 if gross else 1.0
        sf_net = int(sf * k) if sf is not None else None
        st_net = int(st * k) if st is not None else None
        best = max(x for x in (sf_net, st_net) if x is not None)
        if best >= prof["expected_net"]:
            sal_score = 1.0
        elif best >= prof["expected_net"] * 0.8:
            sal_score = 0.7
        else:
            sal_score = 0.4

    score = (0.45 * cov + 0.20 * name_bonus + 0.20 * city_score + 0.15 * sal_score) * penalty
    return score, cov, matched, missing, sf_net, st_net


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    os.makedirs(REPORTS, exist_ok=True)
    vacs = load_vacancies()
    print(f"Загружено вакансий: {len(vacs)}")

    for prof in load_profiles():
        if only and prof["id"] != only:
            continue
        # кандидаты: только вакансии из стека профиля (C#/.NET/ASP.NET и т.п.),
        # иначе SQL/аналитические вакансии с 1-2 совпадениями вытесняют профильные
        core_stack = {s for s, w in prof["skills"].items() if w == "core" and s in STACK} or set(STACK)
        cand = [v for v in vacs
                if (v["_skills"] & core_stack)
                or any(_PATTERNS[s].search((v.get("name") or "").lower())
                       for s in core_stack if s in _PATTERNS)]
        scored = []
        for v in cand:
            score, cov, matched, missing, sf, st = score_vacancy(v, prof)
            scored.append((score, cov, matched, missing, sf, st, v))
        scored.sort(key=lambda x: -x[0])
        top = scored[:50]

        with open(os.path.join(REPORTS, f"match_{prof['id']}.csv"), "w",
                  encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["vacancy_id", "score", "coverage", "name", "employer", "city",
                        "schedule", "experience", "salary_net_from", "salary_net_to",
                        "matched_skills", "missing_skills", "url"])
            for score, cov, matched, missing, sf, st, v in top:
                w.writerow([v["vacancy_id"], round(score, 3), round(cov, 3), v["name"],
                            v["employer_name"], v["city"], v["schedule"], v["experience"],
                            sf if sf is not None else "", st if st is not None else "",
                            "|".join(sorted(matched)), "|".join(sorted(missing)), v["url"]])

        # --- skill gap по стеку C#/.NET ---
        universe = [v for v in vacs if v["_skills"] & STACK]
        counts = {}
        for v in universe:
            for s in v["_skills"]:
                counts[s] = counts.get(s, 0) + 1
        prof_all = set(prof["skills"])
        gap_rows = []
        for s, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            share = n / max(1, len(universe))
            prio = "высокий" if share >= 0.20 else ("средний" if share >= 0.08 else "низкий")
            gap_rows.append([s, "да" if s in prof_all else "нет", n,
                             round(share * 100, 1), prio if s not in prof_all else "-"])
        with open(os.path.join(REPORTS, f"skill_gap_{prof['id']}.csv"), "w",
                  encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["skill", "in_profile", "vacancies_count", "share_pct", "priority"])
            w.writerows(gap_rows)

        print(f"\n=== Профиль: {prof['id']} ({prof['title']}) ===")
        print(f"Вакансий в стеке C#/.NET: {len(universe)} из {len(vacs)}")
        print("Топ-10 подбора:")
        for score, cov, matched, missing, sf, st, v in top[:10]:
            sal = f"{sf}-{st}" if sf or st else "з/п не указана"
            print(f"  [{score:.2f}] {v['name'][:52]} | {v['employer_name'][:26]} | "
                  f"{v['city']} | {sal} | +{len(matched)} навыков")
        gaps = [r for r in gap_rows if r[1] == "нет"][:15]
        print("Разрыв в навыках (топ-15 по частоте на рынке):")
        for s, _, n, share, prio in gaps:
            print(f"  {s:24s} — {n:5d} вакансий ({share}%), приоритет: {prio}")
        print("CSV:", f"data/reports/match_{prof['id']}.csv",
              f"data/reports/skill_gap_{prof['id']}.csv")


if __name__ == "__main__":
    main()
