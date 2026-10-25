"""Офлайн-конвейер обработки отзывов (СРО2, п. 2.4 и 2.8: «Фоновые задачи»).

Для новых отзывов: ABSA -> эмбеддинг -> антифрод -> пересчёт профилей товаров.
В демо запускается синхронно из scripts/seed_db.py и фоновым потоком после POST /api/reviews.
"""
import threading
from datetime import datetime

import numpy as np

from ai import antifraud
from ai.embeddings import Embedder, from_blob, to_blob
from ai.recommender import build_profiles
from ai.sentiment import SentimentModel, analyze_review
from . import db

_lock = threading.Lock()


class Models:
    """Ленивая загрузка моделей: создаются один раз на процесс."""
    _inst = None

    def __init__(self):
        self.sentiment = SentimentModel()
        self.embedder = Embedder()

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    @classmethod
    def reset(cls):
        cls._inst = None


def process_pending(conn, product_ids=None):
    """Обрабатывает отзывы с analyzed=0. Возвращает множество затронутых product_id."""
    m = Models.get()
    sql = "SELECT review_id, product_id, text FROM reviews WHERE analyzed = 0"
    pend = db.rows(conn, sql)
    if product_ids is not None:
        pend = [r for r in pend if r["product_id"] in product_ids]
    if not pend:
        return set()
    embs = m.embedder.encode([r["text"] for r in pend])
    now = datetime.now().isoformat(timespec="seconds")
    for r, e in zip(pend, embs):
        res = analyze_review(m.sentiment, r["text"])
        conn.execute("DELETE FROM review_aspects WHERE review_id = ?", (r["review_id"],))
        for a in res["aspects"]:
            conn.execute("INSERT INTO review_aspects (review_id, aspect, sentiment, confidence, clause) VALUES (?,?,?,?,?)",
                         (r["review_id"], a["aspect"], a["sentiment"], a["confidence"], a["clause"]))
        conn.execute("UPDATE reviews SET overall = ?, embedding = ?, analyzed = 1 WHERE review_id = ?",
                     (res["overall"], to_blob(e), r["review_id"]))
    touched = {r["product_id"] for r in pend}
    for pid in touched:
        _rescore_fraud(conn, pid)
    recompute_profiles(conn, touched)
    return touched


def _rescore_fraud(conn, pid):
    revs = db.rows(conn, "SELECT review_id, product_id, rating, text, date, is_verified, embedding FROM reviews WHERE product_id = ? AND embedding IS NOT NULL", (pid,))
    if not revs:
        return
    emb = np.vstack([from_blob(r["embedding"]) for r in revs])
    scores = antifraud.score_reviews(revs, emb)
    for r, s in zip(revs, scores):
        conn.execute("UPDATE reviews SET fake_score = ? WHERE review_id = ?", (s, r["review_id"]))


def recompute_profiles(conn, product_ids=None):
    """Пересчитывает product_profiles по формуле (1). mu_a считается по категории по всем товарам."""
    rows = db.rows(conn, """
        SELECT p.product_id, c.name AS category, ra.aspect, ra.sentiment, r.fake_score
        FROM review_aspects ra
        JOIN reviews r ON r.review_id = ra.review_id
        JOIN products p ON p.product_id = r.product_id
        JOIN categories c ON c.category_id = p.category_id""")
    data = [(x["product_id"], x["category"], x["aspect"], x["sentiment"], antifraud.review_weight(x["fake_score"] or 0.0)) for x in rows]
    profiles = build_profiles(data)
    targets = set(profiles) if product_ids is None else set(product_ids)
    now = datetime.now().isoformat(timespec="seconds")
    for pid in targets:
        conn.execute("DELETE FROM product_profiles WHERE product_id = ?", (pid,))
        for aspect, v in profiles.get(pid, {}).items():
            conn.execute("INSERT INTO product_profiles (product_id, aspect, pos_raw, pos_share, n_mentions, updated_at) VALUES (?,?,?,?,?,?)",
                         (pid, aspect, v["pos_raw"], v["pos_share"], v["n_mentions"], now))


def run_in_background(product_ids):
    """Фоновая задача после добавления отзыва (замена очереди Celery/Redis из СРО2)."""
    def job():
        with _lock:
            with db.session() as conn:
                process_pending(conn, set(product_ids))
    t = threading.Thread(target=job, daemon=True)
    t.start()
    return t
