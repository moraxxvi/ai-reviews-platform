"""Диалоговый сервис (СРО2, п. 2.3, 2.8): намерение -> сущности -> маршрутизация -> ответ с источниками.

Правила логики (из СРО2, п. 2.8), реализованные ниже:
  * уверенность < порога -> уточняющий вопрос;
  * подбор: жёсткие фильтры (категория, бюджет, бренд), затем ранжирование по формуле (3);
  * вопрос о товаре: ответ только на найденных отзывах и данных БД, иначе отказ или оператор;
  * фактические значения (цена, наличие) берутся из БД, а не генерируются;
  * ошибка ИИ-сервиса не ломает чат - возвращается сообщение о временной недоступности.
"""
import json
from datetime import datetime

import numpy as np

from ai import intents as intents_mod
from ai.lexicon import CATEGORIES
from ai.preprocess import clean, mask_personal_data
from ai.rag import GroundedGenerator, retrieve
from ai.recommender import rank_products
from . import catalog, db
from .core import ApiError
from .pipeline import Models

CATEGORY_ASPECTS = {
    "смартфоны": ["камера", "производительность", "автономность", "нагрев", "цена", "экран"],
    "ноутбуки": ["экран", "производительность", "автономность", "качество сборки", "цена", "звук"],
    "наушники": ["звук", "автономность", "качество сборки", "цена"],
}
MAX_LEN = 1000
_classifier = None
_generator = GroundedGenerator()


def _clf():
    global _classifier
    if _classifier is None:
        _classifier = intents_mod.IntentClassifier()
    return _classifier


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _get_dialog(conn, user, dialog_id):
    if dialog_id:
        d = db.one(conn, "SELECT * FROM dialogs WHERE dialog_id = ?", (dialog_id,))
        if not d:
            raise ApiError(404, "Диалог не найден")
        if d["user_id"] and (not user or d["user_id"] != user["user_id"]):
            raise ApiError(403, "Это чужой диалог")
        return d
    cur = conn.execute("INSERT INTO dialogs (user_id, started_at, channel) VALUES (?,?, 'web')", (user["user_id"] if user else None, _now()))
    return db.one(conn, "SELECT * FROM dialogs WHERE dialog_id = ?", (cur.lastrowid,))


def _save(conn, dialog_id, role, text, intent=None, sources=None):
    # в журнал пишем обезличенный текст (СРО2, п. 2.5)
    conn.execute("INSERT INTO messages (dialog_id, role, text, intent, sources, created_at) VALUES (?,?,?,?,?,?)",
                 (dialog_id, role, mask_personal_data(text), intent, json.dumps(sources or []), _now()))


def handle_message(conn, user, body):
    text = (body.get("message") or "").strip()
    if not text:
        raise ApiError(400, "Пустое сообщение")
    if len(text) > MAX_LEN:
        raise ApiError(400, f"Сообщение слишком длинное (максимум {MAX_LEN} символов)")
    dialog = _get_dialog(conn, user, body.get("dialog_id"))
    ctx = json.loads(dialog["context"] or "{}")
    try:
        _save(conn, dialog["dialog_id"], "user", text)
    except Exception:  # сбой журнала не блокирует ответ (СРО2, п. 2.8)
        pass
    try:
        result = _respond(conn, text, ctx, dialog)
    except Exception as exc:  # недоступность ИИ-сервиса: деградация до каталога
        print(f"[chat] ошибка ИИ-сервиса: {exc!r}")
        result = {"reply": "AI-консультант временно недоступен. Вы можете воспользоваться каталогом и фильтрами, а я скоро вернусь.",
                  "intent": None, "confidence": 0.0, "entities": {}, "products": [], "sources": [], "escalate": False, "degraded": True}
    conn.execute("UPDATE dialogs SET context = ?, status = ? WHERE dialog_id = ?",
                 (json.dumps(ctx, ensure_ascii=False), "escalated" if result.get("escalate") else dialog["status"], dialog["dialog_id"]))
    try:
        _save(conn, dialog["dialog_id"], "assistant", result["reply"], result.get("intent"), [s["review_id"] for s in result.get("sources", [])])
    except Exception:
        pass
    result["dialog_id"] = dialog["dialog_id"]
    return result


def _empty(reply, intent=None, conf=0.0, ents=None, **kw):
    r = {"reply": reply, "intent": intent, "confidence": round(conf, 3), "entities": ents or {}, "products": [], "sources": [], "escalate": False}
    r.update(kw)
    return r


