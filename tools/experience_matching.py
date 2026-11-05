"""Bounded deterministic experience evidence; not an inference of absent abilities.

Only explicit requirements are compared with supplied work history. Shared by
CLI and agent tools. No network, embeddings, arbitrary word-overlap or model calls.
"""
import html
import re
from functools import lru_cache

METHOD = ("база = 0.45·покрытие навыков + 0.20·название + 0.20·город/график + "
          "0.15·зарплата; опыт: подтверждённые требования +0.12 (желательные +0.03), "
          "обязательные неподтверждённые ×(1−0.25·доля); стаж сравнивается с указанными "
          "годами; нет сведений ≠ нет опыта; без требований нет поправки")

# Categories, not individual employers or arbitrary lexical overlap. Employer
# aliases establish industry history only, never a specific technology.
_CATEGORIES = {
    "banking": ("Банковский опыт", r"банков\w*|банке|банках|banking|fintech|финтех\w*", r"т[ -]?банк\w*|тинькофф\w*|tinkoff|t[ -]?bank|сбербанк\w*|альфа[ -]?банк\w*|втб"),
    "machine_learning": ("Машинное обучение / ML", r"\bml\b|machine learning|машинн\w* обучен\w*|обучен\w* модел\w*|нейросет\w*|deep learning", ""),
    "data_engineering": ("Инженерия данных / ETL", r"\betl\b|\belt\b|data engineering|пайплайн\w* данных|хранилищ\w* данных", ""),
    "highload": ("Высоконагруженные системы", r"high[ -]?load|высоконагруж\w*", ""),
    "microservices": ("Микросервисы", r"microservices?|микросервис\w*", ""),
    "management": ("Руководство командой", r"руковод\w* команд\w*|управлен\w* команд\w*|team leadership|team management", ""),
    "testing": ("Автоматизация тестирования", r"автоматизац\w* тестирован\w*|test automation|автотест\w*", ""),
    "cloud": ("Облачная инфраструктура", r"\baws\b|\bazure\b|\bgcp\b|облачн\w* инфраструктур\w*|cloud", ""),
    "ecommerce": ("Электронная коммерция", r"e[ -]?commerce|маркетплейс\w*|электронн\w* коммерц\w*", ""),
    "telecom": ("Телеком", r"телеком\w*|telecom\w*", ""),
    "security": ("Информационная безопасность", r"информационн\w* безопасност\w*|кибербезопасност\w*|cybersecurity", ""),
    "mobile": ("Мобильная разработка", r"мобильн\w* приложен\w*|mobile development|android|\bios\b", ""),
}
_PATTERNS = {k: (label, re.compile(r"(?<!\w)(?:" + req + r")(?!\w)", re.I),
                 re.compile(r"(?<!\w)(?:" + req + ("|" + aliases if aliases else "") + r")(?!\w)", re.I))
             for k, (label, req, aliases) in _CATEGORIES.items()}
_EXPERIENCE = re.compile(r"опыт|работал|работы с|разрабатывал|создавал|внедрял|experience|worked|built|implemented", re.I)
_REQUIRED = re.compile(r"требован|обязател|необходим|нужно|must|required|requirements|ожидаем|от вас|умеете", re.I)
_OPTIONAL = re.compile(r"желател|плюсом|приветству|будет плюс|будет преимуществ|nice.to.have|optional|preferred|необязател|не обязател|не требу", re.I)
_NEGATIVE = re.compile(r"нет\s+(?:\w+\s+){0,2}опыт|без\s+опыт|не\s+(?:имею|имел|работал|занимался|разрабатывал)|no\s+experience|never\s+worked|haven.t\s+worked|не знаком", re.I)


