# -*- coding: utf-8 -*-
"""
Разбор PDF-резюме в профиль соискателя (data/profiles/<name>.json).

Детерминированный и офлайн (конфиденциальные данные никуда не уходят):
pypdf извлекает текст, дальше работают эвристики:
- навыки — общей картой алиасов tools/match_gap.py (та же, что в матчинге и зарплатах);
- город — по списку крупных городов;
- опыт — по датам работы и фразам «опыт … лет»;
- образование/языки — по ключевым словам секций.
Результат — JSON того же формата, что data/profiles/anastasia.json: его можно
править руками или в интерфейсе (вкладка «Навыки» → профиль).

Использование:
  .venv\\Scripts\\python tools\\parse_resume.py                       # PDF из корня проекта
  .venv\\Scripts\\python tools\\parse_resume.py path\\resume.pdf --out ivan
"""
import argparse
import json
import os
import re
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
sys.path.insert(0, os.path.join(PROJECT, "tools"))
from match_gap import CANON, _PATTERNS  # noqa: E402  единая карта навыков

CITIES = ["Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
          "Нижний Новгород", "Красноярск", "Челябинск", "Самара", "Уфа",
          "Ростов-на-Дону", "Краснодар", "Пермь", "Воронеж", "Волгоград",
          "Калининград", "Тюмень", "Иркутск", "Томск", "Омск", "Владивосток",
          "Хабаровск", "Саратов", "Тольятти", "Липецк", "Ижевск", "Тула"]

TITLE_RE = re.compile(r"(?i)(c#|\.net|java|python|php|frontend|backend|fullstack|"
                      r"разработчик|developer|программист|инженер|тестировщик|аналитик)")
EXP_YEARS_RE = re.compile(r"(?i)опыт[^\n]{0,30}?(\d+)\s*(\+)?\s*(год|года|лет)")
DATE_RE = re.compile(r"(20\d{2})\s*[–—\-]\s*(20\d{2}|н\.?\s*вр\.?|настоящее)", re.IGNORECASE)
EDU_RE = re.compile(r"(?i)(бакалавр|магистр|специалист|университет|институт|академия|колледж|юфу|мгу|спбгу)")
LANG_RE = re.compile(r"(?i)(английск\w*|english)")


def extract_text(pdf_path):
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def detect_skills(text):
    """Навыки по общей карте алиасов: [{'name': канон, 'weight': 'core'|'extra'}]."""
    t = text.lower()
    found = []
    for canon in CANON:
        if _PATTERNS[canon].search(t):
            found.append({"name": canon, "source": "резюме (авто-разбор)", "weight": "core"})
    return found


def detect_title(text):
    for line in text.splitlines():
        line = line.strip()
        if 3 < len(line) < 80 and TITLE_RE.search(line):
            return line
    return ""


def detect_city(text):
    """Город проживания: сначала явная строка «Проживает», иначе первый город в тексте.

    Важно: запасной поиск идёт по ПОРЯДКУ ВСТРЕЧИ в тексте, а не по списку CITIES —
    иначе город места работы (например «Уфа») обгоняет город из строки «Проживает».
    """
    residence = re.search(
        r"(?im)^[ \t]*(?:проживает|место[ \t]+проживания|город(?:[ \t]+проживания)?)[ \t]*[:—–-]?[ \t]*"
        r"(?:\r?\n[ \t]*)?([^\r\n]+)", text,
    )
    if residence:
        city = re.split(r"[,;]|\s+(?:гражданство|готов[а]?\b|не готов[а]?\b)",
                        residence.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
        city = re.sub(r"(?i)^г(?:ород)?\.?\s+", "", city).strip().strip(".,;: ")
        if city:
            return next((c for c in CITIES if c.casefold() == city.casefold()), city)
    best = None
    for c in CITIES:
        m = re.search(r"(?<![0-9A-Za-zА-Яа-яЁё-])" + re.escape(c) +
                      r"(?![0-9A-Za-zА-Яа-яЁё])", text, re.IGNORECASE)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), c)
    return best[1] if best else ""


