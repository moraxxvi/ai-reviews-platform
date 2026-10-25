"""Сервисы «Каталог и поиск», «Отзывы и анализ», «Сравнение», «Аналитика продавца» (СРО2, табл. 3)."""
import json
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

from ai.embeddings import from_blob
from ai.lexicon import ASPECT_LIST
from ai.recommender import N_MIN
from ai.antifraud import EXCLUDE_ABOVE
from . import db
from .core import ApiError, cache
from .pipeline import Models

MIN_SHOW = 5  # минимальное число упоминаний аспекта для показа в «плюсах/минусах»


def _specs(v):
    try:
        return json.loads(v) if isinstance(v, str) else (v or {})
    except Exception:
        return {}


def valid_reviews_clause():
    return f"(r.fake_score IS NULL OR r.fake_score < {EXCLUDE_ABOVE})"


def list_all_products(conn):
    """Лёгкий список (для NER и сопоставления названий)."""
    return db.rows(conn, "SELECT p.product_id, p.title, p.brand, c.name AS category, p.price FROM products p JOIN categories c USING(category_id)")


def product_cards(conn, where="", params=()):
    sql = f"""
    SELECT p.product_id, p.title, p.brand, p.price, p.specs, c.name AS category,
           (SELECT ROUND(AVG(r.rating),1) FROM reviews r WHERE r.product_id = p.product_id AND {valid_reviews_clause()}) AS rating,
           (SELECT COUNT(*) FROM reviews r WHERE r.product_id = p.product_id AND {valid_reviews_clause()}) AS n_reviews
    FROM products p JOIN categories c USING(category_id) {where}"""
    out = db.rows(conn, sql, params)
    for p in out:
        p["specs"] = _specs(p["specs"])
    return out


def profiles_for(conn, product_ids=None):
    sql = "SELECT product_id, aspect, pos_raw, pos_share, n_mentions FROM product_profiles"
    prm = ()
    if product_ids:
        sql += " WHERE product_id IN (%s)" % ",".join("?" * len(product_ids))
        prm = tuple(product_ids)
    res = defaultdict(dict)
    for r in db.rows(conn, sql, prm):
        res[r["product_id"]][r["aspect"]] = {"pos_raw": r["pos_raw"], "pos_share": r["pos_share"], "n_mentions": r["n_mentions"]}
    return dict(res)


def _review_matrix(conn, product_id=None):
    """Матрица эмбеддингов валидных отзывов (кэшируется)."""
    key = f"matrix:{product_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    sql = f"SELECT r.review_id, r.product_id, r.text, r.embedding FROM reviews r WHERE r.embedding IS NOT NULL AND {valid_reviews_clause()}"
    prm = ()
    if product_id:
        sql += " AND r.product_id = ?"
        prm = (product_id,)
    rs = db.rows(conn, sql, prm)
    if not rs:
        return cache.set(key, (np.zeros((0, 1), dtype=np.float32), []))
    mat = np.vstack([from_blob(r["embedding"]) for r in rs])
    meta = [{"review_id": r["review_id"], "product_id": r["product_id"], "text": r["text"]} for r in rs]
    return cache.set(key, (mat, meta))


def list_products(conn, q):
    where, prm = [], []
    if q.get("category"):
        where.append("c.name = ?"); prm.append(q["category"])
    if q.get("brand"):
        where.append("LOWER(p.brand) = LOWER(?)"); prm.append(q["brand"])
    if q.get("price_min"):
        where.append("p.price >= ?"); prm.append(_int(q["price_min"], "price_min"))
    if q.get("price_max"):
        where.append("p.price <= ?"); prm.append(_int(q["price_max"], "price_max"))
    items = product_cards(conn, ("WHERE " + " AND ".join(where)) if where else "", tuple(prm))
    if q.get("rating_min"):
        rm = float(q["rating_min"])
        items = [p for p in items if (p["rating"] or 0) >= rm]
    prof = profiles_for(conn, [p["product_id"] for p in items])
    for p in items:
        pr = prof.get(p["product_id"], {})
        good = [(a, v["pos_share"]) for a, v in pr.items() if v["n_mentions"] >= MIN_SHOW and a != "доставка"]
        p["top_aspects"] = [a for a, _ in sorted(good, key=lambda x: -x[1])[:2]]
        p["ai_badge"] = "AI-анализ"
    text = (q.get("q") or "").strip()
    if text:
        items = _search(conn, items, text)
    else:
        sort = q.get("sort", "popular")
        key = {"price_asc": lambda p: p["price"], "price_desc": lambda p: -p["price"], "rating": lambda p: -(p["rating"] or 0)}.get(sort, lambda p: -p["n_reviews"])
        items.sort(key=key)
    return {"items": items, "total": len(items), "search": text or None}