def _respond(conn, text, ctx, dialog):
    products = catalog.list_all_products(conn)
    intent, conf = _clf().predict(text)
    ents = intents_mod.extract_entities(text, products)
    by_id = {p["product_id"]: p for p in products}

    if conf < intents_mod.CONF_THRESHOLD:
        return _empty("Не совсем понял вопрос. Уточните, пожалуйста: вы хотите подобрать товар, узнать мнение покупателей о конкретной модели или сравнить несколько? Например: «смартфон до 300 000 ₸ с хорошей камерой».",
                      None, conf, ents, clarify=True)

    if intent == "приветствие":
        return _empty("Здравствуйте! Я AI-консультант. Помогаю подобрать товар по отзывам реальных покупателей, отвечаю на вопросы о моделях и сравниваю их. С чего начнём?", intent, conf, ents)

    if intent == "поддержка":
        return _empty("Передаю диалог оператору. Он подключится в ближайшее время, а пока можете описать проблему подробнее. (Демо: оператор не подключён, обращение помечено как эскалированное.)",
                      intent, conf, ents, escalate=True)

    if intent == "доставка_возврат":
        return _faq(conn, text, intent, conf, ents)

    if intent == "сравнение":
        ids = list(ents["products"])
        brands = _brands_in(text, products)
        if len(brands) >= 2 and len({by_id[i]["brand"] for i in ids}) < 2:
            # «что лучше Xiaomi или iPhone»: сравниваем лучшие по оценке модели брендов в одной категории
            ids = _best_per_brand(conn, brands, ents, ids, by_id, products)
            note = "Сравниваю лучшие по оценке модели этих брендов: "
        else:
            if len(ids) < 2 and ctx.get("last_product") and ctx["last_product"] not in ids:
                ids.append(ctx["last_product"])
            note = "Сравниваю "
        if len(ids) < 2:
            return _empty("Назовите две модели или два бренда, которые нужно сравнить, например: «сравни iPhone 15 и Galaxy S24 FE» или «что лучше, Xiaomi или Samsung».", intent, conf, ents, clarify=True)
        ids = ids[:2]
        ctx["last_product"] = ids[0]
        return _empty(_compare_reply(conn, ids, by_id, note), intent, conf, ents, products=[_card(by_id, i) for i in ids], compare_ids=ids)

    if intent == "подбор":
        return _recommend(conn, text, ctx, ents, intent, conf, by_id)

    # вопрос_о_товаре
    return _product_question(conn, text, ctx, ents, intent, conf, by_id)


_BRAND_HINTS = {"iphone": "Apple", "айфон": "Apple", "macbook": "Apple", "airpods": "Apple", "galaxy": "Samsung", "самсунг": "Samsung", "pixel": "Google",
                "redmi": "Xiaomi", "poco": "Xiaomi", "сяоми": "Xiaomi", "ксиаоми": "Xiaomi", "эппл": "Apple", "асус": "ASUS", "леново": "Lenovo", "сони": "Sony",
                "хуавей": "Huawei", "хонор": "Honor", "делл": "Dell"}


def _brands_in(text, products):
    t = text.lower()
    found = []
    for b in sorted({p["brand"] for p in products}):
        if b.lower() in t and b not in found:
            found.append(b)
    for k, b in _BRAND_HINTS.items():
        if k in t and b not in found:
            found.append(b)
    return found


def _best_per_brand(conn, brands, ents, ids, by_id, products):
    """Для каждого бренда - упомянутая модель (если названа точно) или лучшая по оценке в категории."""
    cat = ents.get("category") or (by_id[ids[0]]["category"] if ids else None)
    if not cat:  # категория не названа: берём первую, где есть товары обоих брендов
        for c in ("смартфоны", "ноутбуки", "наушники"):
            if all(any(p["brand"] == b and p["category"] == c for p in products) for b in brands[:2]):
                cat = c
                break
    cards = {c["product_id"]: c for c in catalog.product_cards(conn)}
    out = []
    for b in brands[:2]:
        named = [i for i in ids if by_id[i]["brand"] == b]
        pool = named or [p["product_id"] for p in products if p["brand"] == b and p["category"] == cat]
        pool = [i for i in pool if cards.get(i, {}).get("rating")]
        if pool:
            out.append(max(pool, key=lambda i: cards[i]["rating"]))
    return out


def _money(n):
    return f"{n:,}".replace(",", " ") + " ₸"