def text_values(value):
    """Flatten history strings/list/dicts, excluding numeric metadata."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in text_values(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in text_values(v)]
    return []


def _clauses(text):
    text = re.sub(r"</?(?:p|li|ul|ol|div|br|h[1-6])\b[^>]*>", "\n", text or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return [re.sub(r"\s+", " ", s).strip() for s in re.split(r"[\n;.!?]+", text) if s.strip()]


@lru_cache(maxsize=4096)
def _requirements(text):
    found = {}
    section = None
    for clause in _clauses(text):
        # Section scope carries across HTML bullets but stops at other sections.
        if re.search(r"условия|предлагаем|о компании|обязанности|responsibilities|we offer|about us", clause, re.I):
            section = None
        if _OPTIONAL.search(clause):
            priority = "optional"
            if len(clause) < 100 and ":" in clause:
                section = priority
        elif _REQUIRED.search(clause):
            priority = "required"
            if ":" in clause:
                section = priority
        else:
            priority = section or "required"
        if not (_EXPERIENCE.search(clause) or _REQUIRED.search(clause) or section):
            continue
        for category, (label, pat, _) in _PATTERNS.items():
            if pat.search(clause):
                item = {"category": category, "requirement": label, "priority": priority,
                        "vacancy_evidence": clause[:500]}
                if category not in found or priority == "required":
                    found[category] = item
    return tuple(found.values())


def experience_evidence(vacancy, profile):
    """JSON-safe quotes for matched/unconfirmed requirements, never 'missing'."""
    text = vacancy.get("description") or ""
    snippets = "\n".join(str(vacancy.get(k) or "") for k in
                         ("requirement", "requirement_snippet", "responsibility_snippet"))
    requirements = _requirements(text + "\n" + snippets)
    history = [c for key in ("summary", "work_experience", "companies", "work_places")
               for value in text_values(profile.get(key)) for c in _clauses(value)]
    matched, unconfirmed = [], []
    for req in requirements:
        item = dict(req)
        pattern = _PATTERNS[item["category"]][2]
        positive = [c for c in history if pattern.search(c) and not _NEGATIVE.search(c)]
        negative = [c for c in history if pattern.search(c) and _NEGATIVE.search(c)]
        # Explicit denial wins over an unqualified mention elsewhere.
        item["status"] = "matched" if positive and not negative else "unconfirmed"
        item["profile_evidence"] = positive[0][:500] if positive and not negative else ""
        item["reason"] = ("Подтверждено текстом опыта" if item["status"] == "matched" else
                          "В профиле явно отрицается этот опыт" if negative else
                          "В предоставленном опыте нет подтверждения; это не означает отсутствия опыта")
        if negative:
            item["profile_evidence"] = negative[0][:500]
        (matched if item["status"] == "matched" else unconfirmed).append(item)
    return {"matched_requirements": matched, "unconfirmed_requirements": unconfirmed,
            "description_available": bool(text.strip()), "experience_years": profile.get("experience_years"),
            "note": "Сравнение явных категорий требований с текстом трудового опыта; не полная семантическая оценка"}


def adjust_score(base_score, vacancy, profile):
    evidence = experience_evidence(vacancy, profile)
    matched, unconfirmed = evidence["matched_requirements"], evidence["unconfirmed_requirements"]
    required = [r for r in matched + unconfirmed if r["priority"] == "required"]
    unknown = [r for r in unconfirmed if r["priority"] == "required"]
    factor = 1 - 0.25 * len(unknown) / max(1, len(required))
    bonus = (0.12 * sum(r["priority"] == "required" for r in matched) / max(1, len(required))
             + (0.03 if any(r["priority"] == "optional" for r in matched) else 0))
    years = profile.get("experience_years")
    minimum = {"between1And3": 1, "between3And6": 3, "moreThan6": 6}.get(vacancy.get("experience"), 0)
    if isinstance(years, (int, float)) and not isinstance(years, bool) and years >= 0 and minimum:
        factor *= min(1.0, max(0.5, years / minimum))
    return max(0.0, min(1.0, base_score * factor + bonus))