def _int(v, name):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"Параметр {name} должен быть целым числом")


def _search(conn, items, text, sem_min=0.3):
    """Текстовый + семантический поиск: совпадение с названием или близость к отзывам товара."""
    toks = text.lower().split()
    m = Models.get()
    qv = m.embedder.encode([text])[0]
    mat, meta = _review_matrix(conn)
    sims_by_pid = defaultdict(list)
    if len(meta):
        sims = mat @ qv
        for s, mt in zip(sims, meta):
            sims_by_pid[mt["product_id"]].append(float(s))
    res = []
    for p in items:
        hay = f"{p['title']} {p['brand']} {p['category']}".lower()
        lex = all(t in hay for t in toks)
        top = sorted(sims_by_pid.get(p["product_id"], []), reverse=True)[:3]
        sem = float(np.mean(top)) if top else 0.0
        if lex or sem >= sem_min:
            p["match"] = {"lexical": lex, "semantic": round(sem, 3)}
            res.append((lex, sem, p))
    res.sort(key=lambda t: (-int(t[0]), -t[1]))
    return [p for *_, p in res]


def sentiment_share(conn, product_id):
    r = db.rows(conn, f"""SELECT r.overall, COUNT(*) n FROM reviews r WHERE r.product_id = ? AND {valid_reviews_clause()} GROUP BY r.overall""", (product_id,))
    tot = sum(x["n"] for x in r) or 1
    d = {x["overall"]: x["n"] for x in r}
    return {k: round(d.get(k, 0) / tot * 100) for k in ("positive", "neutral", "negative")}, sum(x["n"] for x in r)


def conclusion_text(title, profile):
    good = sorted([(a, v) for a, v in profile.items() if v["n_mentions"] >= MIN_SHOW], key=lambda kv: -kv[1]["pos_share"])
    if not good:
        return "Недостаточно отзывов для вывода."
    best, worst = good[:2], [g for g in good[::-1][:2] if g[1]["pos_share"] < 0.6]
    s = "Сильные стороны: " + ", ".join(f"{a} ({round(v['pos_share'] * 100)}%)" for a, v in best) + "."
    if worst:
        s += " Слабые места: " + ", ".join(f"{a} ({round(v['pos_share'] * 100)}%)" for a, v in worst) + "."
    return s


def product_detail(conn, product_id):
    hit = cache.get(f"detail:{product_id}")
    if hit:
        return hit
    cards = product_cards(conn, "WHERE p.product_id = ?", (product_id,))
    if not cards:
        raise ApiError(404, "Товар не найден")
    p = cards[0]
    prof = profiles_for(conn, [product_id]).get(product_id, {})
    share, n = sentiment_share(conn, product_id)
    aspects = [{"aspect": a, **v} for a, v in sorted(prof.items(), key=lambda kv: -kv[1]["n_mentions"]) if v["n_mentions"] >= 2]
    strengths = [a["aspect"] for a in sorted(aspects, key=lambda a: -a["pos_share"]) if a["n_mentions"] >= MIN_SHOW and a["pos_share"] >= 0.7][:3]
    weaknesses = [a["aspect"] for a in sorted(aspects, key=lambda a: a["pos_share"]) if a["n_mentions"] >= MIN_SHOW and a["pos_share"] < 0.6][:3]
    excl = db.one(conn, f"SELECT COUNT(*) n FROM reviews WHERE product_id = ? AND fake_score >= {EXCLUDE_ABOVE}", (product_id,))["n"]
    p.update({"aspects": aspects, "sentiment": share, "n_analyzed": n, "n_excluded_suspicious": excl,
              "strengths": strengths, "weaknesses": weaknesses, "conclusion": conclusion_text(p["title"], prof),
              "model": Models.get().sentiment.name})
    return cache.set(f"detail:{product_id}", p)


def product_reviews(conn, product_id, limit=20):
    if not db.one(conn, "SELECT 1 x FROM products WHERE product_id = ?", (product_id,)):
        raise ApiError(404, "Товар не найден")
    revs = db.rows(conn, f"""SELECT r.review_id, r.rating, r.text, r.date, r.is_verified, r.overall, r.fake_score FROM reviews r
                              WHERE r.product_id = ? AND {valid_reviews_clause()} ORDER BY r.date DESC LIMIT ?""", (product_id, limit))
    for r in revs:
        r["aspects"] = db.rows(conn, "SELECT aspect, sentiment FROM review_aspects WHERE review_id = ?", (r["review_id"],))
        r["counted_in_ai"] = True
    return {"items": revs}


