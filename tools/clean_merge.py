"""
Чистка и склейка всех слоёв датасета в единый data/clean/vacancies_unified.csv.

Слои:
  1. data/vacancies_live.jsonl       — свежий срез API (окт 2026) [+ data/vacancy_details.jsonl для key_skills]
  2. hh_it_vacancies.csv             — Kaggle IT-вакансии (сен 2026)
  3. Raw_Jobs.csv                    — Kaggle 560k вакансий (март-апрель 2023), chunked

Единая схема: source_layer, vacancy_id, name, employer, city,
salary_from, salary_to, currency, salary_rur_from, salary_rur_to, gross,
experience (canonical id), key_skills ("|"-separated), published_at (ISO), url, query.

Курсы валют — ЦБ РФ на фиксированную дату CB_RATE_DATE (указывается в data card).
"""
import csv, json, os, re, sys, urllib.request, xml.etree.ElementTree as ET
import pandas as pd

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN = os.path.join(PROJECT, "data", "clean")
os.makedirs(CLEAN, exist_ok=True)

CB_RATE_DATE = "28.10.2026"  # дата курсов ЦБ для конвертации валют
OUT = os.path.join(CLEAN, "vacancies_unified.csv")
RATES_NOTE = ""

FIELDS = ["source_layer", "vacancy_id", "name", "employer", "city",
          "salary_from", "salary_to", "currency",
          "salary_rur_from", "salary_rur_to", "gross",
          "experience", "key_skills", "published_at", "url", "query"]


# ---------- курсы ЦБ ----------
def fetch_cb_rates(date_req):
    url = "https://www.cbr.ru/scripts/XML_daily.asp?date_req=" + date_req
    data = None
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read()
    except Exception:
        # фолбэк: системный curl (использует хранилище сертификатов Windows)
        import subprocess
        res = subprocess.run(["curl", "-s", "--fail", url], capture_output=True, timeout=60)
        if res.returncode != 0:
            raise RuntimeError("curl вернул код " + str(res.returncode))
        data = res.stdout
    root = ET.fromstring(data)
    rates = {"RUR": 1.0, "RUB": 1.0}
    for v in root.findall("Valute"):
        code = v.findtext("CharCode")
        nominal = float(v.findtext("Nominal").replace(",", "."))
        value = float(v.findtext("Value").replace(",", "."))
        rates[code] = value / nominal
    if "BYN" in rates:
        rates.setdefault("BYR", rates["BYN"])
    return rates


# ---------- нормализация опыта ----------
EXP_CANON = {
    "нет опыта": "noExperience",
    "от 1 года до 3 лет": "between1And3",
    "от 1 до 3 лет": "between1And3",
    "от 3 до 6 лет": "between3And6",
    "более 6 лет": "moreThan6",
    "noexperience": "noExperience",
    "between1and3": "between1And3",
    "between3and6": "between3And6",
    "morethan6": "moreThan6",
}
UNMAPPED_EXP = {}


