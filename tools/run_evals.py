# -*- coding: utf-8 -*-
"""
Прогон эталонных вопросов через агента и оценка качества (evals).

Для каждого вопроса из evals/benchmark.json выполняется настоящий ход агента
(LLM + инструменты), затем считаются метрики app/evals.py:
- отказ на off-topic и инъекции;
- использование ожидаемых инструментов;
- анти-галлюцинация (числа ответа только из выдачи инструментов).
Опционально --judge: LLM-as-judge по рубрике (релевантность, точность, краткость, тон).

Отчёт: data/reports/eval_<дата>.md + .json. Требует заполненного .env (модель).
Сессии строятся во временной БД — рабочие данные агента не трогаются.

Использование:
  .venv\\Scripts\\python tools\\run_evals.py            # метрики без судьи
  .venv\\Scripts\\python tools\\run_evals.py --judge    # + LLM-as-judge
"""
import argparse
import asyncio
import json
import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config  # noqa: E402
config.apply_env()  # noqa: E402  ключи провайдера — до litellm/ADK

from app import evals as ev  # noqa: E402
from app.agent import build_agent, APP_NAME  # noqa: E402
from app.event_protocol import stream_turn  # noqa: E402
from app.system_prompt import profile_summary  # noqa: E402

JUDGE_PROMPT = """Ты оцениваешь ответы карьерного агента соискателю. Рубрика (1–5):
5 — точно, по делу, цифры обоснованы, тон человеческий; 3 — в целом верно, но есть
шероховатости; 1 — мимо темы, выдумки или грубые ошибки.
Верни ТОЛЬКО JSON: {"score": <1-5>, "comment": "<одно предложение по-русски>"}"""


async def run_question(runner, q):
    """Один ход агента -> {answer, used_tools, tool_facts}."""
    used, facts, answer = [], [], ""
    s = await runner.session_service.create_session(app_name=runner.app_name, user_id="eval-user")
    async for env in stream_turn(runner, "eval-user", s.id, q["question"]):
        t = env["type"]
        if t == "tool_call":
            used.append(env["name"])
        elif t == "tool_result":
            facts.append(env.get("text") or "")
            if env.get("payload"):
                facts.append(json.dumps(env["payload"], ensure_ascii=False)[:4000])
        elif t == "answer":
            answer = env["text"]
        elif t == "refusal":
            answer = env["text"]
    return {"answer": answer, "used_tools": used, "tool_facts": "\n".join(facts)}


async def judge(model, question, answer):
    import litellm
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "system", "content": JUDGE_PROMPT},
                      {"role": "user", "content": f"Вопрос: {question}\n\nОтвет агента:\n{answer[:3000]}"}],
            temperature=0.0, max_tokens=200, timeout=60)
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
        d = json.loads(raw)
        return {"score": int(d.get("score", 0)), "comment": str(d.get("comment", ""))[:200]}
    except Exception as e:
        return {"score": 0, "comment": "судья недоступен: " + str(e)[:120]}


async def main(use_judge=False, limit=0, only=""):
    from google.adk.runners import Runner
    from google.adk.sessions import DatabaseSessionService

    tmp_db = os.path.join(config.DATA, "evals_sessions.db")
    if os.path.exists(tmp_db):
        os.remove(tmp_db)

    # eval-user должен иметь своё резюме: инструменты профиля работают по user_id
    # (изоляция), а часть эталона спрашивает «под мой профиль». Если резюме нет —
    # берём демо-резюме anastasia как тестовые данные (в рабочие профили не пишем).
    import shutil
    eval_profile = os.path.join(config.PROFILES_DIR, "eval-user.json")
    demo = os.path.join(config.PROFILES_DIR, "anastasia.json")
    if not os.path.exists(eval_profile) and os.path.exists(demo):
        shutil.copy(demo, eval_profile)
    svc = DatabaseSessionService(db_url="sqlite+aiosqlite:///" + tmp_db.replace("\\", "/"))
    runner = Runner(agent=build_agent(), app_name=APP_NAME, session_service=svc)

    benchmark = ev.load_benchmark()
    if only:
        benchmark = [q for q in benchmark if q["id"] == only]
    if limit:
        benchmark = benchmark[:limit]
    prof = profile_summary("anastasia")

    results = []
    for q in benchmark:
        print(f"[{q['id']}] {q['question'][:70]} ...", flush=True)
        r = await run_question(runner, q)
        scored = ev.score_answer(q, r["answer"], r["used_tools"], r["tool_facts"], prof)
        scored["used_tools"] = r["used_tools"]
        scored["answer_head"] = (r["answer"] or "")[:160]
        if use_judge and not q.get("must_refuse"):
            scored["judge"] = await judge(config.model_name(), q["question"], r["answer"])
        results.append(scored)
        print("    ->", "PASS" if scored["pass"] else "FAIL", scored.get("used_tools"), flush=True)

    await svc.db_engine.dispose()
    if os.path.exists(tmp_db):
        os.remove(tmp_db)

    passed = sum(1 for r in results if r["pass"])
    ts = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(os.path.join(config.DATA, "reports"), exist_ok=True)
    json_path = os.path.join(config.DATA, "reports", f"eval_{ts}.json")
    md_path = os.path.join(config.DATA, "reports", f"eval_{ts}.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    lines = [f"# Отчёт evals — {ts}", "",
             f"Вопросов: {len(results)}, пройдено: **{passed}** "
             f"({passed / max(1, len(results)):.0%})", "",
             "| id | вопрос | итог | инструменты | числа | судья |", "|---|---|---|---|---|---|"]
    for r in results:
        judge_col = f"{r['judge']['score']}/5" if r.get("judge") else "—"
        lines.append(f"| {r['id']} | {r['question']} | {'✅' if r['pass'] else '❌'} | "
                     f"{', '.join(r.get('used_tools') or []) or '—'} | "
                     f"{'ok' if r.get('numbers_ok', True) else '⚠ ' + str(r.get('number_violations'))} | "
                     f"{judge_col} |")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nпройдено {passed}/{len(results)} -> {md_path}")
    return passed, len(results)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", action="store_true", help="добавить LLM-as-judge")
    ap.add_argument("--limit", type=int, default=0, help="только первые N вопросов")
    ap.add_argument("--only", default="", help="только вопрос с данным id (напр. q08)")
    args = ap.parse_args()
    asyncio.run(main(use_judge=args.judge, limit=args.limit, only=args.only))
