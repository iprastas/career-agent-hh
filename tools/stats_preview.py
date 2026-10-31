"""
Первичная аналитика по data/clean/vacancies_unified.csv:
- покрытие зарплатами по слоям, медианы вилок (RUR),
- срез по C#/.NET-вакансиям (свежий слой) — релевантно профилю anastasia,
- топ навыков среди C#/.NET-вакансий (из детальных описаний).
"""
import os, json
import pandas as pd

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNI = os.path.join(PROJECT, "data", "clean", "vacancies_unified.csv")

df = pd.read_csv(UNI, low_memory=False)
print("Всего строк:", len(df))
print("\n--- Покрытие зарплатами по слоям ---")
for layer, g in df.groupby("source_layer"):
    sal = g[["salary_rur_from", "salary_rur_to"]].notna().any(axis=1)
    print(f"{layer}: {len(g)} строк, с зарплатой {sal.mean()*100:.0f}%")

print("\n--- Свежий слой (api_live_2026_10): зарплаты RUR ---")
live = df[df["source_layer"] == "api_live_2026_10"]
sal_live = live.dropna(subset=["salary_rur_from", "salary_rur_to"], how="all")
mid = sal_live[["salary_rur_from", "salary_rur_to"]].mean(axis=1)
print("медиана вилки:", int(mid.median()), "₽ | P25:", int(mid.quantile(0.25)), "| P75:", int(mid.quantile(0.75)))

print("\n--- C#/.NET-вакансии (query содержит c# / .net / asp.net) ---")
mask = df["query"].astype(str).str.contains(r"c#|\.net|asp\.net", case=False, regex=True, na=False)
cs = df[mask]
print("всего по слоям:", cs["source_layer"].value_counts().to_dict())
cs_live = cs[(cs["source_layer"] == "api_live_2026_10")]
sal_cs = cs_live.dropna(subset=["salary_rur_from", "salary_rur_to"], how="all")
mid_cs = sal_cs[["salary_rur_from", "salary_rur_to"]].mean(axis=1)
if len(mid_cs):
    print(f"C#/.NET свежие с зарплатой: {len(sal_cs)}/{len(cs_live)} | медиана: {int(mid_cs.median())} ₽ | P25: {int(mid_cs.quantile(0.25))} | P75: {int(mid_cs.quantile(0.75))}")
print("опыт:", cs_live["experience"].value_counts().to_dict())
print("города топ:", cs_live["city"].value_counts().head(5).to_dict())

print("\n--- Топ-20 навыков в C#/.NET-вакансиях (детальные описания) ---")
det_path = os.path.join(PROJECT, "data", "vacancy_details.jsonl")
cs_ids = set(cs_live["vacancy_id"].astype(str))
from collections import Counter
skills = Counter()
n_det = 0
for line in open(det_path, encoding="utf-8"):
    d = json.loads(line)
    if str(d["vacancy_id"]) in cs_ids:
        n_det += 1
        for s in d.get("key_skills") or []:
            skills[s] += 1
print(f"детальных записей по C#/.NET: {n_det}")
for s, c in skills.most_common(20):
    print(f"  {s}: {c}")
