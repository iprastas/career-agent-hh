# -*- coding: utf-8 -*-
"""
Консольный прогон агента (MVP-цикл до UI): тот же конверт событий, что пойдёт в SSE.

Использование:
  python -m app.runner_cli                          # интерактивный диалог (/exit — выход)
  python -m app.runner_cli --once "вопрос"          # один ход (для проверок)
  python -m app.runner_cli --once "вопрос" --user u2  # своя сессия (изоляция по user_id)
"""
import argparse
import asyncio

from .agent import build_agent, APP_NAME
from .event_protocol import stream_turn


async def run(once=None, user_id="console", profile_id="anastasia"):
    from google.adk.runners import InMemoryRunner

    agent = build_agent(profile_id)
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=user_id)
    print(f"[session] user_id={user_id} session_id={session.id}")

    async def one_turn(text):
        async for env in stream_turn(runner, user_id, session.id, text):
            t = env["type"]
            if t == "delta":
                print(env["text"], end="", flush=True)
            elif t == "think":
                print("\n[think] " + env["text"][:200].replace("\n", " "))
            elif t == "status":
                print("\n[status] " + env["text"][:200])
            elif t == "tool_call":
                print(f"\n[tool_call] {env['name']}({env['args']})")
            elif t == "tool_result":
                print(f"[tool_result] {env['name']}: " + env["text"][:180].replace("\n", " "))
            elif t == "refusal":
                print("\n[refusal] " + env["text"])
            elif t == "answer":
                print("\n[answer] " + env["text"])
            elif t == "done":
                print(f"\n[done] tools={env['tools']} elapsed={env['elapsed']}s "
                      f"refused={env.get('refused')}\n")

    if once is not None:
        await one_turn(once)
        return
    print("Карьерный агент. Вопросы: вакансии, навыки, зарплаты, письма, отклики. /exit — выход.")
    while True:
        try:
            text = input("\nвы> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in ("/exit", "/quit", "exit"):
            break
        await one_turn(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", help="один вопрос и выход")
    ap.add_argument("--user", default="console", help="user_id (изоляция сессий)")
    ap.add_argument("--profile", default="anastasia", help="id профиля")
    args = ap.parse_args()
    asyncio.run(run(once=args.once, user_id=args.user, profile_id=args.profile))


if __name__ == "__main__":
    main()
