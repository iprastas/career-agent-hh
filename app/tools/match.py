# -*- coding: utf-8 -*-
"""
Матчинг профиля и разрыв в навыках: match_profile, skill_gap.

Скор и карта навыков — те же, что в tools/match_gap.py (единая методика для отчётов
и инструментов агента): скор = 0.45·покрытие + 0.20·название + 0.20·город/график
+ 0.15·зарплата, штраф за опыт. Детерминированно и объяснимо.
"""
import os
import sqlite3
import sys

from google.adk.tools import ToolContext

from .. import config

sys.path.insert(0, os.path.join(config.PROJECT, "tools"))
from match_gap import score_vacancy, load_profiles, STACK, _PATTERNS  # noqa: E402  единая методика


def _con_ro():
    con = sqlite3.connect(config.APP_DB, timeout=30)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def _load_profile(profile_id):
    profs = load_profiles(config.PROFILES_DIR)
    for p in profs:
        if p["id"] == profile_id:
            return p
    return None


def resolve_profile_id(profile_id: str = "", tool_context: ToolContext = None):
    """Чей профиль использовать. Приоритет: явный аргумент > собственный профиль
    пользователя из контекста (data/profiles/u<id>.json) > legacy 'anastasia' только
    для прямых вызовов без контекста (консоль, старые тесты). Изоляция: один
    пользователь никогда не видит резюме другого — своя запись или ошибка."""
    pid = (profile_id or "").strip()
    if pid:
        return pid
    uid = (getattr(tool_context, "user_id", "") or "") if tool_context is not None else ""
    if uid and os.path.exists(os.path.join(config.PROFILES_DIR, uid + ".json")):
        return uid
    if tool_context is None:
        return "anastasia"
    return uid  # у этого пользователя профиля ещё нет -> вызывающий получит ошибку+подсказку


def _candidates(con):
    """Вакансии api-слоя с распознанными навыками -> формат score_vacancy()."""
    rows = con.execute("""
        SELECT v.*, group_concat(s.skill_canon) AS sk
        FROM vacancies v JOIN vacancy_skills s ON s.vacancy_id = v.vacancy_id
        WHERE v.source_layer = 'api_live_2026_10'
        GROUP BY v.vacancy_id""").fetchall()
    out = []
    for r in rows:
        out.append({
            "vacancy_id": r["vacancy_id"], "name": r["name"], "employer_name": r["employer"],
            "city": r["city"], "schedule": r["schedule"], "experience": r["experience"],
            "salary_from": r["salary_from"], "salary_to": r["salary_to"],
            "salary_currency": r["currency"], "salary_gross": r["gross"],
            "url": r["url"], "published_at": r["published_at"],
            "_skills": set((r["sk"] or "").split(",")),
        })
    return out


def match_profile(profile_id: str = "", top_n: int = 5, tool_context: ToolContext = None):
    """Подбор вакансий под профиль соискателя с прозрачным счётом и объяснением.

    Args:
        profile_id: id профиля ("anastasia", "junior"/"mid"/"senior" — примеры);
            пусто — собственный профиль пользователя из контекста (его резюме).
        top_n: сколько вакансий вернуть (1-10).

    Returns:
        dict: топ-N вакансий с score, покрытием навыков, matched/missing навыками и вилкой net.
    """
    pid = resolve_profile_id(profile_id, tool_context)
    prof = _load_profile(pid)
    if not prof:
        return {"error": "профиль не найден: " + str(pid or profile_id),
                "hint": "заполните своё резюме во вкладке «Навыки» — прикрепите файл "
                        "или заполните форму",
                "available": [p["id"] for p in load_profiles(config.PROFILES_DIR)]}
    top_n = max(1, min(int(top_n or 5), 10))
    con = _con_ro()
    vacs = _candidates(con)
    con.close()

    core_stack = {s for s, w in prof["skills"].items() if w == "core" and s in STACK} or set(STACK)
    cand = []
    for v in vacs:
        name_low = (v["name"] or "").lower()
        if (v["_skills"] & core_stack) or any(
                _PATTERNS[s].search(name_low) for s in core_stack if s in _PATTERNS):
            cand.append(v)
    scored = []
    for v in cand:
        score, cov, matched, missing, sf, st = score_vacancy(v, prof)
        scored.append((score, cov, matched, missing, sf, st, v))
    scored.sort(key=lambda x: -x[0])
    top = []
    for score, cov, matched, missing, sf, st, v in scored[:top_n]:
        top.append({
            "vacancy_id": v["vacancy_id"], "name": v["name"], "employer": v["employer_name"],
            "city": v["city"], "score": round(score, 3), "coverage": round(cov, 3),
            "matched_skills": sorted(matched), "missing_skills": sorted(missing),
            "salary_net_from": sf, "salary_net_to": st, "url": v["url"],
        })
    return {
        "profile": pid, "candidates": len(cand), "shown": len(top), "vacancies": top,
        "method": "скор = 0.45·покрытие навыков + 0.20·название + 0.20·город/график + 0.15·зарплата, "
                  "штраф за опыт (3-6 лет ×0.95, 6+ ×0.5); навыки — единая каноническая карта; "
                  f"кандидатов в стеке профиля: {len(cand)} из {len(vacs)} вакансий api-среза",
    }


