"""Общие вспомогательные средства сервера: ошибки, контекст запроса, кэш, аутентификация."""
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import db


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Ctx:
    """Запрос в формате, не зависящем от веб-фреймворка (адаптеры: FastAPI и Flask-шим для тестов)."""
    params: dict = field(default_factory=dict)   # path + query
    body: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)

    def token(self):
        h = self.headers.get("authorization") or self.headers.get("Authorization") or ""
        return h[7:] if h.lower().startswith("bearer ") else None


class TTLCache:
    """Простой кэш в памяти (замена Redis из СРО2 для демо)."""

    def __init__(self, ttl=60):
        self.ttl, self._d = ttl, {}

    def get(self, key):
        v = self._d.get(key)
        if v and time.time() - v[0] < self.ttl:
            return v[1]
        return None

    def set(self, key, value):
        self._d[key] = (time.time(), value)
        return value

    def clear(self):
        self._d.clear()


cache = TTLCache(ttl=60)


def new_session(conn, user_id):
    token = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)", (token, user_id, datetime.now().isoformat(timespec="seconds")))
    return token


def current_user(conn, ctx, required=False, roles=None):
    tok = ctx.token()
    user = None
    if tok:
        user = db.one(conn, "SELECT u.user_id, u.email, u.role FROM sessions s JOIN users u USING(user_id) WHERE s.token = ?", (tok,))
    if required and not user:
        raise ApiError(401, "Требуется вход в систему")
    if user and roles and user["role"] not in roles:
        raise ApiError(403, "Недостаточно прав для этого действия")
    return user


def hash_password(password, salt=None):
    salt = salt or os.urandom(8).hex()
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 60_000).hex()
    return f"{salt}${h}"


def verify_password(password, stored):
    salt, _ = stored.split("$", 1)
    return hash_password(password, salt) == stored
