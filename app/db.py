"""Слой данных. Реляционная схема из СРО2 (рис. 7, табл. 5), реализованная на SQLite.

Отличия от СРО2 (зафиксированы в главе 3):
  * СУБД - SQLite вместо PostgreSQL + pgvector: для учебной демонстрации не нужен отдельный сервер;
    эмбеддинги хранятся как BLOB (float32), поиск - скалярное произведение в NumPy.
    Структура таблиц и связи сохранены, переход на PostgreSQL требует только смены драйвера.
  * Добавлены служебные поля: users.password_hash, reviews.review_id-индексы, dialogs.status
    (для передачи оператору), таблицы sessions и faq.
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "shop.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('buyer','seller','admin')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES categories(category_id)
);
CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(category_id),
    seller_id INTEGER REFERENCES users(user_id),
    title TEXT NOT NULL,
    brand TEXT,
    price INTEGER NOT NULL,
    specs TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    user_id INTEGER REFERENCES users(user_id),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    text TEXT NOT NULL,
    date TEXT NOT NULL,
    is_verified INTEGER NOT NULL DEFAULT 0,
    fake_score REAL,
    overall TEXT,
    embedding BLOB,
    analyzed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_reviews_product ON reviews(product_id);
CREATE TABLE IF NOT EXISTS review_aspects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(review_id) ON DELETE CASCADE,
    aspect TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    confidence REAL,
    clause TEXT
);
CREATE INDEX IF NOT EXISTS ix_ra_review ON review_aspects(review_id);
CREATE TABLE IF NOT EXISTS product_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    aspect TEXT NOT NULL,
    pos_raw REAL,
    pos_share REAL NOT NULL,
    n_mentions REAL NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (product_id, aspect)
);
CREATE TABLE IF NOT EXISTS dialogs (
    dialog_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(user_id),
    started_at TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'web',
    status TEXT NOT NULL DEFAULT 'active',
    context TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id INTEGER NOT NULL REFERENCES dialogs(dialog_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    intent TEXT,
    sources TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS faq (
    key TEXT PRIMARY KEY,
    keywords TEXT NOT NULL,
    answer TEXT NOT NULL
);
"""


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path=None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path=None, reset=False):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if reset and os.path.exists(p):
        os.remove(p)
    with session(p) as c:
        c.executescript(SCHEMA)


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None
