# -*- coding: utf-8 -*-
"""
Guardrails в три слоя (здесь — слои 1 и 3, слой 2 — системный промпт):

1. Детерминированный ПРЕД-фильтр (до модели): формы инъекций -> готовый отказ БЕЗ вызова модели.
3. ПОСТ-фильтр (после модели): утечка системного промпта или заявление о запрещённом
   действии («отправил отклик») -> готовый отказ вместо ответа.

Инъекции отсекаются сопоставлением с образцами, а не интерпретацией: слои 1 и 3
не понимают слова атакующего — они ищут совпадения.
"""
import re

MAX_QUESTION_LEN = 1500  # длиннее — не модельная задача, а мусор/флуд

# Единые формулировки отказа (тесты и UI ссылаются на них)
REFUSAL_TOPIC = ("Я отвечаю только на вопросы по: подбор вакансий, навыки и их разрыв, "
                 "зарплаты, письма работодателям, учёт откликов. "
                 "Сформулируйте запрос по одной из них.")
REFUSAL_INJECTION = ("Запрос отклонён: попытка изменить инструкции агента. "
                     "Я отвечаю только на карьерные вопросы: вакансии, навыки, зарплаты, "
                     "письма, отклики.")
REFUSAL_LEAK = ("Ответ отклонён: попытка раскрыть системные инструкции. "
                + REFUSAL_TOPIC)

# Слои 1: формы инъекций (EN/RU). Порядок не важен.
INJECTION_PATTERNS = [
    r"забудь(\s+все)?(\s+предыдущие)?\s+инструкции",
    r"забыть\s+(все\s+)?инструкции",
    r"игнорируй(\s+все)?(\s+предыдущие)?\s+(инструкции|правила|настройки)",
    r"ignore\s+(all\s+|previous\s+|prior\s+|above\s+)*(instructions?|prompts?|rules)",
    r"forget\s+(all\s+|previous\s+)?(instructions?|prompts?|rules)",
    r"(ты|вы)\s+теперь\s+(другой|системный|system)",
    r"now\s+you\s+are",
    r"act\s+as\s+(a\s+|an\s+|the\s+)?(system|developer|jailbreak|dan\b)",
    r"\bdan\b\s*(mode)?",
    r"do\s+anything\s+now",
    r"jailbreak|джейлбрейк|взлом(ай)?\s+(защиту|правила)",
    r"(выведи|покажи|раскрой|повтори|напечатай)\s+(твой|ваш|свой)?\s*(системный\s+)?(промпт|prompt|инструкции|instructions)",
    r"(print|show|reveal|repeat|output)\s+(your\s+|the\s+)?(system\s+)?(prompt|instructions)",
    r"(какие|что)\s+(у\s+тебя|у\s+вас)?\s*(твои|ваши)?\s*(инструкции|правила|промпт)",
    r"(what|how)\s+(are|is)\s+(your|the)\s+(instructions?|rules?|prompt)",
    r"работай\s+без\s+(правил|ограничений|инструкций)",
    r"без\s+ограничений\s+и\s+правил",
    r"(мой|мне)\s+пароль|password\s+is|api[_\s-]?key\s*[:=]|токен\s+доступа\s*[:=]",
    r"ты\s+больше\s+не\s+(агент|ассистент|связан)",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# Слой 3: заявления о запрещённом действии (отправка куда-либо)
FORBIDDEN_CLAIMS = [
    re.compile(r"я\s+отправил", re.IGNORECASE),
    re.compile(r"отправлен[оы]?\s+(автоматически|за\s+вас|от\s+вашего\s+имени)", re.IGNORECASE),
    re.compile(r"отклик\s+успешно\s+отправлен", re.IGNORECASE),
    re.compile(r"(письмо|сообщение)\s+(успешно\s+)?отправлен[оы]?\s+(работодателю|на\s+почту)", re.IGNORECASE),
    re.compile(r"sent\s+(the\s+)?(letter|application|message)\s+(for\s+you|automatically)", re.IGNORECASE),
]


def pre_filter(text):
    """Слой 1. Возвращает (ok, reason). Отказ происходит БЕЗ вызова модели.

    1a. Регэкспы форм инъекций (слой 1), 1b. семантический классификатор тем
    (слой 1.5, app/topic_gate.py): «просто не по теме» тоже отсекается до модели.
    """
    t = (text or "").strip()
    if not t:
        return False, "empty"
    if len(t) > MAX_QUESTION_LEN:
        return False, "too_long"
    for rx in _INJ:
        if rx.search(t):
            return False, "injection"
    from . import topic_gate
    if topic_gate.is_offtopic(t):
        return False, "offtopic"
    return True, None


def refusal_for(reason):
    if reason == "injection":
        return REFUSAL_INJECTION
    return REFUSAL_TOPIC


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _shingles(s, n=8):
    words = _norm(s).split()
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def post_filter(answer, system_prompt):
    """Слой 3. Утечка промпта / заявление о запрещённом действии -> готовый отказ."""
    a = answer or ""
    # утечка системного промпта: общий фрагмент в 8 слов подряд.
    # Готовые фразы отказа исключены: они входят в промпт, но их воспроизведение — НЕ утечка.
    if system_prompt:
        prompt_sh = (_shingles(system_prompt) - _shingles(REFUSAL_TOPIC)
                     - _shingles(REFUSAL_INJECTION) - _shingles(REFUSAL_LEAK))
        if _shingles(a) & prompt_sh:
            return REFUSAL_LEAK
    for rx in FORBIDDEN_CLAIMS:
        if rx.search(a):
            return ("Ответ отклонён: агент не отправляет отклики и письма. "
                    "Текст можно только сохранить локально и скопировать. " + REFUSAL_TOPIC)
    return a
