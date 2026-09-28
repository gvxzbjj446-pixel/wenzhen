"""登录、首次设置与退出。除登录页外，所有页面都需要登录。"""

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import ADMIN_USERNAME, ensure_admin, get_db, set_setting
from .utils import safe_next

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"static", "auth.login", "auth.setup"}
MIN_PASSWORD = 6


def has_users():
    return get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def validate_new_account(username, password, password2):
    errors = []
    if not username:
        errors.append("请填写用户名。")
    elif len(username) > 30:
        errors.append("用户名不超过 30 个字符。")
    elif get_db().execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        errors.append("该用户名已存在。")
    errors.extend(validate_password(password, password2))
    return errors


def validate_password(password, password2):
    if len(password) < MIN_PASSWORD:
        return [f"密码至少 {MIN_PASSWORD} 位。"]
    if password != password2:
        return ["两次输入的密码不一致。"]
    return []


def create_user(username, display_name, password):
    cur = get_db().execute(
        "INSERT INTO users (username, display_name, password_hash) VALUES (?, ?, ?)",
        (username, display_name or username, generate_password_hash(password)),
    )
    return cur.lastrowid


def admin_user():
    return get_db().execute(
        "SELECT id, username, display_name FROM users WHERE username = ?", (ADMIN_USERNAME,)
    ).fetchone()


def log_in(user_id):
    session.clear()
    session["user_id"] = user_id
    session.permanent = True


@bp.before_app_request
def load_user_and_require_login():
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = get_db().execute(
            "SELECT id, username, display_name FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if request.endpoint in PUBLIC_ENDPOINTS or g.user is not None:
        return None
    if not has_users():
        return redirect(url_for("auth.setup"))
    target = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=target))


@bp.route("/setup", methods=("GET", "POST"))
def setup():
    """首次使用：创建医师账户并填写诊所名称。"""
    if has_users():
        return redirect(url_for("auth.login"))
    errors = []
    if request.method == "POST":
        form = request.form
        username = form.get("username", "").strip()
        display_name = form.get("display_name", "").strip()
        password = form.get("password", "")
        errors = validate_new_account(username, password, form.get("password2", ""))
        if not errors:
            user_id = create_user(username, display_name, password)
            ensure_admin(get_db())
            for key in ("clinic_name", "doctor_name"):
                value = form.get(key, "").strip()
                if value:
                    set_setting(key, value)
            get_db().commit()
            log_in(user_id)
            flash("初始化完成，欢迎使用！", "success")
            return redirect(url_for("main.index"))
    return render_template("auth/setup.html", errors=errors)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if not has_users():
        return redirect(url_for("auth.setup"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            log_in(user["id"])
            return redirect(safe_next(request.form.get("next")) or url_for("main.index"))
        error = "用户名或密码错误。"
    return render_template("auth/login.html", error=error, next=request.values.get("next", ""))


@bp.route("/logout", methods=("POST",))
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
