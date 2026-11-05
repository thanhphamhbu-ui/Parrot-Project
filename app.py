"""Parrot Workflow: Flask backend for the Kanban workflow page.

Runs on Render with Postgres (DATABASE_URL) and falls back to a local SQLite
file when DATABASE_URL is not set, so the app can be tried on a laptop.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
SQLITE_PATH = os.environ.get("SQLITE_PATH", os.path.join(BASE_DIR, "local.db"))
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
PM_EMAILS = {e.strip().lower() for e in os.environ.get("PM_EMAILS", "").split(",") if e.strip()}
ON_RENDER = bool(os.environ.get("RENDER"))
# Local-only shortcut that skips Google; never active on Render.
DEV_LOGIN = os.environ.get("DEV_LOGIN") == "1" and not ON_RENDER
SECRET = os.environ.get("SESSION_SECRET") or ""
if not SECRET:
    if ON_RENDER:
        raise RuntimeError("SESSION_SECRET is required on Render")
    SECRET = secrets.token_hex(32)

COOKIE = "pw_session"
MAX_AGE = 30 * 24 * 3600
TASK_RE = re.compile(r"^[A-Z][0-9]{2}B?$")
GATE_RE = re.compile(r"^C[0-3]B?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^https?://\S+$", re.I)
LANES = {"F", "M", "P", "S", "D"}
GATE_DEC = {"", "go", "adj", "no"}
# kind -> (required current status, next status, PM only)
TRANSITIONS = {
    "start": ("todo", "doing", False),
    "submit": ("doing", "review", False),
    "todo": ("doing", "todo", False),
    "withdraw": ("review", "doing", False),
    "approve": ("review", "done", True),
    "reject": ("review", "doing", True),
    "reopen": ("done", "doing", True),
}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS status (task_id TEXT PRIMARY KEY, s TEXT NOT NULL, link TEXT, note TEXT, by_email TEXT, by_name TEXT, at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS approvals (task_id TEXT PRIMARY KEY, ok INTEGER NOT NULL, by_email TEXT, by_name TEXT, at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gates (gate_id TEXT PRIMARY KEY, d TEXT NOT NULL, by_email TEXT, by_name TEXT, at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS owners (lane TEXT PRIMARY KEY, name TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS log (id TEXT PRIMARY KEY, task TEXT NOT NULL, act TEXT NOT NULL, decision TEXT, note TEXT, link TEXT, by_email TEXT, by_name TEXT, at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS members (email TEXT PRIMARY KEY, added_by TEXT, at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT, picture TEXT, last_login TEXT)",
]


# ---------- database ----------
def get_db():
    if "db" not in g:
        if IS_PG:
            import psycopg
            from psycopg.rows import dict_row

            g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
        else:
            conn = sqlite3.connect(SQLITE_PATH, isolation_level=None)
            conn.row_factory = sqlite3.Row
            g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def q(sql, params=()):
    if IS_PG:
        sql = sql.replace("?", "%s")
    cur = get_db().execute(sql, params)
    try:
        rows = cur.fetchall()
    except Exception:
        rows = []
    return [dict(r) for r in rows]


def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def init_db():
    with app.app_context():
        for stmt in SCHEMA:
            q(stmt)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ---------- sessions ----------
def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(email):
    body = b64(json.dumps({"e": email, "x": int(time.time()) + MAX_AGE}).encode())
    sig = b64(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token):
    try:
        body, sig = token.split(".", 1)
        good = b64(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(unb64(body))
        return data["e"] if data["x"] > time.time() else None
    except Exception:
        return None


def is_allowed(email):
    return email in PM_EMAILS or one("SELECT email FROM members WHERE email = ?", (email,)) is not None


def current_user():
    email = read_token(request.cookies.get(COOKIE, ""))
    if not email or not is_allowed(email):
        return None
    u = one("SELECT email, name, picture FROM users WHERE email = ?", (email,)) or {"email": email, "name": "", "picture": ""}
    u["role"] = "pm" if email in PM_EMAILS else "member"
    u["name"] = u.get("name") or email.split("@")[0]
    return u


def err(code, msg, status):
    return jsonify({"error": code, "message": msg}), status


@app.before_request
def guard():
    # Writes must come from the page's own fetch() calls (simple CSRF guard).
    if request.method not in ("GET", "HEAD") and request.headers.get("X-Requested-With") != "fetch":
        return err("bad_request", "Thiếu header của ứng dụng.", 400)


def login_response(email, name, picture):
    q(
        "INSERT INTO users (email, name, picture, last_login) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (email) DO UPDATE SET name = excluded.name, picture = excluded.picture, last_login = excluded.last_login",
        (email, name, picture, now_iso()),
    )
    resp = jsonify({"ok": True})
    resp.set_cookie(COOKIE, make_token(email), max_age=MAX_AGE, httponly=True, secure=ON_RENDER, samesite="Lax")
    return resp


# ---------- pages ----------
@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/")
def index():
    resp = send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/logo.jpg")
def logo():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "logo.jpg", max_age=86400)


# ---------- auth ----------
@app.get("/api/config")
def config():
    return jsonify({"googleClientId": GOOGLE_CLIENT_ID, "devLogin": DEV_LOGIN, "pmConfigured": bool(PM_EMAILS)})


@app.post("/api/auth/google")
def auth_google():
    if not GOOGLE_CLIENT_ID:
        return err("not_configured", "Máy chủ chưa có GOOGLE_CLIENT_ID.", 500)
    cred = (request.get_json(silent=True) or {}).get("credential", "")
    try:
        from google.auth.transport import requests as grequests
        from google.oauth2 import id_token

        info = id_token.verify_oauth2_token(cred, grequests.Request(), GOOGLE_CLIENT_ID)
    except Exception:
        return err("bad_token", "Không xác minh được tài khoản Google, thử đăng nhập lại.", 401)
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified"):
        return err("bad_token", "Tài khoản Google này chưa xác minh email.", 401)
    if not is_allowed(email):
        return jsonify({"error": "not_allowed", "email": email, "message": "Email này chưa được cấp quyền."}), 403
    return login_response(email, info.get("name") or "", info.get("picture") or "")


@app.post("/api/auth/dev")
def auth_dev():
    if not DEV_LOGIN:
        return err("not_found", "Không có.", 404)
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return err("bad_request", "Email không hợp lệ.", 400)
    if not is_allowed(email):
        return jsonify({"error": "not_allowed", "email": email, "message": "Email này chưa được cấp quyền."}), 403
    return login_response(email, email.split("@")[0], "")


@app.post("/api/auth/logout")
def logout():
    resp = jsonify({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
def me():
    u = current_user()
    if not u:
        return err("unauthorized", "Chưa đăng nhập.", 401)
    return jsonify(u)


# ---------- state ----------
def effective_status(task_id):
    s = one("SELECT s, at FROM status WHERE task_id = ?", (task_id,))
    a = one("SELECT ok, at FROM approvals WHERE task_id = ?", (task_id,))
    if a and a["ok"] and (not s or a["at"] >= s["at"]):
        return "done"
    return s["s"] if s else "todo"


@app.get("/api/state")
def state():
    u = current_user()
    if not u:
        return err("unauthorized", "Chưa đăng nhập.", 401)
    out = {
        "status": {r["task_id"]: {"s": r["s"], "link": r["link"] or "", "note": r["note"] or "", "by": r["by_email"], "byName": r["by_name"] or "", "at": r["at"]} for r in q("SELECT * FROM status")},
        "approvals": {r["task_id"]: {"ok": bool(r["ok"]), "by": r["by_email"], "byName": r["by_name"] or "", "at": r["at"]} for r in q("SELECT * FROM approvals")},
        "gates": {r["gate_id"]: r["d"] for r in q("SELECT gate_id, d FROM gates")},
        "owners": {r["lane"]: r["name"] for r in q("SELECT lane, name FROM owners")},
        "log": [
            {"task": r["task"], "act": r["act"], "dec": r["decision"] or "", "note": r["note"] or "", "link": r["link"] or "", "by": r["by_email"], "byName": r["by_name"] or "", "at": r["at"]}
            for r in q("SELECT * FROM log ORDER BY at DESC LIMIT 800")
        ],
    }
    if u["role"] == "pm":
        out["members"] = [r["email"] for r in q("SELECT email FROM members ORDER BY email")]
        out["pmEmails"] = sorted(PM_EMAILS)
    return jsonify(out)


def add_log(task, act, user, note="", link="", dec=""):
    q(
        "INSERT INTO log (id, task, act, decision, note, link, by_email, by_name, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, task, act, dec, note, link, user["email"], user["name"], now_iso()),
    )


@app.post("/api/tasks/<task_id>/action")
def task_action(task_id):
    u = current_user()
    if not u:
        return err("unauthorized", "Chưa đăng nhập.", 401)
    if not TASK_RE.match(task_id):
        return err("bad_request", "Mã việc không hợp lệ.", 400)
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    if kind not in TRANSITIONS:
        return err("bad_request", "Thao tác không hợp lệ.", 400)
    need, nxt, pm_only = TRANSITIONS[kind]
    if pm_only and u["role"] != "pm":
        return err("forbidden", "Chỉ Project Manager được làm thao tác này.", 403)
    cur = effective_status(task_id)
    if cur != need:
        return jsonify({"error": "stale", "message": "Việc này vừa được người khác cập nhật. Trang đã tải lại trạng thái mới.", "status": cur}), 409
    note = str(data.get("note") or "").strip()[:2000]
    prev = one("SELECT link FROM status WHERE task_id = ?", (task_id,))
    link = (prev or {}).get("link") or ""
    if kind == "submit":
        link = str(data.get("link") or "").strip()
        if len(link) > 1000 or not URL_RE.match(link):
            return err("bad_request", "Dán link đầu ra (bắt đầu bằng http:// hoặc https://) trước khi nộp.", 400)
    if kind in ("reject", "reopen") and not note:
        return err("bad_request", "Ghi chú là bắt buộc để người phụ trách biết cần sửa gì.", 400)
    at = now_iso()
    if kind == "approve":
        q(
            "INSERT INTO approvals (task_id, ok, by_email, by_name, at) VALUES (?, 1, ?, ?, ?) "
            "ON CONFLICT (task_id) DO UPDATE SET ok = 1, by_email = excluded.by_email, by_name = excluded.by_name, at = excluded.at",
            (task_id, u["email"], u["name"], at),
        )
    else:
        if kind in ("reject", "reopen"):
            q(
                "INSERT INTO approvals (task_id, ok, by_email, by_name, at) VALUES (?, 0, ?, ?, ?) "
                "ON CONFLICT (task_id) DO UPDATE SET ok = 0, by_email = excluded.by_email, by_name = excluded.by_name, at = excluded.at",
                (task_id, u["email"], u["name"], at),
            )
        q(
            "INSERT INTO status (task_id, s, link, note, by_email, by_name, at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (task_id) DO UPDATE SET s = excluded.s, link = excluded.link, note = excluded.note, by_email = excluded.by_email, by_name = excluded.by_name, at = excluded.at",
            (task_id, nxt, link, note, u["email"], u["name"], at),
        )
    add_log(task_id, kind, u, note=note, link=link if kind == "submit" else "")
    return jsonify({"ok": True, "status": nxt})


@app.put("/api/gates/<gate_id>")
def set_gate(gate_id):
    u = current_user()
    if not u:
        return err("unauthorized", "Chưa đăng nhập.", 401)
    if u["role"] != "pm":
        return err("forbidden", "Chỉ Project Manager chốt quyết định ở cổng.", 403)
    d = (request.get_json(silent=True) or {}).get("d", "")
    if not GATE_RE.match(gate_id) or d not in GATE_DEC:
        return err("bad_request", "Quyết định không hợp lệ.", 400)
    q(
        "INSERT INTO gates (gate_id, d, by_email, by_name, at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (gate_id) DO UPDATE SET d = excluded.d, by_email = excluded.by_email, by_name = excluded.by_name, at = excluded.at",
        (gate_id, d, u["email"], u["name"], now_iso()),
    )
    add_log(gate_id, "gate", u, dec=d)
    return jsonify({"ok": True})


@app.put("/api/owners")
def set_owners():
    u = current_user()
    if not u:
        return err("unauthorized", "Chưa đăng nhập.", 401)
    if u["role"] != "pm":
        return err("forbidden", "Chỉ Project Manager sửa được đội ngũ.", 403)
    data = request.get_json(silent=True) or {}
    for lane, name in data.items():
        if lane in LANES and isinstance(name, str):
            q(
                "INSERT INTO owners (lane, name) VALUES (?, ?) ON CONFLICT (lane) DO UPDATE SET name = excluded.name",
                (lane, name.strip()[:60]),
            )
    return jsonify({"ok": True})


@app.post("/api/members")
def add_member():
    u = current_user()
    if not u or u["role"] != "pm":
        return err("forbidden", "Chỉ Project Manager thêm được thành viên.", 403)
    email = str((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 200:
        return err("bad_request", "Email không hợp lệ.", 400)
    q("INSERT INTO members (email, added_by, at) VALUES (?, ?, ?) ON CONFLICT (email) DO NOTHING", (email, u["email"], now_iso()))
    return jsonify({"ok": True})


@app.delete("/api/members/<path:email>")
def remove_member(email):
    u = current_user()
    if not u or u["role"] != "pm":
        return err("forbidden", "Chỉ Project Manager xóa được thành viên.", 403)
    q("DELETE FROM members WHERE email = ?", (email.strip().lower(),))
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
