"""Наполнение БД ДЕМО-данными: категории, товары (из прототипа Figma), пользователи, синтетические отзывы.

Отзывы - шаблонные тексты (см. data/demo_corpus.py), их нельзя выдавать за реальные.
"""
import json
import os
import random
from datetime import datetime, timedelta

from app import db
from app.core import hash_password
from data import demo_corpus as dc

N_REVIEWS = 90


def _compose(rnd, clauses):
    text = clauses[0]
    for c in clauses[1:]:
        text += rnd.choice(dc.CONNECT) + c
    text = text[0].upper() + text[1:]
    if not text.endswith("."):
        text += "."
    return text


def generate_reviews(now=None, seed=2026):
    """Возвращает список словарей: title, rating, text, date, is_verified."""
    rnd = random.Random(seed)
    now = now or datetime.now()
    out = []
    for title, brand, cat, price, specs in dc.CATALOG:
        quality = dc.QUALITY.get(title, {})
        aspects = dc.CATEGORY_ASPECTS[cat]
        for i in range(N_REVIEWS):
            days_ago = rnd.randint(0, 180)
            n_cl = rnd.choice([2, 2, 3])
            picked = rnd.sample(aspects + (["доставка"] if rnd.random() < 0.15 else []), min(n_cl, len(aspects)))
            clauses, signs = [], []
            for a in picked:
                p_pos = quality.get(a, 0.7)
                if title == "Xiaomi 14T Pro" and a == "нагрев":
                    p_pos = 0.2 if days_ago <= 30 else 0.8  # рост жалоб в последний месяц - для кабинета продавца
                pos = rnd.random() < p_pos
                src = dc.POS if pos else dc.NEG
                clauses.append(rnd.choice(src[a])); signs.append(1 if pos else -1)
            if rnd.random() < 0.25:
                clauses.insert(rnd.randint(0, len(clauses)), rnd.choice(dc.NEUTRAL))
            balance = sum(signs)
            stars = rnd.choice(dc.STARS_POS) if balance > 0 else rnd.choice(dc.STARS_NEG) if balance < 0 else 3
            out.append({"title": title, "rating": stars, "text": _compose(rnd, clauses),
                        "date": (now - timedelta(days=days_ago, hours=rnd.randint(0, 20))).isoformat(timespec="seconds"),
                        "is_verified": 1 if rnd.random() < 0.8 else 0})
    # история для кабинета продавца: после «обновления прошивки» резко выросли жалобы на нагрев Xiaomi 14T Pro
    for k in range(14):
        a2 = rnd.choice(["камера", "экран", "автономность"])
        clauses = [rnd.choice(dc.NEG["нагрев"]), rnd.choice(dc.POS[a2])]
        out.append({"title": "Xiaomi 14T Pro", "rating": rnd.choice([2, 3, 3]), "text": _compose(rnd, clauses),
                    "date": (now - timedelta(days=rnd.randint(1, 26), hours=rnd.randint(0, 20))).isoformat(timespec="seconds"), "is_verified": 1})
    # накрученные отзывы: шаблонные восторги в один день без подтверждения покупки
    burst_day = now - timedelta(days=9)
    for title, n in (("Redmi Note 13 Pro", 8), ("JBL Tune 770NC", 7)):
        for k in range(n):
            txt = rnd.choice(["Супер! Всё отлично, рекомендую!", "Супер! Все отлично рекомендую", "Супер, всё отлично, рекомендую!"])
            out.append({"title": title, "rating": 5, "text": txt, "date": (burst_day + timedelta(minutes=7 * k)).isoformat(timespec="seconds"), "is_verified": 0})
    return out


def build_review_texts():
    return [r["text"] for r in generate_reviews()]


FAQ = [
    ("delivery", "доставка доставят срок сроки курьер самовывоз сколько стоит доставка отследить заказ где мой заказ",
     "ДЕМО-условия: доставка по Алматы - 1-2 дня (от 1 500 ₸, бесплатно от 50 000 ₸), по Казахстану - 3-7 дней. Самовывоз из пунктов выдачи бесплатный. Статус заказа виден в личном кабинете."),
    ("returns", "возврат вернуть обмен вернуть товар сроки возврата",
     "ДЕМО-условия: товар надлежащего качества можно вернуть в течение 14 дней при сохранении упаковки и товарного вида. Деньги возвращаются на карту в течение 3-10 рабочих дней."),
    ("warranty", "гарантия гарантийный сервис ремонт",
     "ДЕМО-условия: гарантия производителя на электронику - от 12 месяцев, срок указан в карточке товара."),
]


def seed_raw(path=None, reset=True, n_reviews=None):
    """Создаёт БД и вставляет необработанные отзывы (analyzed=0). Обработку выполняет конвейер."""
    db.init_db(path, reset=reset)
    now = datetime.now().isoformat(timespec="seconds")
    with db.session(path) as c:
        uids = {}
        for email, pwd, role in (("buyer@demo.kz", "buyer123", "buyer"), ("seller@demo.kz", "seller123", "seller"), ("admin@demo.kz", "admin123", "admin")):
            cur = c.execute("INSERT INTO users (email, password_hash, role, created_at) VALUES (?,?,?,?)", (email, hash_password(pwd), role, now))
            uids[role] = cur.lastrowid
        cats = {}
        for name in ("смартфоны", "ноутбуки", "наушники"):
            cats[name] = c.execute("INSERT INTO categories (name) VALUES (?)", (name,)).lastrowid
        pid = {}
        for title, brand, cat, price, specs in dc.CATALOG:
            pid[title] = c.execute("INSERT INTO products (category_id, seller_id, title, brand, price, specs) VALUES (?,?,?,?,?,?)",
                                   (cats[cat], uids["seller"], title, brand, price, json.dumps(specs, ensure_ascii=False))).lastrowid
        for r in generate_reviews():
            c.execute("INSERT INTO reviews (product_id, user_id, rating, text, date, is_verified, analyzed) VALUES (?,?,?,?,?,?,0)",
                      (pid[r["title"]], uids["buyer"], r["rating"], r["text"], r["date"], r["is_verified"]))
        for key, kw, ans in FAQ:
            c.execute("INSERT INTO faq (key, keywords, answer) VALUES (?,?,?)", (key, kw, ans))