def compare(conn, ids):
    ids = [i for i in ids if isinstance(i, int)]
    if len(ids) < 2:
        raise ApiError(400, "Для сравнения нужно выбрать минимум два товара")
    if len(ids) > 4:
        raise ApiError(400, "Можно сравнить не более четырёх товаров")
    cards = [product_detail(conn, i) for i in ids]
    aspects = sorted({a["aspect"] for c in cards for a in c["aspects"] if a["n_mentions"] >= MIN_SHOW and a["aspect"] != "доставка"})
    table = []
    for a in aspects:
        row = {"aspect": a, "values": {}}
        best, best_v = None, -1
        for c in cards:
            v = next((x for x in c["aspects"] if x["aspect"] == a), None)
            if v and v["n_mentions"] >= MIN_SHOW:
                row["values"][str(c["product_id"])] = {"pos_share": v["pos_share"], "n_mentions": v["n_mentions"]}
                if v["pos_share"] > best_v:
                    best, best_v = c["product_id"], v["pos_share"]
        row["best"] = best
        table.append(row)
    return {"products": [{k: c[k] for k in ("product_id", "title", "brand", "price", "rating", "n_reviews", "strengths", "weaknesses", "specs", "category")} for c in cards], "aspects": table}


# ---------------- аналитика продавца ----------------
SENSITIVITY = {"low": {"jump": 0.3, "z_min": 4.0}, "normal": {"jump": 0.2, "z_min": 3.0}, "high": {"jump": 0.15, "z_min": 2.0}}


def _seller_where(user, product_id=None, days=0, alias_r="r"):
    where = "p.seller_id = ?" if user["role"] == "seller" else "1=1"
    prm = [user["user_id"]] if user["role"] == "seller" else []
    if product_id:
        where += " AND p.product_id = ?"; prm.append(product_id)
    if days:
        where += f" AND {alias_r}.date >= ?"; prm.append((datetime.now() - timedelta(days=days)).isoformat(timespec="seconds"))
    return where, prm


def seller_products(conn, user):
    """Таблица товаров продавца с показателями по отзывам."""
    where, prm = _seller_where(user)
    rows = db.rows(conn, f"""SELECT p.product_id, p.title, p.brand, p.price, c.name AS category,
        COUNT(r.review_id) AS n_reviews,
        SUM(CASE WHEN r.fake_score >= {EXCLUDE_ABOVE} THEN 1 ELSE 0 END) AS suspicious,
        SUM(CASE WHEN (r.fake_score IS NULL OR r.fake_score < {EXCLUDE_ABOVE}) AND r.overall = 'positive' THEN 1 ELSE 0 END) AS pos,
        SUM(CASE WHEN (r.fake_score IS NULL OR r.fake_score < {EXCLUDE_ABOVE}) AND r.overall = 'negative' THEN 1 ELSE 0 END) AS neg,
        AVG(CASE WHEN (r.fake_score IS NULL OR r.fake_score < {EXCLUDE_ABOVE}) THEN r.rating END) AS rating
        FROM products p JOIN categories c USING(category_id) LEFT JOIN reviews r ON r.product_id = p.product_id
        WHERE {where} GROUP BY p.product_id ORDER BY p.product_id""", tuple(prm))
    alerts = {}
    for a in seller_analytics(conn, user)["alerts"]:
        alerts[a["product_id"]] = alerts.get(a["product_id"], 0) + 1
    for r in rows:
        valid = (r["n_reviews"] or 0) - (r["suspicious"] or 0)
        r["rating"] = round(r["rating"], 1) if r["rating"] else None
        r["positive_pct"] = round((r["pos"] or 0) / valid * 100) if valid else 0
        r["negative_pct"] = round((r["neg"] or 0) / valid * 100) if valid else 0
        r["suspicious"] = r["suspicious"] or 0
        r["alerts"] = alerts.get(r["product_id"], 0)
        del r["pos"], r["neg"]
    return {"products": rows}


def seller_reviews(conn, user, params):
    """Отзывы на товары продавца с фильтрами: товар, тональность, аспект, подозрительные."""
    where, prm = _seller_where(user, int(params["product_id"]) if params.get("product_id") else None, int(params.get("days") or 0))
    if params.get("sentiment") in ("positive", "neutral", "negative"):
        where += " AND r.overall = ?"; prm.append(params["sentiment"])
    if str(params.get("suspicious")) == "1":
        where += f" AND r.fake_score >= {EXCLUDE_ABOVE}"
    if params.get("aspect"):
        where += " AND EXISTS (SELECT 1 FROM review_aspects ra WHERE ra.review_id = r.review_id AND ra.aspect = ?"
        prm.append(params["aspect"])
        if params.get("aspect_sentiment") in ("positive", "negative"):
            where += " AND ra.sentiment = ?"; prm.append(params["aspect_sentiment"])
        where += ")"
    limit = min(int(params.get("limit") or 50), 200)
    total = db.one(conn, f"SELECT COUNT(*) n FROM reviews r JOIN products p USING(product_id) WHERE {where}", tuple(prm))["n"]
    rows = db.rows(conn, f"""SELECT r.review_id, r.product_id, p.title, r.rating, r.text, r.date, r.overall, r.fake_score, r.is_verified
                             FROM reviews r JOIN products p USING(product_id) WHERE {where} ORDER BY r.date DESC LIMIT ?""", tuple(prm) + (limit,))
    for r in rows:
        r["aspects"] = db.rows(conn, "SELECT aspect, sentiment FROM review_aspects WHERE review_id = ?", (r["review_id"],))
        r["suspicious"] = (r["fake_score"] or 0) >= EXCLUDE_ABOVE
    return {"total": total, "reviews": rows}


