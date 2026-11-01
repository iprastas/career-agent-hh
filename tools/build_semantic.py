# -*- coding: utf-8 -*-
"""
Семантический индекс вакансий: TF-IDF + латентно-семантический анализ (LSA, TruncatedSVD).

Корпус — свежие слои датасета (api_live + kaggle_it, ~26k вакансий): название +
ключевые навыки + требования. Индекс пересобирается ежедневно после импорта
(daily_increment.py), хранится локально:
  data/semantic_model.joblib  — векторизатор TF-IDF + SVD (модель)
  data/semantic_index.npz     — векторы документов (n × k, float32)
  data/semantic_meta.json     — порядок vacancy_id и статистика сборки

Использование:
  .venv\\Scripts\\python tools\\build_semantic.py           # пересобрать индекс
"""
import json
import os
import sqlite3
import sys
import time

import joblib
import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
APP_DB = os.path.join(DATA, "app.db")
MODEL_PATH = os.path.join(DATA, "semantic_model.joblib")
INDEX_PATH = os.path.join(DATA, "semantic_index.npz")
META_PATH = os.path.join(DATA, "semantic_meta.json")

LAYERS = ("api_live_2026_10", "kaggle_it_2026_09")
N_COMPONENTS = 200
MAX_FEATURES = 15000


def load_docs():
    """[(vacancy_id, текст документа)] для свежих слоёв датасета."""
    con = sqlite3.connect(APP_DB, timeout=60)
    con.row_factory = sqlite3.Row
    q = ",".join("?" * len(LAYERS))
    rows = con.execute(f"""
        SELECT v.vacancy_id, v.name, v.requirement_snippet,
               (SELECT group_concat(s.skill_canon, ' ') FROM vacancy_skills s
                WHERE s.vacancy_id = v.vacancy_id) AS skills
        FROM vacancies v
        WHERE v.source_layer IN ({q})
        ORDER BY v.vacancy_id""", LAYERS).fetchall()
    con.close()
    out = []
    for r in rows:
        text = " ".join(filter(None, [r["name"], r["skills"] or "", r["requirement_snippet"] or ""]))
        out.append((str(r["vacancy_id"]), text.lower()))
    return out


def build(verbose=True):
    docs = load_docs()
    if verbose:
        print(f"документов: {len(docs)}")
    ids = [d[0] for d in docs]
    texts = [d[1] for d in docs]

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=MAX_FEATURES,
                                 sublinear_tf=True, min_df=2)
    tfidf = vectorizer.fit_transform(texts)
    if verbose:
        print(f"TF-IDF: {tfidf.shape[0]} x {tfidf.shape[1]}")

    k = min(N_COMPONENTS, tfidf.shape[0] - 1, tfidf.shape[1] - 1)
    svd = TruncatedSVD(n_components=k, random_state=42)
    X = normalize(svd.fit_transform(tfidf).astype(np.float32))
    if verbose:
        print(f"LSA: {X.shape[0]} x {X.shape[1]}, объяснённая дисперсия "
              f"{svd.explained_variance_ratio_.sum():.2%}")

    joblib.dump({"vectorizer": vectorizer, "svd": svd}, MODEL_PATH)
    np.savez_compressed(INDEX_PATH, X=X)
    from scipy import sparse
    sparse.save_npz(os.path.join(DATA, "semantic_tfidf.npz"), tfidf.astype(np.float32))
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump({"ids": ids, "n": len(ids), "k": int(k),
                   "layers": list(LAYERS),
                   "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "explained_variance": round(float(svd.explained_variance_ratio_.sum()), 4)},
                  f, ensure_ascii=False)
    if verbose:
        print("сохранено:", MODEL_PATH, INDEX_PATH, META_PATH, sep="\n  ")
    return {"n": len(ids), "k": int(k)}


if __name__ == "__main__":
    build()