def _compare_reply(conn, ids, by_id, note):
    cmp = catalog.compare(conn, ids)
    a, b = ids
    ta, tb = by_id[a]["title"], by_id[b]["title"]
    info = {p["product_id"]: p for p in cmp["products"]}
    wins = {a: [], b: []}
    lines = []
    for row in cmp["aspects"]:
        v = row["values"]
        if str(a) in v and str(b) in v:
            pa, pb = round(v[str(a)]["pos_share"] * 100), round(v[str(b)]["pos_share"] * 100)
            if abs(pa - pb) < 5:
                lines.append(f"• {row['aspect'].capitalize()}: примерно поровну ({pa}% и {pb}%)")
            else:
                w = a if pa > pb else b
                wins[w].append(row["aspect"])
                lines.append(f"• {row['aspect'].capitalize()}: лучше у {by_id[w]['title']} ({max(pa, pb)}% против {min(pa, pb)}%)")
    head = f"{note}{ta} ({info[a]['rating']}★, {_money(info[a]['price'])}) и {tb} ({info[b]['rating']}★, {_money(info[b]['price'])})."
    if not lines:
        return head + "\nДанных для сравнения по аспектам пока мало."
    verdict = []
    for w, o in ((a, b), (b, a)):
        if len(wins[w]) > len(wins[o]):
            verdict.append(f"Итог по отзывам: {by_id[w]['title']} выигрывает по {len(wins[w])} из {len(lines)} аспектов ({', '.join(wins[w])}).")
    if not verdict:
        verdict.append("Итог по отзывам: модели примерно равны, выбор зависит от того, какой аспект важнее.")
    cheap = a if info[a]["price"] < info[b]["price"] else b
    diff = abs(info[a]["price"] - info[b]["price"])
    verdict.append(f"{by_id[cheap]['title']} дешевле на {_money(diff)}.")
    return head + "\n" + "\n".join(lines) + "\n" + " ".join(verdict)


def _card(by_id, pid):
    p = by_id[pid]
    return {"product_id": pid, "title": p["title"], "price": p["price"], "category": p["category"]}


def _faq(conn, text, intent, conf, ents):
    t = set(clean(text).split())
    best, best_n = None, 0
    for f in db.rows(conn, "SELECT * FROM faq"):
        kw = {w for w in clean(f["keywords"]).split() if len(w) > 3}
        n = sum(1 for w in kw if any(tok.startswith(w[:5]) for tok in t))
        if n > best_n:
            best, best_n = f, n
    if best:
        return _empty(best["answer"], intent, conf, ents)
    return _empty("По вопросам доставки и возврата лучше уточнить у оператора. Могу передать ему обращение.", intent, conf, ents, offer_operator=True)


def _recommend(conn, text, ctx, ents, intent, conf, by_id):
    category = ents["category"] or ctx.get("last_category")
    if not category:
        return _empty("Какую категорию товаров вы рассматриваете: смартфоны, ноутбуки или наушники?", intent, conf, ents, clarify=True)
    budget = ents["budget"] or (ctx.get("last_budget") if not ents["category"] else None)
    aspects = ents["aspects"] or ctx.get("last_aspects") or []
    cands = [p for p in by_id.values() if p["category"] == category]
    if ents["brand"]:
        cands = [p for p in cands if (p["brand"] or "").lower() == ents["brand"]]
    if budget:  # жёсткое ограничение
        cands = [p for p in cands if p["price"] <= budget]
    ctx.update({"last_category": category, "last_budget": budget, "last_aspects": aspects})
    if not cands:
        return _empty(f"В категории «{category}» нет товаров{(' до %s ₸' % f'{budget:,}'.replace(',', ' ')) if budget else ''}. Попробуйте увеличить бюджет или убрать бренд.", intent, conf, ents)
    prof = catalog.profiles_for(conn, [p["product_id"] for p in cands])
    ranked = rank_products(cands, prof, aspects, CATEGORY_ASPECTS.get(category, []))[:3]
    cards = []
    for r in ranked:
        why = [f"{d['aspect']} - {round(d['pos_share'] * 100)}% положительных ({round(d['n_mentions'])} упоминаний)" for d in r["details"][:2]]
        cards.append({"product_id": r["product_id"], "title": r["title"], "price": r["price"], "category": category, "score": r["score"], "why": why})
    ctx["last_product"] = ranked[0]["product_id"]
    used = ", ".join(ranked[0]["aspects_used"])
    reply = f"Подобрал варианты в категории «{category}»" + (f" до {budget:,} ₸".replace(",", " ") if budget else "") + f". Ранжирование по аспектам: {used}. Оценки построены по отзывам покупателей (без подозрительных)."
    return _empty(reply, intent, conf, ents, products=cards)


