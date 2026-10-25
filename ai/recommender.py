"""Профили товаров по аспектам и рекомендатель - метод ИИ №5 (ранжирование).

Формулы из СРО2, п. 2.4:
  (1)  P~_a(p) = (n_a+ + k * mu_a) / (n_a + k)        - сглаженная доля положительных упоминаний
  (3)  Score(p) = sum_a w_a * P~_a(p) * c_a(p),         c_a(p) = min(1, n_a / n_min)
Веса w_a нормируются так, чтобы сумма была равна 1.
Упоминания взвешиваются весом отзыва из антифрод-модуля (подозрительные отзывы весят меньше).
"""
from collections import defaultdict

K_SMOOTH = 5.0   # k - вес априорной оценки
N_MIN = 5.0      # n_min - число упоминаний, при котором оценка считается надёжной
GLOBAL_MU = 0.5  # запасное значение mu, если по категории нет данных


def build_profiles(rows):
    """rows: iterable of (product_id, category, aspect, sentiment, weight).
    Возвращает {product_id: {aspect: {pos_raw, pos_share, n_mentions}}}."""
    pos = defaultdict(float)
    tot = defaultdict(float)
    cat_pos = defaultdict(float)
    cat_tot = defaultdict(float)
    for pid, cat, aspect, sent, w in rows:
        if w <= 0 or sent == "neutral":
            continue
        tot[(pid, aspect)] += w
        cat_tot[(cat, aspect)] += w
        if sent == "positive":
            pos[(pid, aspect)] += w
            cat_pos[(cat, aspect)] += w
    prod_cat = {pid: cat for pid, cat, *_ in rows}
    profiles = defaultdict(dict)
    for (pid, aspect), n in tot.items():
        cat = prod_cat[pid]
        mu = cat_pos[(cat, aspect)] / cat_tot[(cat, aspect)] if cat_tot[(cat, aspect)] else GLOBAL_MU
        p_plus = pos[(pid, aspect)]
        profiles[pid][aspect] = {
            "pos_raw": round(p_plus / n, 4),
            "pos_share": round((p_plus + K_SMOOTH * mu) / (n + K_SMOOTH), 4),
            "n_mentions": round(n, 2),
        }
    return dict(profiles)


def score_product(profile, weights):
    """profile: {aspect: {pos_share, n_mentions}}; weights: {aspect: w}. Возвращает (score, details)."""
    total_w = sum(weights.values()) or 1.0
    score, details = 0.0, []
    for aspect, w in weights.items():
        info = profile.get(aspect)
        if not info:
            continue
        conf = min(1.0, info["n_mentions"] / N_MIN)
        contrib = (w / total_w) * info["pos_share"] * conf
        score += contrib
        details.append({"aspect": aspect, "pos_share": info["pos_share"], "n_mentions": info["n_mentions"], "contribution": round(contrib, 4)})
    return score, details


def rank_products(candidates, profiles, aspects, category_aspects):
    """candidates: list[dict(product_id, ...)]. aspects - аспекты из запроса (может быть пусто)."""
    use = aspects or category_aspects
    weights = {a: 1.0 for a in use}
    ranked = []
    for p in candidates:
        s, det = score_product(profiles.get(p["product_id"], {}), weights)
        ranked.append({**p, "score": round(s, 4), "details": sorted(det, key=lambda d: -d["contribution"]), "aspects_used": list(use)})
    ranked.sort(key=lambda x: -x["score"])
    return ranked
