"""王艳霞中医门诊 · 问诊记录系统。"""

import mimetypes
import os
import secrets
import threading
from datetime import date, timedelta

from flask import Flask

from . import db
from .security import csrf_protect, csrf_token, set_security_headers
from .utils import register_template_filters

__version__ = "1.3.2"

# 界面文件的类型。Windows 上 Python 会从注册表读取文件类型，不少电脑把 .js 登记成
# text/plain；配合 nosniff 响应头，浏览器会拒绝执行界面脚本（点选项、开方等都没反应）。
STATIC_TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json",
}


def create_app(test_config=None, instance_path=None):
    """test_config 为空时读取 instance/config.py；instance_path 指定数据目录（桌面版使用）。"""
    for extension, mime in STATIC_TYPES.items():
        mimetypes.add_type(mime, extension)
    app = Flask(__name__, instance_relative_config=True, instance_path=instance_path)
    app.config.from_mapping(
        DATABASE=os.path.join(app.instance_path, "wenzhen.sqlite3"),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_ENABLED=True,
        AUTO_BACKUP=True,       # 每天首次使用时在后台自动备份
        BACKUP_KEEP_DAYS=30,    # 每日备份全部保留的天数；更早的每月保留一份，长期保存
        MAX_CONTENT_LENGTH=1024 * 1024 * 1024,  # 上传备份文件恢复时的大小上限
        DESKTOP=False,      # 桌面版：显示“备份到…”“打开数据文件夹”等本机功能
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

    from . import auth, backup_views, formulas, main, patients, therapy, visits
    for blueprint in (auth.bp, main.bp, patients.bp, visits.bp, formulas.bp, therapy.bp,
                      backup_views.bp):
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
    """每天第一次有人使用时，在后台线程做当天的自动备份，不耽误打开页面。"""
    from .backup import daily_backup

    lock = threading.Lock()
    state = {"day": None}

    def run():
        with app.app_context():
            try:
                daily_backup(app.config["BACKUP_KEEP_DAYS"])
            except Exception:  # 备份失败不能影响看诊，记录日志即可
                app.logger.exception("自动备份失败")

    @app.before_request
    def start_daily_backup():
        today = date.today().isoformat()
        if state["day"] == today:
            return
        with lock:
            if state["day"] == today:
                return
            state["day"] = today
        threading.Thread(target=run, name="wenzhen-daily-backup", daemon=True).start()
