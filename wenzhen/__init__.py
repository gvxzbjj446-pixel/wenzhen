"""王艳霞中医门诊 · 问诊记录系统。"""

import os
import secrets
import threading
from datetime import date, timedelta

from flask import Flask

from . import db
from .security import csrf_protect, csrf_token, set_security_headers
from .utils import register_template_filters


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.path.join(app.instance_path, "wenzhen.sqlite3"),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_ENABLED=True,
        AUTO_BACKUP=True,   # 每天首次访问时自动备份数据库
        BACKUP_KEEP=30,     # 自动备份保留份数
    )
    if test_config is None:
        app.config.from_pyfile("config.py", silent=True)
    else:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_secret_key(app.instance_path)

    db.init_app(app)
    app.before_request(csrf_protect)
    app.after_request(set_security_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
    register_template_filters(app)

    from . import auth, formulas, main, patients, visits
    for blueprint in (auth.bp, main.bp, patients.bp, visits.bp, formulas.bp):
        app.register_blueprint(blueprint)

    with app.app_context():
        db.init_db()

    if app.config["AUTO_BACKUP"]:
        _install_daily_backup(app)
    return app


def _load_secret_key(instance_path):
    """首次运行时生成会话密钥并保存，重启后登录状态不失效。"""
    path = os.path.join(instance_path, "secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def _install_daily_backup(app):
    lock = threading.Lock()
    state = {"day": None}

    @app.before_request
    def daily_backup():
        today = date.today().isoformat()
        if state["day"] == today:
            return
        with lock:
            if state["day"] == today:
                return
            try:
                db.auto_backup(app.config["BACKUP_KEEP"])
            except Exception:  # 备份失败不能影响看诊，记录日志即可
                app.logger.exception("自动备份失败")
            state["day"] = today
