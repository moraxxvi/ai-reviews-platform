"""Обнаружение подозрительных отзывов (антифрод) - метод ИИ №4 (правила + признаки).

Показатель подозрительности fake_score in [0, 1] складывается из признаков (СРО2, п. 2.4):
  * шаблонность - почти дословный дубль другого отзыва (косинусная близость эмбеддингов);
  * всплеск публикаций - много отзывов на один товар за один день;
  * отсутствие отметки о подтверждённой покупке;
  * короткий восторженный текст без конкретики при оценке 5.
Это эвристика, а не обученная модель: надёжного решения задачи сейчас не существует (СРО1, п. 1.6).
"""
from collections import Counter

import numpy as np

W_DUP, W_BURST, W_UNVERIFIED, W_GENERIC = 0.5, 0.25, 0.15, 0.2
DUP_SIM = 0.93
BURST_PER_DAY = 6
EXCLUDE_ABOVE = 0.7  # отзывы выше порога не учитываются в профиле


def score_reviews(reviews, embeddings):
    """reviews: list[dict(review_id, product_id, rating, text, date, is_verified)];
    embeddings: ndarray [n, d] (L2-нормированные). Возвращает list[float]."""
    n = len(reviews)
    scores = np.zeros(n)
    by_prod = {}
    for i, r in enumerate(reviews):
        by_prod.setdefault(r["product_id"], []).append(i)
    for pid, idx in by_prod.items():
        day_count = Counter(reviews[i]["date"][:10] for i in idx)
        sub = embeddings[idx]
        sim = sub @ sub.T
        np.fill_diagonal(sim, 0.0)
        for k, i in enumerate(idx):
            r = reviews[i]
            s = 0.0
            if sim[k].max() >= DUP_SIM:
                s += W_DUP
            if day_count[r["date"][:10]] >= BURST_PER_DAY:
                s += W_BURST
            if not r.get("is_verified"):
                s += W_UNVERIFIED
            words = len(r["text"].split())
            if r["rating"] == 5 and words <= 4:
                s += W_GENERIC
            scores[i] = min(1.0, s)
    return [round(float(x), 3) for x in scores]


def review_weight(fake_score):
    """Вес отзыва при агрегации: 0 для явных подделок, иначе 1 - fake_score."""
    if fake_score >= EXCLUDE_ABOVE:
        return 0.0
    return max(0.0, 1.0 - fake_score)
