# -*- coding: utf-8 -*-
"""
Хранилище пользователей и сессий (data/users.db): регистрация, вход, токены доступа.

Решения:
- пароли: PBKDF2-HMAC-SHA256 (120k итераций), индивидуальная соль; исходные пароли не хранятся;
- токен доступа: случайный (secrets.token_urlsafe), в БД лежит только его sha256-хэш,
  срок действия 24 часа — токен можно отозвать (удалить строку);
- изоляция пользователей: user_id из токена, клиентский id не принимается нигде.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time

from .. import config

USERS_DB = os.path.join(config.DATA, "users.db")
PBKDF2_ITERS = 120_000
TOKEN_TTL = 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT UNIQUE NOT NULL,
    salt       TEXT NOT NULL,
    pw_hash    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


def _con():
    con = sqlite3.connect(USERS_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _hash_pw(password, salt_hex):
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt_hex), PBKDF2_ITERS)
    return dk.hex()


def register(email, password):
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        return {"error": "некорректный email"}
    if not password or len(password) < 8:
        return {"error": "пароль должен быть не короче 8 символов"}
    salt = secrets.token_hex(16)
    con = _con()
    try:
        cur = con.execute(
            "INSERT INTO users (email, salt, pw_hash, created_at) VALUES (?,?,?,?)",
            (email, salt, _hash_pw(password, salt), time.strftime("%Y-%m-%dT%H:%M:%S")))
        user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        con.close()
        return {"error": "пользователь с таким email уже существует"}
    con.commit()
    con.close()
    return _issue(user_id, email)


def login(email, password):
    email = (email or "").strip().lower()
    con = _con()
    row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    con.close()
    if not row or not hmac.compare_digest(row["pw_hash"], _hash_pw(password or "", row["salt"])):
        return {"error": "неверный email или пароль"}
    return _issue(row["id"], row["email"])


def _issue(user_id, email):
    token = secrets.token_urlsafe(32)
    con = _con()
    con.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), user_id,
                 time.strftime("%Y-%m-%dT%H:%M:%S"), time.time() + TOKEN_TTL))
    con.commit()
    con.close()
    return {"token": token, "user": {"id": user_id, "email": email}}


def user_by_token(token):
    """Проверка токена -> {'id', 'email'} или None. Токен принимается только из заголовка."""
    if not token:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    con = _con()
    row = con.execute(
        """SELECT u.id, u.email, s.expires_at FROM sessions s
           JOIN users u ON u.id = s.user_id WHERE s.token_hash=?""", (th,)).fetchone()
    if row and row["expires_at"] >= time.time():
        con.close()
        return {"id": row["id"], "email": row["email"]}
    if row:  # просроченный — удаляем
        con.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
        con.commit()
    con.close()
    return None


def logout(token):
    if not token:
        return
    con = _con()
    con.execute("DELETE FROM sessions WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),))
    con.commit()
    con.close()