def _quotes(conn, product_id, qvec, aspects):
    """Цитаты из отзывов по аспектам: ближайшие к вопросу по косинусной близости клаузы."""
    rows = db.rows(conn, f"""SELECT ra.aspect, ra.sentiment, ra.clause, ra.review_id, ra.confidence FROM review_aspects ra
                            JOIN reviews r USING(review_id) WHERE r.product_id = ? AND {catalog.valid_reviews_clause()}""", (product_id,))
    rows = [r for r in rows if not aspects or r["aspect"] in aspects]
    if not rows:
        return {"positive": [], "negative": []}
    cv = Models.get().embedder.encode([r["clause"] for r in rows])
    # шаг Retrieval из RAG: ранжируем клаузы отзывов по косинусной близости к вопросу (формула (2))
    ranked = retrieve(qvec, cv, rows, k=len(rows), sim_min=-1.0)
    out = {"positive": [], "negative": []}
    seen = set()
    for r in sorted(ranked, key=lambda x: -(x["sim"] + 0.1 * float(x["confidence"] or 0))):
        key = (r["aspect"], r["sentiment"])
        if key in seen:
            continue
        seen.add(key)
        out[r["sentiment"]].append(r)
    return out


_GENERAL = ("что говорят", "что пишут", "минус", "плюс", "отзыв", "расскажи", "стоит ли", "покупатели", "мнение", "как себя", "в целом", "достоинств", "недостатк", "проблем")


def _product_question(conn, text, ctx, ents, intent, conf, by_id):
    scored = ents.get("products_scored") or []
    if len(scored) > 1 and scored[0][1] == scored[1][1]:  # несколько равноценных совпадений - просим уточнить
        names = " или ".join(by_id[p]["title"] for p, _ in scored[:3] if scored[0][1] == _)
        return _empty(f"Уточните модель: {names}?", intent, conf, ents, clarify=True)
    pid = (ents["products"] or [None])[0] or ctx.get("last_product")
    if not pid:
        return _empty("О какой модели речь? Назовите товар, например: «как батарея у iPhone 15».", intent, conf, ents, clarify=True)
    ctx["last_product"] = pid
    title, price = by_id[pid]["title"], by_id[pid]["price"]
    prof = catalog.profiles_for(conn, [pid]).get(pid, {})
    qvec = Models.get().embedder.encode([text])[0]
    t = clean(text)
    want_price = any(w in t for w in ("сколько стоит", "цена", "цену", "стоимость"))
    asp = ents["aspects"]
    general = any(w in t for w in _GENERAL)
    if not asp and not want_price and not general:
        # вопрос о теме, которой нет среди аспектов анализа: не выдумываем ответ (принцип «безопасность по умолчанию»)
        return _empty(f"В отзывах о {title} я не нашёл информации по этому вопросу, поэтому не буду гадать. Могу рассказать про {', '.join(sorted(prof, key=lambda a: -prof[a]['n_mentions'])[:4]) or 'аспекты из отзывов'} или передать вас оператору.",
                      intent, conf, ents, products=[_card(by_id, pid)], grounded=False, offer_operator=True)
    quotes = _quotes(conn, pid, qvec, asp or None)
    text_out, sources, grounded = _generator_call(title, asp, prof, quotes, price, want_price)
    src_info = []
    for sid in sources[:5]:
        r = db.one(conn, "SELECT review_id, text, rating, date FROM reviews WHERE review_id = ?", (sid,))
        if r:
            src_info.append(r)
    reply = text_out
    return _empty(reply, intent, conf, ents, products=[_card(by_id, pid)], sources=src_info, grounded=grounded,
                  offer_operator=not grounded)


def _generator_call(title, asp, prof, quotes, price, want_price):
    flat = {"positive": [], "negative": []}
    for k in flat:
        for q in quotes.get(k, []):
            flat[k].append({"aspect": q["aspect"], "clause": q["clause"], "review_id": q["review_id"]})
    return _GENERATOR.compose(title, asp, prof, flat, price=price, want_price=want_price)


_GENERATOR = GroundedGenerator()


def list_dialogs(conn, user):
    ds = db.rows(conn, "SELECT dialog_id, started_at, channel, status FROM dialogs WHERE user_id = ? ORDER BY dialog_id DESC LIMIT 30", (user["user_id"],))
    for d in ds:
        m = db.one(conn, "SELECT text FROM messages WHERE dialog_id = ? AND role = 'user' ORDER BY message_id LIMIT 1", (d["dialog_id"],))
        d["title"] = (m["text"][:40] if m else "Новый диалог")
    return {"items": ds}


def dialog_messages(conn, user, dialog_id):
    d = db.one(conn, "SELECT * FROM dialogs WHERE dialog_id = ?", (dialog_id,))
    if not d:
        raise ApiError(404, "Диалог не найден")
    if d["user_id"] != user["user_id"]:
        raise ApiError(403, "Это чужой диалог")
    return {"items": db.rows(conn, "SELECT role, text, intent, sources, created_at FROM messages WHERE dialog_id = ? ORDER BY message_id", (dialog_id,))}
