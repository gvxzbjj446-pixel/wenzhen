"""SQLite 数据库连接、初始化、系统设置与备份。"""

import os
import sqlite3
from datetime import date

import click
from flask import current_app, g
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

from .fields import VISIT_TEXT_FIELDS
from .herbs import SEED_FORMULAS

SCHEMA_VERSION = 1

DEFAULT_SETTINGS = {
    "clinic_name": "王艳霞中医门诊",
    "doctor_name": "王艳霞",
    "clinic_address": "",
    "clinic_phone": "",
    "default_usage": "水煎服，日一剂，早晚分服",
    "default_dose_count": "7",
}


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    """建表并写入默认设置。可重复执行，不会影响已有数据。"""
    conn = get_db()
    with current_app.open_resource("schema.sql") as f:
        conn.executescript(f.read().decode("utf-8"))
    _add_missing_visit_columns(conn)
    conn.executemany(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        DEFAULT_SETTINGS.items(),
    )
    # 示例方剂只写入一次；医师删掉后不会再出现
    if get_setting("formulas_seeded") != "1":
        _seed_formulas(conn)
        set_setting("formulas_seeded", "1")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _add_missing_visit_columns(conn):
    """fields.py 新增问诊项目后，自动给已有数据库补上对应的列。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(visits)")}
    for key in VISIT_TEXT_FIELDS:
        if key not in existing:
            conn.execute(f"ALTER TABLE visits ADD COLUMN {key} TEXT NOT NULL DEFAULT ''")


def _seed_formulas(conn):
    for formula in SEED_FORMULAS:
        exists = conn.execute(
            "SELECT 1 FROM formulas WHERE name = ?", (formula["name"],)
        ).fetchone()
        if exists:
            continue
        cur = conn.execute(
            "INSERT INTO formulas (name, source, indication, usage, notes)"
            " VALUES (?, ?, ?, ?, ?)",
            (formula["name"], formula["source"], formula["indication"],
             formula["usage"], formula["notes"]),
        )
        conn.executemany(
            "INSERT INTO formula_items (formula_id, position, herb, dose, unit)"
            " VALUES (?, ?, ?, ?, ?)",
            [(cur.lastrowid, i, herb, dose, unit)
             for i, (herb, dose, unit) in enumerate(formula["items"])],
        )


def get_setting(key, default=""):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_settings():
    settings = dict(DEFAULT_SETTINGS)
    settings.update(
        (row["key"], row["value"])
        for row in get_db().execute("SELECT key, value FROM settings")
    )
    return settings


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def backup_to(path):
    """用 SQLite 在线备份接口复制数据库，运行中备份也是一致的。"""
    target = sqlite3.connect(path)
    try:
        get_db().backup(target)
    finally:
        target.close()


def backup_dir():
    return os.path.join(current_app.instance_path, "backups")


def auto_backup(keep=30):
    """每天保留一份备份，只保留最近 keep 份。返回新备份路径（今天已备份则返回 None）。"""
    folder = backup_dir()
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"wenzhen-{date.today():%Y%m%d}.sqlite3")
    if os.path.exists(target):
        return None
    backup_to(target)
    backups = sorted(
        name for name in os.listdir(folder)
        if name.startswith("wenzhen-") and name.endswith(".sqlite3")
    )
    for old in backups[:-keep]:
        os.remove(os.path.join(folder, old))
    return target


@click.command("set-password")
@click.argument("username")
@click.option("--password", prompt="新密码", hide_input=True, confirmation_prompt="再次输入")
@with_appcontext
def set_password_command(username, password):
    """创建账户，或重置已有账户的密码（忘记密码时使用）。"""
    if len(password) < 6:
        raise click.BadParameter("密码至少 6 位。", param_hint="password")
    conn = get_db()
    pw_hash = generate_password_hash(password)
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, row["id"]))
        message = f"已重置账户 {username} 的密码。"
    else:
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash) VALUES (?, ?, ?)",
            (username, username, pw_hash),
        )
        message = f"已创建账户 {username}。"
    conn.commit()
    click.echo(message)


@click.command("backup")
@with_appcontext
def backup_command():
    """立即备份数据库到 instance/backups/。"""
    folder = backup_dir()
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"wenzhen-manual-{date.today():%Y%m%d}.sqlite3")
    backup_to(target)
    click.echo(f"已备份到 {target}")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(set_password_command)
    app.cli.add_command(backup_command)