def norm_exp(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    key = s.lower()
    if key in EXP_CANON:
        return EXP_CANON[key]
    UNMAPPED_EXP[s] = UNMAPPED_EXP.get(s, 0) + 1
    return s


# ---------- salary из строки Raw_Jobs ----------
RE_RANGE = re.compile(r"^от (\d+) до (\d+) ([A-Z]{3})$")
RE_FROM = re.compile(r"^от (\d+) ([A-Z]{3})$")
RE_TO = re.compile(r"^до (\d+) ([A-Z]{3})$")
SAL_UNMAPPED = []


def parse_salary_str(s):
    """'от 160000 до 160000 RUR' -> (160000, 160000, 'RUR'); 'з/п не указана' -> ('', '', '')"""
    if not isinstance(s, str) or s.strip() in ("", "з/п не указана"):
        return "", "", ""
    s = s.strip()
    for rx, order in ((RE_RANGE, "range"), (RE_FROM, "from"), (RE_TO, "to")):
        m = rx.match(s)
        if m:
            if order == "range":
                return int(m.group(1)), int(m.group(2)), m.group(3)
            if order == "from":
                return int(m.group(1)), "", m.group(2)
            return "", int(m.group(1)), m.group(2)
    SAL_UNMAPPED.append(s)
    return "", "", ""


def conv(amount, cur, rates):
    if amount == "" or amount is None:
        return ""
    try:
        r = rates.get(str(cur).upper())
        return round(float(amount) * r) if r else ""
    except Exception:
        return ""


def main():
    print("Курсы ЦБ на", CB_RATE_DATE, "...", flush=True)
    try:
        rates = fetch_cb_rates(CB_RATE_DATE)
        print("  USD:", rates.get("USD"), "EUR:", rates.get("EUR"), "BYN:", rates.get("BYN"), flush=True)
    except Exception as e:
        print("  ВНИМАНИЕ: курсы ЦБ недоступны (" + str(e)[:80] + ") — salary_rur_* останутся пустыми", flush=True)
        rates = {"RUR": 1.0, "RUB": 1.0}

    out = open(OUT, "w", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(out, fieldnames=FIELDS)
    w.writeheader()
    stats = {}

    seen_ids = set()

    def emit(layer, rows):
        written = 0
        for r in rows:
            vid = str(r.get("vacancy_id"))
            if vid in seen_ids:  # дедупликация между слоями, приоритет — порядок emit
                continue
            seen_ids.add(vid)
            r["source_layer"] = layer
            r["salary_rur_from"] = conv(r["salary_from"], r["currency"], rates)
            r["salary_rur_to"] = conv(r["salary_to"], r["currency"], rates)
            w.writerow(r)
            written += 1
        stats[layer] = stats.get(layer, 0) + written

    # ---------- слой 1: свежий API-срез ----------
    details = {}
    det_path = os.path.join(PROJECT, "data", "vacancy_details.jsonl")
    if os.path.exists(det_path):
        for line in open(det_path, encoding="utf-8"):
            try:
                d = json.loads(line)
                details[d["vacancy_id"]] = d
            except Exception:
                pass
    rows = []
    live_path = os.path.join(PROJECT, "data", "vacancies_live.jsonl")
    seen = set()
    for line in open(live_path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r["vacancy_id"] in seen:
            continue
        seen.add(r["vacancy_id"])
        det = details.get(r["vacancy_id"])
        rows.append({
            "vacancy_id": r["vacancy_id"], "name": r["name"], "employer": r["employer_name"],
            "city": r["city"], "salary_from": r["salary_from"] or "", "salary_to": r["salary_to"] or "",
            "currency": r["salary_currency"] or "", "gross": r["salary_gross"],
            "experience": norm_exp(r["experience"]),
            "key_skills": " | ".join(det["key_skills"]) if det else "",
            "published_at": (r["published_at"] or "")[:19], "url": r["url"], "query": r["query"],
        })
    emit("api_live_2026_10", rows)
    print("Слой api_live:", stats["api_live_2026_10"], "вакансий,", len(details), "с навыками из деталей", flush=True)

    # ---------- слой 2: hh_it_vacancies.csv (Kaggle, сен 2026) ----------
    df = pd.read_csv(os.path.join(PROJECT, "hh_it_vacancies.csv"))
    rows = []
    for r in df.to_dict("records"):
        rows.append({
            "vacancy_id": r.get("vacancy_id"), "name": r.get("name", ""), "employer": r.get("employer_name", ""),
            "city": r.get("city", "") or "", "salary_from": r.get("salary_from") if pd.notna(r.get("salary_from")) else "",
            "salary_to": r.get("salary_to") if pd.notna(r.get("salary_to")) else "",
            "currency": r.get("salary_currency") or "", "gross": r.get("salary_gross"),
            "experience": norm_exp(r.get("experience")),
            "key_skills": "",
            "published_at": str(r.get("published_at") or "")[:19],
            "url": r.get("url", ""), "query": r.get("search_query", ""),
        })
    emit("kaggle_it_2026_09", rows)
    print("Слой kaggle_it:", stats["kaggle_it_2026_09"], flush=True)

    # ---------- слой 3: Raw_Jobs.csv (Kaggle 2023), chunked ----------
    total = 0
    for chunk in pd.read_csv(os.path.join(PROJECT, "Raw_Jobs.csv"), sep=";", chunksize=100000,
                             usecols=["id", "title", "salary", "experience", "key_skills",
                                      "company", "location", "date_of_post"]):
        rows = []
        for r in chunk.to_dict("records"):
            sf, st, cur = parse_salary_str(r.get("salary"))
            loc = r.get("location")
            city = str(loc).split(",")[0].strip() if isinstance(loc, str) else ""
            dt = r.get("date_of_post")
            try:
                iso = pd.to_datetime(dt, format="%d.%m.%Y").strftime("%Y-%m-%d")
            except Exception:
                iso = ""
            ks = r.get("key_skills")
            rows.append({
                "vacancy_id": r.get("id"), "name": r.get("title", ""), "employer": r.get("company", ""),
                "city": city, "salary_from": sf, "salary_to": st, "currency": cur,
                "gross": "", "experience": norm_exp(r.get("experience")),
                "key_skills": str(ks).replace(",", " | ") if isinstance(ks, str) else "",
                "published_at": iso,
                "url": "https://hh.ru/vacancy/" + str(r.get("id")), "query": "",
            })
        emit("kaggle_raw_2023", rows)
        total += len(rows)
        print("  Raw_Jobs:", total, "обработано", flush=True)
    out.close()

    # ---------- самопроверка ----------
    print("\n=== ИТОГ ===")
    print("Слои:", stats, "| всего:", sum(stats.values()))
    print("Нераспознанные значения experience:", UNMAPPED_EXP if UNMAPPED_EXP else "нет")
    print("Нераспознанные salary-строки:", len(SAL_UNMAPPED), SAL_UNMAPPED[:5] if SAL_UNMAPPED else "")

    dfu = pd.read_csv(OUT)
    sal = dfu[["salary_rur_from", "salary_rur_to"]].notna().any(axis=1)
    print("Единый CSV:", len(dfu), "строк | с зарплатой (RUR):", int(sal.sum()), f"({sal.mean()*100:.0f}%)")
    csharp = dfu[dfu["query"].astype(str).str.contains("c#|\.net|asp\.net", case=False, regex=True, na=False)]
    print("Вакансии по запросам C#/.NET в api_live:", len(csharp))
    print("Файл:", OUT)


if __name__ == "__main__":
    main()
