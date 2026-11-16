"""Bounded deterministic experience evidence; not an inference of absent abilities.

Only explicit requirements are compared with supplied work history. Shared by
CLI and agent tools. No network, embeddings, arbitrary word-overlap or model calls.
"""
import html
import re
from functools import lru_cache

METHOD = ("база = 0.45·покрытие навыков + 0.20·название + 0.20·город/график + "
          "0.15·зарплата; опыт: подтверждённые требования +0.12 (желательные +0.03), "
          "обязательные неподтверждённые ×(1−0.25·доля); стаж резюме сравнивается с "
          "требуемым (поле опыта вакансии и «от N лет» в описании), ×(стаж/требуемый, ≥0.5); "
          "уровень роли: тимлид/руководитель без опыта руководства ×0.55, архитектор ×0.65, "
          "senior/ведущий при стаже <4 лет ×0.8, junior/стажёр при стаже ≥3 лет ×0.85; "
          "другая профессия в названии (контент-менеджер, преподаватель, QA…) ×0.6; "
          "нет сведений ≠ нет опыта")

# Categories, not individual employers or arbitrary lexical overlap. Employer
# aliases establish industry history only, never a specific technology.
_CATEGORIES = {
    "banking": ("Банковский опыт", r"банков\w*|банке|банках|banking|fintech|финтех\w*", r"т[ -]?банк\w*|тинькофф\w*|tinkoff|t[ -]?bank|сбербанк\w*|альфа[ -]?банк\w*|втб"),
    "machine_learning": ("Машинное обучение / ML", r"\bml\b|machine learning|машинн\w* обучен\w*|обучен\w* модел\w*|нейросет\w*|deep learning", ""),
    "data_engineering": ("Инженерия данных / ETL", r"\betl\b|\belt\b|data engineering|пайплайн\w* данных|хранилищ\w* данных", ""),
    "highload": ("Высоконагруженные системы", r"high[ -]?load|высоконагруж\w*", ""),
    "microservices": ("Микросервисы", r"microservices?|микросервис\w*", ""),
    "management": ("Руководство командой", r"руковод\w* (?:команд|групп|отдел|разработк|направлен)\w*|управлен\w* команд\w*|team leadership|team management|"
                   r"тим\s?-?лид\w*|тех\s?-?лид\w*|team\s?-?lead\w*|tech\s?-?lead\w*|teamlead\w*|techlead\w*|в роли (?:руководител|лид)\w*", ""),
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
    history = _history(profile)
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


_HISTORY_KEYS = ("summary", "work_experience", "companies", "work_places", "experience")

# Уровень роли по названию вакансии
_LEAD = re.compile(r"(?<!\w)(?:тим\s?-?лид\w*|тех\s?-?лид\w*|team\s?-?lead\w*|tech\s?-?lead\w*|teamlead\w*|"
                   r"techlead\w*|lead|лид|руководител\w*|head|cto|cio|начальник\w*|директор\w*|"
                   r"engineering manager)(?!\w)", re.I)
_ARCH = re.compile(r"архитектор\w*|architect\w*", re.I)
_SENIOR = re.compile(r"(?<!\w)(?:senior|sr\.?|ведущ\w*|старш\w*|principal|staff|эксперт\w*)(?!\w)", re.I)
_JUNIOR = re.compile(r"(?<!\w)(?:junior|jr\.?|младш\w*|стаж[её]р\w*|intern\w*|trainee|начинающ\w*|без опыта)(?!\w)", re.I)
# Профессия по названию: «C#» в названии контент-менеджера не делает её работой разработчика
_FAMILIES = {
    "dev": r"разработчик\w*|программист\w*|developer|инженер[ -]программист\w*|software engineer|"
           r"back-?end|front-?end|full-?stack|\bdev\b|\.net[ -]инженер|c# инженер",
    "qa": r"тестировщик\w*|\bqa\b|\baqa\b|quality assurance|test engineer|инженер по (?:тестированию|качеству)",
    "analyst": r"аналитик\w*|analyst",
    "teacher": r"преподават\w*|учител\w*|педагог\w*|tutor|репетитор\w*|методист\w*",
    "content": r"контент\w*|content|копирайтер\w*|seo-?специалист\w*",
    "sales": r"продаж\w*|sales|менеджер по (?:работе с клиентами|продажам)|аккаунт",
    "pm": r"project manager|product manager|менеджер проект\w*|менеджер продукт\w*|product owner|владелец продукт\w*",
    "admin": r"системн\w* администратор\w*|администратор\w*|sysadmin|техподдержк\w*|support|поддержк\w*",
    "devops": r"devops|\bsre\b",
    "tech": r"(?<![\w-])техник\b|инженер-техник",
}
_FAMILY_RE = {k: re.compile(v, re.I) for k, v in _FAMILIES.items()}
_YEARS = re.compile(r"(?:от|не менее|минимум|более|больше|свыше|from|at least|minimum|min\.?)\s*"
                    r"(\d{1,2})(?:[.,]5)?\s*(?:-?х|-?ти|\+)?\s*(?:лет|года?|years?|yrs?)"
                    r"|(\d{1,2})\s*\+\s*(?:лет|года?|years?|yrs?)", re.I)
_EXP_WORD = re.compile(r"опыт|experience|стаж", re.I)


def _history(profile):
    return [c for key in _HISTORY_KEYS
            for value in text_values(profile.get(key)) for c in _clauses(value)]


def _families(text):
    return {k for k, rx in _FAMILY_RE.items() if rx.search(text or "")}


def required_years(vacancy):
    """Минимальный стаж: поле опыта hh + «от N лет» в требованиях описания (макс., ≤10)."""
    minimum = {"between1And3": 1, "between3And6": 3, "moreThan6": 6}.get(vacancy.get("experience"), 0)
    text = "\n".join(str(vacancy.get(k) or "") for k in
                     ("description", "requirement", "requirement_snippet"))
    for clause in _clauses(text):
        if not _EXP_WORD.search(clause) or _OPTIONAL.search(clause):
            continue
        for m in _YEARS.finditer(clause):
            n = int(m.group(1) or m.group(2))
            if 0 < n <= 10:
                minimum = max(minimum, n)
    return minimum


def _years(profile):
    y = profile.get("experience_years")
    return y if isinstance(y, (int, float)) and not isinstance(y, bool) and y >= 0 else None


def assess(vacancy, profile):
    """Поправка по опыту: (factor, bonus, notes). notes — человекочитаемые причины,
    их показывает инструмент, чтобы агент объяснял, почему вакансия ниже/выше."""
    notes = []
    evidence = experience_evidence(vacancy, profile)
    matched, unconfirmed = evidence["matched_requirements"], evidence["unconfirmed_requirements"]
    required = [r for r in matched + unconfirmed if r["priority"] == "required"]
    unknown = [r for r in unconfirmed if r["priority"] == "required"]
    factor = 1 - 0.25 * len(unknown) / max(1, len(required))
    bonus = (0.12 * sum(r["priority"] == "required" for r in matched) / max(1, len(required))
             + (0.03 if any(r["priority"] == "optional" for r in matched) else 0))
    for r in matched:
        notes.append("подтверждено опытом: " + r["requirement"])
    for r in unknown:
        notes.append("нет подтверждения в опыте: " + r["requirement"])

    years = _years(profile)
    minimum = required_years(vacancy)
    if years is not None and minimum and years < minimum:
        k = max(0.5, years / minimum)
        factor *= k
        notes.append("требуется опыт от %d лет, в резюме %s (×%.2f)" % (minimum, years, k))

    name = vacancy.get("name") or ""
    title = profile.get("title") or ""
    history = [c for c in _history(profile) if not _NEGATIVE.search(c)]
    own = [title] + history
    if _LEAD.search(name) and not any(_LEAD.search(c) or _PATTERNS["management"][2].search(c)
                                      for c in own):
        factor *= 0.55
        notes.append("роль руководителя/тимлида — в резюме нет опыта руководства (×0.55)")
    elif _ARCH.search(name) and not any(_ARCH.search(c) for c in own) and (years or 0) < 6:
        factor *= 0.65
        notes.append("роль архитектора — в резюме нет такого опыта (×0.65)")
    elif _SENIOR.search(name) and not _SENIOR.search(title) and years is not None and years < 4:
        factor *= 0.8
        notes.append("уровень senior/ведущий при стаже %s лет (×0.8)" % years)
    elif _JUNIOR.search(name) and years is not None and years >= 3:
        factor *= 0.85
        notes.append("позиция junior/стажёр — ниже уровня резюме (×0.85)")

    vac_fam, own_fam = _families(name), _families(title)
    if vac_fam and own_fam and not (vac_fam & own_fam):
        factor *= 0.6
        notes.append("другая профессия в названии вакансии (×0.6)")
    return factor, bonus, notes


def adjust_score(base_score, vacancy, profile):
    factor, bonus, _ = assess(vacancy, profile)
    return max(0.0, min(1.0, base_score * factor + bonus))