def seller_analytics(conn, user, product_id=None, sensitivity="normal", days=0):
    where, prm = _seller_where(user, product_id, days)
    revs = db.rows(conn, f"""SELECT r.review_id, r.product_id, r.date, r.overall, r.rating, r.fake_score FROM reviews r JOIN products p USING(product_id)
                             WHERE {where}""", tuple(prm))
    valid = [r for r in revs if (r["fake_score"] or 0) < EXCLUDE_ABOVE]
    tot = len(valid) or 1
    dist = {k: round(sum(1 for r in valid if r["overall"] == k) / tot * 100) for k in ("positive", "neutral", "negative")}
    by_month = defaultdict(lambda: {"positive": 0, "neutral": 0, "negative": 0})
    for r in valid:
        by_month[r["date"][:7]][r["overall"] or "neutral"] += 1
    dynamics = [{"month": m, "positive_pct": round(v["positive"] / max(1, sum(v.values())) * 100), "n": sum(v.values())} for m, v in sorted(by_month.items())]
    asp_rows = db.rows(conn, f"""SELECT ra.aspect, ra.sentiment, r.date, r.product_id, p.title FROM review_aspects ra
                                 JOIN reviews r USING(review_id) JOIN products p USING(product_id)
                                 WHERE {where} AND (r.fake_score IS NULL OR r.fake_score < {EXCLUDE_ABOVE})""", tuple(prm))
    top = defaultdict(lambda: [0, 0])
    for x in asp_rows:
        top[x["aspect"]][0 if x["sentiment"] == "positive" else 1] += 1
    top_aspects = sorted(({"aspect": a, "positive": v[0], "negative": v[1], "mentions": v[0] + v[1]} for a, v in top.items()), key=lambda d: -d["mentions"])[:12]
    alerts = _alerts(asp_rows, **SENSITIVITY.get(sensitivity, SENSITIVITY['normal']))
    suspicious = sum(1 for r in revs if (r["fake_score"] or 0) >= EXCLUDE_ABOVE)
    return {"total_reviews": len(revs), "analyzed": len(valid), "suspicious_excluded": suspicious, "distribution": dist,
            "dynamics": dynamics, "top_aspects": top_aspects, "alerts": alerts}


def _alerts(asp_rows, recent_days=30, min_recent=8, min_base=8, jump=0.2, z_min=3.0):
    """Предупреждение о росте жалоб на аспект.

    Сравниваются доли негативных упоминаний за последние 30 дней и за прошлый период.
    Сигнал выдаётся, только если рост >= 20 п.п. и он статистически значим (z-критерий для
    двух долей, z >= 3,0), чтобы случайные колебания на малых выборках не давали ложных тревог.
    """
    from math import sqrt
    cutoff = (datetime.now() - timedelta(days=recent_days)).isoformat(timespec="seconds")
    g = defaultdict(lambda: {"r": [0, 0], "b": [0, 0]})
    for x in asp_rows:
        k = (x["product_id"], x["title"], x["aspect"])
        slot = "r" if x["date"] >= cutoff else "b"
        g[k][slot][1] += 1
        if x["sentiment"] == "negative":
            g[k][slot][0] += 1
    out = []
    for (pid, title, aspect), v in g.items():
        (rn, rt), (bn, bt) = v["r"], v["b"]
        if rt < min_recent or bt < min_base:
            continue
        rs, bs = rn / rt, bn / bt
        pool = (rn + bn) / (rt + bt)
        se = sqrt(pool * (1 - pool) * (1 / rt + 1 / bt)) or 1e-9
        z = (rs - bs) / se
        if rs - bs >= jump and z >= z_min:
            out.append({"product_id": pid, "title": title, "aspect": aspect, "recent_negative_pct": round(rs * 100), "baseline_negative_pct": round(bs * 100), "z": round(z, 2),
                        "message": f"Рост жалоб на «{aspect}» у {title}: {round(rs * 100)}% негативных упоминаний за 30 дней против {round(bs * 100)}% ранее"})
    return sorted(out, key=lambda a: -a["z"])[:3]