def detect_experience_years(text):
    m = EXP_YEARS_RE.search(text)
    if m:
        return int(m.group(1))
    years = [int(y) for y, y2 in DATE_RE.findall(text) if y.isdigit()]
    years += [int(y) for y, y2 in DATE_RE.findall(text) if y2.isdigit()]
    if years:
        return max(1, max(years) - min(years))
    return 0


def detect_education(text):
    return [ln.strip() for ln in text.splitlines()
            if EDU_RE.search(ln) and 5 < len(ln.strip()) < 120][:4]


def detect_languages(text):
    langs = {"Русский": "родной"}
    if LANG_RE.search(text):
        langs["Английский"] = "указан в резюме"
    return langs


# ---------- опыт работы и компании ----------
MONTH_RE = (r"(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|август\w*|"
            r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)")
EXPERIENCE_START_RE = re.compile(
    r"(?i)^\s*(?:опыт\s+работы|трудовой\s+опыт|work\s+experience)\b")
SECTION_END_RE = re.compile(
    r"(?i)^\s*(?:образован\w*|навыки|ключевые\s+навыки|знание\s+языков|о\s+себе|обо\s+мне|"
    r"гражданство|желаемая\s+должность|дополнительн\w*|сертификат\w*|курсы|награды|"
    r"контакты|рекомендации)\s*$")
WATERMARK_RE = re.compile(r"(?i)^\s*(?:резюме\s+обновлено|страница\s+\d)")
DATE_LINE_RE = re.compile(
    r"(?i)(?:\d{4}\s*[–—-]\s*(?:\d{4}|н\.?\s*вр\.?|настоящее))|(?:" + MONTH_RE + r"\s+\d{4})")
DURATION_LINE_RE = re.compile(
    r"(?i)^\d+\s*(?:\+)?\s*(?:год\w*|лет|месяц\w*)"
    r"(?:\s+\d+\s*(?:\+)?\s*(?:год\w*|лет|месяц\w*))*\s*$")
ORG_RE = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё])(?:ООО|ЗАО|ПАО|ОАО|АО|НПФ|НПП|НИИ|ИП)\s+"
    r"(?:«[^»]{2,60}»|[\"'][^\"']{2,60}[\"']|[А-ЯA-ZЁ][^«»\"'\n\r,]{1,60})")