def skill_gap(profile_id: str = "", top_n: int = 10, tool_context: ToolContext = None):
    """Разрыв в навыках: какие навыки рынка есть в вакансиях стека, но отсутствуют в профиле.

    Args:
        profile_id: id профиля ("anastasia", "junior"/"mid"/"senior" — примеры);
            пусто — собственный профиль пользователя из контекста (его резюме).
        top_n: сколько строк вернуть (1-25).

    Returns:
        dict: skills — навыки рынка БЕЗ покрытия (разрыв) с долей и приоритетом
        (высокий >=20%, средний >=8%, низкий <8% вакансий стека); covered — навыки,
        которые уже есть в резюме (для сверки: добавленный навык виден здесь и
        не считается отсутствующим).
    """
    pid = resolve_profile_id(profile_id, tool_context)
    prof = _load_profile(pid)
    if not prof:
        return {"error": "профиль не найден: " + str(pid or profile_id),
                "hint": "заполните своё резюме во вкладке «Навыки» — прикрепите файл "
                        "или заполните форму",
                "available": [p["id"] for p in load_profiles(config.PROFILES_DIR)]}
    top_n = max(1, min(int(top_n or 10), 25))
    con = _con_ro()
    rows = con.execute("""
        SELECT v.vacancy_id, group_concat(s.skill_canon) AS sk
        FROM vacancies v JOIN vacancy_skills s ON s.vacancy_id = v.vacancy_id
        WHERE v.source_layer = 'api_live_2026_10'
        GROUP BY v.vacancy_id""").fetchall()
    con.close()
    universe = [(r["sk"] or "").split(",") for r in rows]
    universe = [set(x) for x in universe]
    universe = [u for u in universe if u & STACK]
    counts = {}
    for u in universe:
        for s in u:
            counts[s] = counts.get(s, 0) + 1
    prof_all = set(prof["skills"])
    gaps, covered = [], []
    for s, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        share = n / max(1, len(universe))
        in_prof = s in prof_all
        prio = "высокий" if share >= 0.20 else ("средний" if share >= 0.08 else "низкий")
        row = {"skill": s, "in_profile": in_prof, "vacancies": n,
               "share_pct": round(share * 100, 1),
               "priority": "-" if in_prof else prio}
        # разрыв и покрытие — разные списки: добавленный в резюме навык не тонет
        # в обрезке top_n и всегда виден в covered
        (covered if in_prof else gaps).append(row)
    return {
        "profile": pid, "universe": len(universe),
        "skills": gaps[:top_n], "covered": covered[:top_n],
        "method": f"доля = N вакансий стека с навыком / {len(universe)} вакансий стека "
                  "(api-срез, окт 2026); skills — чего не хватает, covered — уже в резюме; "
                  "каноническая карта навыков едина с матчингом",
    }
