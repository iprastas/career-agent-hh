# -*- coding: utf-8 -*-
"""
Семантический поиск по датасету: semantic_search, similar_vacancies.

Технология: TF-IDF + LSA (TruncatedSVD) — «латентный смысл», а не только совпадение
слов (см. tools/build_semantic.py). Индекс — только чтение, сеть недоступна.
Инструменты честно сообщают, если индекс не собран, и подписывают методику.
"""
import json
import os
import sqlite3
import threading

import numpy as np

from .. import config
from .search import _row_to_card

_LOCK = threading.Lock()
_CACHE = {}  # {"X", "ids", "vectorizer", "svd", "meta"}


def _paths():
    return (os.path.join(config.DATA, "semantic_model.joblib"),
            os.path.join(config.DATA, "semantic_index.npz"),
            os.path.join(config.DATA, "semantic_meta.json"))


def _index():
    """Ленивая загрузка индекса (кэш в памяти процесса). None — индекс не собран."""
    with _LOCK:
        if _CACHE:
            return _CACHE
        model_p, index_p, meta_p = _paths()
        if not (os.path.exists(model_p) and os.path.exists(index_p) and os.path.exists(meta_p)):
            return None
        import joblib
        from scipy import sparse
        model = joblib.load(model_p)
        X = np.load(index_p)["X"]
        meta = json.load(open(meta_p, encoding="utf-8"))
        tfidf_p = os.path.join(config.DATA, "semantic_tfidf.npz")
        T = sparse.load_npz(tfidf_p) if os.path.exists(tfidf_p) else None
        _CACHE.update({"X": X, "T": T, "ids": meta["ids"], "meta": meta,
                       "vectorizer": model["vectorizer"], "svd": model["svd"]})
        return _CACHE


def _sims(idx, text):
    """Гибридная близость к запросу: 0.5·LSA (смысл) + 0.5·TF-IDF (лексика)."""
    from sklearn.preprocessing import normalize
    t = (text or "").lower()
    q_tfidf = idx["vectorizer"].transform([t])
    q_lsa = normalize(idx["svd"].transform(q_tfidf).astype(np.float32))
    sims_lsa = (idx["X"] @ q_lsa[0]).ravel()
    if idx["T"] is not None:
        qn = normalize(q_tfidf)
        sims_lex = (idx["T"] @ qn.T).toarray().ravel()
        return 0.5 * sims_lsa + 0.5 * sims_lex
    return sims_lsa


def _cards(vacancy_ids, with_skills=True):
    con = sqlite3.connect(config.APP_DB, timeout=30)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    out = []
    for vid in vacancy_ids:
        r = con.execute("SELECT * FROM vacancies WHERE vacancy_id=?", (vid,)).fetchone()
        if not r:
            continue
        sk = []
        if with_skills:
            sk = [s[0] for s in con.execute(
                "SELECT skill_canon FROM vacancy_skills WHERE vacancy_id=? LIMIT 6", (vid,))]
        out.append(_row_to_card(r, sk))
    con.close()
    return out


def _filter_city(candidates, idx, city):
    if not city:
        return candidates
    con = sqlite3.connect(config.APP_DB, timeout=30)
    con.execute("PRAGMA query_only=ON")
    keep = []
    for score, vid in candidates:
        row = con.execute("SELECT city FROM vacancies WHERE vacancy_id=?", (vid,)).fetchone()
        if row and (row[0] or "").lower() == city.strip().lower():
            keep.append((score, vid))
    con.close()
    return keep


def semantic_search(query: str, city: str = "", limit: int = 5):
    """Семантический поиск вакансий по смыслу запроса (TF-IDF + LSA, ~26k свежих вакансий).

    Понимает перефразировки: «аналогичное моему стеку» найдёт релевантное, даже если
    слов запроса нет в названии. Для точных фильтров используй search_vacancies.

    Args:
        query: запрос по-русски или по-английски, например "бэкенд на дотнете с базами данных".
        city: город (например "Ростов-на-Дону"), пусто — любые.
        limit: сколько вакансий вернуть (1-10).

    Returns:
        dict: вакансии (топ-N по косинусной близости к запросу), score (0..1), методика.
    """
    idx = _index()
    if idx is None:
        return {"error": "семантический индекс не собран — запустите tools/build_semantic.py",
                "vacancies": []}
    limit = max(1, min(int(limit or 5), 10))
    sims = _sims(idx, query)
    order = np.argsort(-sims)[:200]
    cand = [(float(sims[i]), idx["ids"][i]) for i in order if sims[i] > 0]
    cand = _filter_city(cand, idx, city)[:limit]
    return {
        "found": len(cand),
        "vacancies": _cards([v for _, v in cand]),
        "scores": [round(s, 3) for s, _ in cand],
        "method": f"TF-IDF + LSA (TruncatedSVD, k={idx['meta']['k']}), косинусная близость; "
                  f"корпус: {idx['meta']['n']} свежих вакансий (api_live + kaggle_it); "
                  f"показано {len(cand)}",
    }


def similar_vacancies(vacancy_id: str, limit: int = 5):
    """Вакансии, похожие на данную (косинусная близость LSA-векторов описаний).

    Args:
        vacancy_id: id вакансии (из search_vacancies / match_profile / semantic_search).
        limit: сколько похожих вернуть (1-10).

    Returns:
        dict: похожие вакансии с score сходства и методика.
    """
    idx = _index()
    if idx is None:
        return {"error": "семантический индекс не собран — запустите tools/build_semantic.py",
                "vacancies": []}
    limit = max(1, min(int(limit or 5), 10))
    vid = str(vacancy_id)
    if vid not in idx["ids"]:
        return {"error": "вакансии нет в семантическом индексе (индекс по свежим слоям датасета)",
                "vacancies": []}
    pos = idx["ids"].index(vid)
    sims = (idx["X"] @ idx["X"][pos]).ravel()
    sims[pos] = -1.0
    order = np.argsort(-sims)[:limit]
    cand = [(float(sims[i]), idx["ids"][i]) for i in order if sims[i] > 0]
    return {
        "found": len(cand),
        "base": vid,
        "vacancies": _cards([v for _, v in cand]),
        "scores": [round(s, 3) for s, _ in cand],
        "method": f"TF-IDF + LSA, косинусная близость описаний; показано {len(cand)}",
    }