def detect_work_experience(text):
    """Текст секции «Опыт работы» (без PDF-водяных знаков и соседних секций)."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if EXPERIENCE_START_RE.match(ln.strip())), None)
    if start is None:
        return ""
    out = []
    for ln in lines[start + 1:]:
        s = ln.strip()
        if not s:
            continue
        if SECTION_END_RE.match(s):
            break
        if WATERMARK_RE.match(s):
            continue
        out.append(s)
    return "\n".join(out).strip()


def detect_companies(text):
    """Компании из секции «Опыт работы»: блоки «даты → срок → название компании»
    (формат hh) + орг-формы (ООО/АО/…) в тексте опыта."""
    section = detect_work_experience(text)
    lines = section.splitlines() if section else []
    found, seen = [], set()

    def add(name):
        name = re.sub(r"\s+", " ", name).strip(" .,;:-—–")
        key = name.casefold()
        if not (2 < len(name) < 120) or key in seen or WATERMARK_RE.match(name):
            return
        if any(key in k for k in seen):  # «НПФ ООО» уже есть внутри полного названия
            return
        for k in [k for k in seen if k in key]:  # полное название вытесняет короткое
            seen.discard(k)
            found[:] = [f for f in found if f.casefold() != k]
        seen.add(key)
        found.append(name)

    for i, ln in enumerate(lines):
        s = ln.strip()
        if not (DURATION_LINE_RE.match(s) or DATE_LINE_RE.search(s)):
            continue
        for nxt in lines[i + 1:i + 6]:  # ближайшая строка после блока дат/срока
            t = nxt.strip()
            if (not t or DURATION_LINE_RE.match(t) or DATE_LINE_RE.search(t)
                    or WATERMARK_RE.match(t)):
                continue
            add(t)
            break
    for m in ORG_RE.finditer(section or ""):
        add(m.group(0))
    return found


def detect_work_places(text):
    """Опыт работы по местам: [{'company': ..., 'experience': ...}] — блоки секции
    опыта в формате hh (даты/срок -> компания -> город -> должность -> описание).
    Каждому месту работы — свой текст опыта (без города-строки и водяных знаков)."""
    section = detect_work_experience(text)
    if not section:
        return []
    lines = [ln.strip() for ln in section.splitlines()
             if ln.strip() and not WATERMARK_RE.match(ln.strip())]
    city_set = {c.casefold() for c in CITIES}
    is_head = lambda s: bool(DURATION_LINE_RE.match(s) or DATE_LINE_RE.search(s))  # noqa: E731
    places, i = [], 0
    while i < len(lines):
        if not is_head(lines[i]):
            i += 1
            continue
        while i < len(lines) and is_head(lines[i]):
            i += 1
        if i >= len(lines):
            break
        company, i = lines[i], i + 1
        body = []
        while i < len(lines) and not is_head(lines[i]):
            body.append(lines[i])
            i += 1
        if body and body[0].casefold() in city_set:  # город работы — не часть опыта
            body = body[1:]
        name = re.sub(r"\s+", " ", company).strip(" .,;:-—–")
        if 2 < len(name) < 120:
            places.append({"company": name, "experience": "\n".join(body).strip()})
    return places


def parse_resume(pdf_path, out_name=None):
    text = extract_text(pdf_path)
    skills = detect_skills(text)
    if not skills:
        raise ValueError("не найдено ни одного известного навыка — проверьте PDF")
    slug = (out_name or os.path.splitext(os.path.basename(pdf_path))[0])
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:40] or "profile"
    years = detect_experience_years(text)
    exp_text = detect_work_experience(text)
    profile = {
        "profile_id": slug,
        "source": os.path.basename(pdf_path),
        "name": detect_title(text) or slug,
        "title": detect_title(text),
        "location": detect_city(text),
        "relocation": False,
        "business_trips": False,
        "experience_years": years,
        "summary": exp_text[:2000],
        "work_experience": exp_text[:6000],
        "companies": detect_companies(text)[:20],
        "work_places": detect_work_places(text)[:20],
        "education": detect_education(text),
        "languages": detect_languages(text),
        "skills": skills,
        "preferences": {
            "salary_net": 0,
            "salary_note": "уточните ожидаемую зарплату вручную",
            "employment": ["полная занятость"],
            "work_format": ["на месте работодателя", "удалённо", "гибрид"],
        },
    }
    out_path = os.path.join(DATA, "profiles", slug + ".json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    return out_path, profile


def main():
    ap = argparse.ArgumentParser(description="Разбор PDF-резюме в профиль соискателя")
    ap.add_argument("pdf", nargs="?", help="путь к PDF (по умолчанию — PDF из корня проекта)")
    ap.add_argument("--out", help="имя профиля (slug)")
    args = ap.parse_args()
    pdf = args.pdf
    if not pdf:
        pdfs = [f for f in os.listdir(PROJECT) if f.lower().endswith(".pdf")]
        if not pdfs:
            sys.exit("PDF не найден: передайте путь файлу")
        pdf = os.path.join(PROJECT, pdfs[0])
    path, prof = parse_resume(pdf, args.out)
    print("сохранено:", path)
    print("должность:", prof["title"], "| город:", prof["location"],
          "| опыт:", prof["experience_years"], "лет | навыков:", len(prof["skills"]))


if __name__ == "__main__":
    main()
