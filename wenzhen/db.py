"""SQLite 数据库连接、初始化与系统设置。备份相关功能见 backup.py。"""

import sqlite3

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

# 系统管理员账户：完成首次设置时与医师账户一并建立，不可删除
ADMIN_USERNAME = "admin"
ADMIN_DISPLAY_NAME = "系统管理员"
ADMIN_DEFAULT_PASSWORD = "888888"


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
    ensure_admin(conn)
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


def ensure_admin(conn):
    """已有账户（完成首次设置）后，确保系统管理员账户存在，初始密码 888888。返回其 id。"""
    row = conn.execute("SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,)).fetchone()
    if row:
        return row["id"]
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None:
        return None  # 首次使用，先进入设置页建立医师账户
    cur = conn.execute(
        "INSERT INTO users (username, display_name, password_hash) VALUES (?, ?, ?)",
        (ADMIN_USERNAME, ADMIN_DISPLAY_NAME, generate_password_hash(ADMIN_DEFAULT_PASSWORD)),
    )
    return cur.lastrowid


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


@click.command("set-password")
@click.argument("username")
@click.option("--password", prompt="新密码", hide_input=True, confirmation_prompt="再次输入")
@with_appcontext
def set_password_command(username, password):
    """创建账户，或重置已有账户的密码（忘记密码时使用）。"""
    try:
        message = set_user_password(username, password)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="password") from exc
    click.echo(message)


def set_user_password(username, password):
    """重置账户密码；账户不存在则新建。返回说明文字。"""
    if len(password) < 6:
        raise ValueError("密码至少 6 位。")
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
    return message


@click.command("backup")
@click.option("--output", type=click.Path(dir_okay=False),
              help="导出完整数据包到指定文件（.zip）；不指定则保存到备份文件夹")
@with_appcontext
def backup_command(output):
    """立即备份（完整数据包，含数据库、JSON 和 CSV）。"""
    from .backup import export_package, manual_backup

    if output:
        export_package(output)
        target = output
    else:
        target = manual_backup()
    click.echo(f"已备份到 {target}")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(set_password_command)
    app.cli.add_command(backup_command)
