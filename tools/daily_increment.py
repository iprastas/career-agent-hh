# -*- coding: utf-8 -*-
"""
Ежедневный инкремент датасета: сбор -> догрузка описаний -> импорт в SQLite -> сводка в лог.

Запуск (двойной клик по daily_collect.bat или вручную из корня проекта):
  .venv/Scripts/python tools/daily_increment.py              # обычный день
  .venv/Scripts/python tools/daily_increment.py --smoke      # быстрая проверка цепочки
  .venv/Scripts/python tools/daily_increment.py --details 1000

Шаги:
  1. collect_hh.py --period 1   — новые вакансии за сутки (дедуп по vacancy_id, идемпотентно)
  2. collect_hh.py --details N  — догрузка полных описаний (можно прерывать и перезапускать)
  3. build_db.py --incremental  — импорт новых записей в data/app.db
  4. сводка -> data/collect.log (+ флаг, если за сутки 0 новых)

Автозапуск по расписанию НЕ настроен намеренно: нужен включённый компьютер.
Если понадобится: schtasks /create /tn "HH Daily Collect" /tr "C:\\...\\daily_collect.bat"
                  /sc daily /st 09:00
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(TOOLS)
DATA = os.path.join(PROJECT, "data")
LOG = os.path.join(DATA, "collect.log")
DB = os.path.join(DATA, "app.db")
PY = sys.executable  # тот же интерпретатор (venv 3.11), что и запустил скрипт


def log(line):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {line}\n")
    print(line, flush=True)


def run(step, script_args):
    log(f"Шаг: {step} (python tools/{' '.join(script_args)})")
    res = subprocess.run([PY, os.path.join(TOOLS, script_args[0])] + script_args[1:], cwd=PROJECT)
    if res.returncode != 0:
        log(f"ОШИБКА на шаге '{step}' (код {res.returncode}) — прерываем цепочку")
        sys.exit(res.returncode)
    log(f"Шаг '{step}' завершён")


def db_counts():
    if not os.path.exists(DB):
        return 0, 0, 0
    con = sqlite3.connect(DB)
    total = con.execute("SELECT count(*) FROM vacancies").fetchone()[0]
    det = con.execute("SELECT count(*) FROM vacancy_details").fetchone()[0]
    today = time.strftime("%Y-%m-%d")
    new_today = con.execute(
        "SELECT count(*) FROM vacancies WHERE collected_at LIKE ?",
        (today + "%",)).fetchone()[0]
    con.close()
    return total, det, new_today


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="быстрая проверка цепочки (сбор вхолостую)")
    ap.add_argument("--details", type=int, default=300,
                    help="сколько описаний догрузить (0 — пропустить шаг)")
    ap.add_argument("--period", type=int, default=1,
                    help="за сколько последних дней собрать вакансии (1-7)")
    args = ap.parse_args()
    period = max(1, min(int(args.period), 7))

    log(f"=== Ежедневный инкремент: старт (period={period}) ===")
    if args.smoke:
        run("сбор списков (smoke)", ["collect_hh.py", "--smoke", "--period", str(period)])
    else:
        run(f"сбор списков (period={period})", ["collect_hh.py", "--period", str(period)])
    if args.details > 0:
        run(f"догрузка описаний ({args.details})",
            ["collect_hh.py", "--details", str(args.details)])
    run("импорт в SQLite", ["build_db.py", "--incremental"])
    run("семантический индекс (TF-IDF+LSA)", ["build_semantic.py"])

    total, det, new_today = db_counts()
    flag = (" ВНИМАНИЕ: за сегодня 0 новых вакансий — проверьте сбор/сеть!"
            if new_today == 0 else "")
    log(f"Итог: новых за сегодня {new_today} | всего в БД {total} "
        f"| с описаниями {det}.{flag}")
    log("=== Ежедневный инкремент: завершён ===")


if __name__ == "__main__":
    main()
