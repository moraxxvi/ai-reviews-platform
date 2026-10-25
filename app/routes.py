"""REST API (СРО2, табл. 4). Обработчики не зависят от веб-фреймворка: (Ctx) -> dict.

Таблица ROUTES подключается к FastAPI в app/main.py (и к Flask-шиму в tests/ для проверки без FastAPI).
"""
import re
from datetime import datetime

from . import catalog, chat, db, pipeline
from .core import ApiError, cache, current_user, hash_password, new_session, verify_password

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _pid(ctx):
    try:
        return int(ctx.params["id"])
    except (KeyError, ValueError):
        raise ApiError(400, "Некорректный идентификатор")


def health(ctx):
    m = pipeline.Models.get()
    return {"status": "ok", "sentiment_model": m.sentiment.name, "embedder": m.embedder.backend, "time": datetime.now().isoformat(timespec="seconds")}


def products(ctx):
    with db.session() as c:
        return catalog.list_products(c, ctx.params)


def product(ctx):
    with db.session() as c:
        return catalog.product_detail(c, _pid(ctx))


def product_reviews(ctx):
    with db.session() as c:
        return catalog.product_reviews(c, _pid(ctx))


def analyze_text(ctx):
    """Живой разбор введённого отзыва: аспекты и тональность каждого (без сохранения в БД)."""
    text = (ctx.body.get("text") or "").strip()
    if not 5 <= len(text) <= 2000:
        raise ApiError(400, "Текст отзыва должен содержать от 5 до 2000 символов")
    from ai.sentiment import analyze_review
    res = analyze_review(pipeline.Models.get().sentiment, text)
    return {"overall": res["overall"], "aspects": res["aspects"], "model": pipeline.Models.get().sentiment.name}


def compare(ctx):
    ids = ctx.body.get("ids")
    if not isinstance(ids, list):
        raise ApiError(400, "Ожидается список ids")
    with db.session() as c:
        return catalog.compare(c, ids)


def chat_post(ctx):
    with db.session() as c:
        return chat.handle_message(c, current_user(c, ctx), ctx.body)


def chat_dialogs(ctx):
    with db.session() as c:
        return chat.list_dialogs(c, current_user(c, ctx, required=True))


def chat_dialog(ctx):
    with db.session() as c:
        return chat.dialog_messages(c, current_user(c, ctx, required=True), _pid(ctx))


def seller_analytics(ctx):
    with db.session() as c:
        user = current_user(c, ctx, required=True, roles=("seller", "admin"))
        pid = ctx.params.get("product_id")
        days = int(ctx.params.get("days") or 0)
        return catalog.seller_analytics(c, user, int(pid) if pid else None, ctx.params.get("sensitivity") or "normal", days)


def seller_products(ctx):
    with db.session() as c:
        user = current_user(c, ctx, required=True, roles=("seller", "admin"))
        return catalog.seller_products(c, user)


def seller_reviews(ctx):
    with db.session() as c:
        user = current_user(c, ctx, required=True, roles=("seller", "admin"))
        try:
            return catalog.seller_reviews(c, user, ctx.params)
        except ValueError:
            raise ApiError(400, "Некорректные параметры фильтра")


def post_review(ctx):
    b = ctx.body
    with db.session() as c:
        user = current_user(c, ctx, required=True)
        try:
            pid, rating = int(b.get("product_id")), int(b.get("rating"))
        except (TypeError, ValueError):
            raise ApiError(400, "product_id и rating должны быть числами")
        text = (b.get("text") or "").strip()
        if not 1 <= rating <= 5:
            raise ApiError(400, "Оценка должна быть от 1 до 5")
        if not 5 <= len(text) <= 2000:
            raise ApiError(400, "Текст отзыва должен содержать от 5 до 2000 символов")
        if not db.one(c, "SELECT 1 x FROM products WHERE product_id = ?", (pid,)):
            raise ApiError(404, "Товар не найден")
        cur = c.execute("INSERT INTO reviews (product_id, user_id, rating, text, date, is_verified, analyzed) VALUES (?,?,?,?,?,0,0)",
                        (pid, user["user_id"], rating, text, datetime.now().isoformat(timespec="seconds")))
        rid = cur.lastrowid
    # анализ запускается в фоне; кэш сбрасывается после обработки
    t = pipeline.run_in_background([pid])
    if b.get("wait"):
        t.join(timeout=30)
    cache.clear()
    return {"review_id": rid, "status": "queued", "message": "Отзыв принят и поставлен в очередь анализа"}


def login(ctx):
    email, pwd = (ctx.body.get("email") or "").strip().lower(), ctx.body.get("password") or ""
    with db.session() as c:
        u = db.one(c, "SELECT * FROM users WHERE email = ?", (email,))
        if not u or not verify_password(pwd, u["password_hash"]):
            raise ApiError(401, "Неверный email или пароль")
        return {"token": new_session(c, u["user_id"]), "user": {"email": u["email"], "role": u["role"]}}


def register(ctx):
    email, pwd = (ctx.body.get("email") or "").strip().lower(), ctx.body.get("password") or ""
    if not EMAIL.match(email):
        raise ApiError(400, "Некорректный email")
    if len(pwd) < 6:
        raise ApiError(400, "Пароль должен содержать минимум 6 символов")
    with db.session() as c:
        if db.one(c, "SELECT 1 x FROM users WHERE email = ?", (email,)):
            raise ApiError(409, "Пользователь с таким email уже существует")
        uid = c.execute("INSERT INTO users (email, password_hash, role, created_at) VALUES (?,?, 'buyer', ?)",
                        (email, hash_password(pwd), datetime.now().isoformat(timespec="seconds"))).lastrowid
        return {"token": new_session(c, uid), "user": {"email": email, "role": "buyer"}}


def me(ctx):
    with db.session() as c:
        u = current_user(c, ctx, required=True)
        return {"user": u}


# (метод, путь, обработчик). Пути в стиле FastAPI: {id}
ROUTES = [
    ("GET", "/api/health", health),
    ("GET", "/api/products", products),
    ("GET", "/api/products/{id}", product),
    ("GET", "/api/products/{id}/reviews", product_reviews),
    ("POST", "/api/compare", compare),
    ("POST", "/api/analyze", analyze_text),
    ("POST", "/api/chat", chat_post),
    ("GET", "/api/chat/dialogs", chat_dialogs),
    ("GET", "/api/chat/dialogs/{id}", chat_dialog),
    ("GET", "/api/seller/analytics", seller_analytics),
    ("GET", "/api/seller/products", seller_products),
    ("GET", "/api/seller/reviews", seller_reviews),
    ("POST", "/api/reviews", post_review),
    ("POST", "/api/auth/login", login),
    ("POST", "/api/auth/register", register),
    ("GET", "/api/auth/me", me),
]
