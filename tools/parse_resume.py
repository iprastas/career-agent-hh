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
    for c in CITIES:
        if c.lower() in text.lower():
            return c
    return ""


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


def parse_resume(pdf_path, out_name=None):
    text = extract_text(pdf_path)
    skills = detect_skills(text)
    if not skills:
        raise ValueError("не найдено ни одного известного навыка — проверьте PDF")
    slug = (out_name or os.path.splitext(os.path.basename(pdf_path))[0])
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:40] or "profile"
    years = detect_experience_years(text)
    profile = {
        "profile_id": slug,
        "source": os.path.basename(pdf_path),
        "name": detect_title(text) or slug,
        "title": detect_title(text),
        "location": detect_city(text),
        "relocation": False,
        "business_trips": False,
        "experience_years": years,
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
