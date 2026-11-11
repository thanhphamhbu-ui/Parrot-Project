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
ON_RENDER = bool(os.environ.get("RENDER"))
# Two shared passwords, set in Render → Environment. Local runs get simple defaults.
# .strip(): a space or line break pasted into Render's Environment form would
# otherwise make every login fail with "wrong password".
PM_PASSWORD = os.environ.get("PM_PASSWORD", "").strip() or ("" if ON_RENDER else "pm123")
TEAM_PASSWORD = os.environ.get("TEAM_PASSWORD", "").strip() or ("" if ON_RENDER else "team123")
# SESSION_SECRET is optional. Without it, the key is derived from the two
# passwords, so every worker and every restart agrees on it.
SECRET = os.environ.get("SESSION_SECRET", "").strip()
if not SECRET:
    if PM_PASSWORD and TEAM_PASSWORD:
        SECRET = hashlib.sha256(f"parrot-session:{PM_PASSWORD}:{TEAM_PASSWORD}".encode()).hexdigest()
    else:
        SECRET = secrets.token_hex(32)
if ON_RENDER and not IS_PG:
    print("CẢNH BÁO: chưa đặt DATABASE_URL. Dữ liệu đang lưu tạm trong SQLite và sẽ mất sau mỗi lần deploy.", flush=True)

COOKIE = "pw_session"
MAX_AGE = 30 * 24 * 3600
TASK_RE = re.compile(r"^[A-Z][0-9]{2}B?$")
GATE_RE = re.compile(r"^C[0-3]B?$")
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
]


# ---------- database ----------
def get_db():
    if "db" not in g:
        if IS_PG:
            import psycopg
            from psycopg.rows import dict_row

            g.db = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True, connect_timeout=10)
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
    # Retry: the free Postgres can take a few seconds to accept connections, and
    # two gunicorn workers may create the tables at the same moment.
    for attempt in range(6):
        try:
            with app.app_context():
                for stmt in SCHEMA:
                    q(stmt)
            return
        except Exception as e:  # noqa: BLE001
            print(f"init_db attempt {attempt + 1} failed: {e}", flush=True)
            time.sleep(2 + attempt * 2)
    raise RuntimeError("Không kết nối được database. Kiểm tra parrot-workflow-db trên Render.")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ---------- sessions ----------
def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def role_key(role):
    # Changing a password on Render signs everyone with that role out.
    pw = PM_PASSWORD if role == "pm" else TEAM_PASSWORD
    return f"{SECRET}:{role}:{pw}".encode()


def make_token(name, role):
    body = b64(json.dumps({"n": name, "r": role, "x": int(time.time()) + MAX_AGE}).encode())
    sig = b64(hmac.new(role_key(role), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token):
    try:
        body, sig = token.split(".", 1)
        data = json.loads(unb64(body))
        if data.get("r") not in ("pm", "member"):
            return None
        good = b64(hmac.new(role_key(data["r"]), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good) or data["x"] <= time.time():
            return None
        return data
    except Exception:
        return None


def current_user():
    data = read_token(request.cookies.get(COOKIE, ""))
    if not data:
        return None
    name = str(data.get("n") or "").strip()[:40]
    # "email" is the stable id stored with each change; here it is the display name.
    return {"email": name, "name": name, "picture": "", "role": data["r"]}


def err(code, msg, status):
    return jsonify({"error": code, "message": msg}), status


@app.before_request
def guard():
    # Writes must come from the page's own fetch() calls (simple CSRF guard).
    if request.method not in ("GET", "HEAD") and request.headers.get("X-Requested-With") != "fetch":
        return err("bad_request", "Thiếu header của ứng dụng.", 400)


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
FAILS = {}  # ip -> recent failed-login timestamps (per worker, in memory)


def too_many_fails(ip):
    recent = [t for t in FAILS.get(ip, []) if t > time.time() - 600]
    FAILS[ip] = recent
    # Generous limit: a whole office may share one public IP.
    return len(recent) >= 20


@app.get("/api/config")
def config():
    return jsonify({
        "configured": bool(PM_PASSWORD and TEAM_PASSWORD),
        "samePasswords": bool(PM_PASSWORD) and PM_PASSWORD == TEAM_PASSWORD,
    })


@app.get("/api/health")
def health():
    # Open this URL in a browser to see what is wrong without reading the logs.
    out = {"app": "ok", "passwordsSet": bool(PM_PASSWORD and TEAM_PASSWORD),
           "database": "postgres" if IS_PG else ("sqlite TẠM – thiếu DATABASE_URL, dữ liệu mất khi deploy" if ON_RENDER else "sqlite")}
    try:
        q("SELECT 1 AS ok")
        out["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["db"] = "error: " + type(e).__name__
    return jsonify(out), (200 if out["db"] == "ok" and out["passwordsSet"] else 503)


@app.post("/api/auth/login")
def login():
    if not (PM_PASSWORD and TEAM_PASSWORD):
        return err("not_configured", "Máy chủ chưa đặt PM_PASSWORD và TEAM_PASSWORD.", 500)
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]).strip()
    if too_many_fails(ip):
        return err("rate_limited", "Sai mật khẩu quá nhiều lần. Đợi 10 phút rồi thử lại.", 429)
    data = request.get_json(silent=True) or {}
    name = " ".join(str(data.get("name") or "").split())[:40]
    pw = str(data.get("password") or "").strip()
    if not name:
        return err("bad_request", "Nhập tên của bạn để team biết ai cập nhật việc.", 400)
    if hmac.compare_digest(pw.encode(), PM_PASSWORD.encode()):
        role = "pm"
    elif hmac.compare_digest(pw.encode(), TEAM_PASSWORD.encode()):
        role = "member"
    else:
        FAILS.setdefault(ip, []).append(time.time())
        return err("bad_password", "Mật khẩu không đúng.", 401)
    resp = jsonify({"ok": True})
    resp.set_cookie(COOKIE, make_token(name, role), max_age=MAX_AGE, httponly=True, secure=ON_RENDER, samesite="Lax")
    return resp


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


init_db()

if __name__ == "__main__":
    # Local test only. On Render the Start Command is: gunicorn app:app
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
