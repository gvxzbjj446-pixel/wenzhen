"""数据备份：备份包的生成、校验与恢复；每日自动备份与长期保留；同步到外部文件夹。

备份包是一个 .zip 文件（文件名均为英文，任何解压软件都能正确显示）：

    manifest.json      备份信息：时间、软件版本、各类记录数、每个文件的 SHA-256 校验值
    wenzhen.sqlite3    完整数据库，用于在本软件中恢复
    README.txt         中文说明：文件用途、字段含义、如何恢复与迁移
    data.json          全部数据的结构化 JSON（完整备份包才有，用于迁移到其他系统）
    csv/*.csv          各数据表（完整备份包才有，Excel 可直接打开）

保留策略：每日自动备份最近 30 天全部保留，更早的每个月保留最后一份，长期保存；
手动备份和“恢复前自动保存”的备份不会自动删除。
"""

import csv
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import current_app

from .db import DEFAULT_SETTINGS, SCHEMA_VERSION, get_db, get_setting, init_db, set_setting
from .fields import VISIT_SECTIONS

log = logging.getLogger(__name__)

FORMAT = "wenzhen-backup"
FORMAT_VERSION = 1
DB_FILE = "wenzhen.sqlite3"
MIRROR_SUBDIR = "问诊记录备份"
OFFSITE_REMIND_DAYS = 7
KEEP_DAYS = 30

KIND_LABELS = {
    "auto": "每日自动",
    "manual": "手动备份",
    "restore": "恢复前自动保存",
    "export": "完整数据包",
}

# 文件名 → (类型, 是否带时间)。后三种是 1.1.0 版的备份文件，仍可列出和恢复。
_NAME_PATTERNS = (
    (re.compile(r"auto-(\d{8})\.zip"), "auto"),
    (re.compile(r"manual-(\d{8})-\d{6}(?:-\d+)?\.zip"), "manual"),
    (re.compile(r"before-restore-(\d{8})-\d{6}(?:-\d+)?\.zip"), "restore"),
    (re.compile(r"wenzhen-(\d{8})\.sqlite3"), "auto"),
    (re.compile(r"wenzhen-manual-(\d{8})\.sqlite3"), "manual"),
    (re.compile(r"wenzhen-before-restore-(\d{8})-\d{6}\.sqlite3"), "restore"),
)

# 与本机有关的设置：恢复备份时保留当前电脑上的值
MACHINE_SETTINGS = (
    "backup_mirror_dir", "backup_mirror_ok_at", "backup_mirror_error", "last_offsite_backup",
)

_REQUIRED_TABLES = {"patients", "visits", "prescription_items", "users"}

# 备份与恢复互斥（自动备份在后台线程进行）；可重入，恢复时要先生成一份备份
_lock = threading.RLock()


# ---------------------------------------------------------------- 表结构（CSV / JSON 共用）

PATIENT_COLUMNS = (
    ("id", "患者ID"), ("record_no", "病历号"), ("name", "姓名"), ("gender", "性别"),
    ("birth_date", "出生日期"), ("phone", "电话"), ("address", "住址"), ("occupation", "职业"),
    ("allergies", "过敏史"), ("past_history", "既往史"), ("family_history", "家族史"),
    ("notes", "备注"), ("created_at", "建档时间"), ("updated_at", "更新时间"),
)
VISIT_COLUMNS = (
    (("id", "就诊ID"), ("patient_id", "患者ID"), ("visit_date", "就诊日期"),
     ("visit_type", "初诊/复诊"))
    + tuple((f.key, f.label) for _, fields in VISIT_SECTIONS for f in fields)
    + (("formula_name", "方名"), ("dose_count", "剂数"), ("usage", "煎服法"),
       ("fee", "收费（元）"), ("next_visit_date", "预约复诊日期"),
       ("created_at", "记录时间"), ("updated_at", "修改时间"))
)
ITEM_COLUMNS = (
    ("visit_id", "就诊ID"), ("position", "序号"), ("herb", "药名"), ("dose", "剂量"),
    ("unit", "单位"), ("note", "脚注"),
)
FORMULA_COLUMNS = (
    ("id", "方剂ID"), ("name", "方名"), ("source", "出处"), ("indication", "功用主治"),
    ("usage", "用法"), ("notes", "备注"), ("created_at", "创建时间"), ("updated_at", "更新时间"),
)
FORMULA_ITEM_COLUMNS = (
    ("formula_id", "方剂ID"), ("position", "序号"), ("herb", "药名"), ("dose", "剂量"),
    ("unit", "单位"), ("note", "脚注"),
)
_PRESCRIPTION_KEYS = ("formula_name", "dose_count", "usage")

CSV_TABLES = (
    ("csv/patients.csv", "患者档案", PATIENT_COLUMNS),
    ("csv/visits.csv", "就诊记录（门诊病历）", VISIT_COLUMNS),
    ("csv/prescription_items.csv", "处方明细，按“就诊ID”对应就诊记录", ITEM_COLUMNS),
    ("csv/formulas.csv", "方剂库", FORMULA_COLUMNS),
    ("csv/formula_items.csv", "方剂组成，按“方剂ID”对应方剂", FORMULA_ITEM_COLUMNS),
)


# ---------------------------------------------------------------- 小工具

def backup_dir():
    return os.path.join(current_app.instance_path, "backups")


def _now():
    return datetime.now().replace(microsecond=0)


def _parse_time(text):
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def classify(name):
    """按文件名识别备份：返回 (类型, 日期)；不是本系统的备份文件则返回 None。"""
    for pattern, kind in _NAME_PATTERNS:
        match = pattern.fullmatch(name)
        if match:
            try:
                return kind, datetime.strptime(match.group(1), "%Y%m%d").date()
            except ValueError:
                return None
    return None


def backup_path(name):
    """备份文件夹中某个备份的完整路径；名称不合法或文件不存在时返回 None。"""
    if not name or os.path.basename(name) != name or not classify(name):
        return None
    path = os.path.join(backup_dir(), name)
    return path if os.path.isfile(path) else None


def _unique_path(folder, stem, suffix=".zip"):
    """同一秒内多次备份时，在文件名后加序号，避免互相覆盖。"""
    path = os.path.join(folder, stem + suffix)
    number = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem}-{number}{suffix}")
        number += 1
    return path


def snapshot_database(target_path):
    """用 SQLite 在线备份接口复制数据库，运行中复制也是一致的。"""
    target = sqlite3.connect(target_path)
    try:
        get_db().backup(target)
    finally:
        target.close()


def _counts(conn):
    tables = ("patients", "visits", "prescription_items", "formulas")
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


# ---------------------------------------------------------------- 生成备份包

def create_package(path, kind="manual", full=True):
    """生成备份包并返回其 manifest。full=True 时附带 JSON 与 CSV（迁移到其他系统用）。

    先写入临时文件，完成后再改名，中途出错不会留下半个备份。
    """
    from . import __version__

    with _lock, tempfile.TemporaryDirectory(prefix="wenzhen-backup-") as tmp:
        db_copy = os.path.join(tmp, DB_FILE)
        snapshot_database(db_copy)
        created = _now()
        conn = sqlite3.connect(db_copy)
        conn.row_factory = sqlite3.Row
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("数据库自检未通过，未生成备份。")
            counts = _counts(conn)
            files = {DB_FILE: db_copy}
            if full:
                files.update(_write_exports(conn, tmp, created, __version__))
        finally:
            conn.close()

        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "kind": kind,
            "created_at": created.isoformat(),
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "clinic_name": get_setting("clinic_name"),
            "counts": counts,
        }
        files["README.txt"] = _write_readme(tmp, manifest, full)
        manifest["files"] = {
            name: {"size": os.path.getsize(p), "sha256": _sha256(p)} for name, p in files.items()
        }

        part = path + ".part"
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, source in files.items():
                zf.write(source, name)
        os.replace(part, path)
    return manifest


def _write_exports(conn, folder, created, version):
    os.makedirs(os.path.join(folder, "csv"), exist_ok=True)
    files = {}
    queries = {
        "csv/patients.csv": "SELECT * FROM patients ORDER BY id",
        "csv/visits.csv": "SELECT * FROM visits ORDER BY id",
        "csv/prescription_items.csv":
            "SELECT * FROM prescription_items ORDER BY visit_id, position, id",
        "csv/formulas.csv": "SELECT * FROM formulas ORDER BY id",
        "csv/formula_items.csv": "SELECT * FROM formula_items ORDER BY formula_id, position, id",
    }
    for name, _, columns in CSV_TABLES:
        path = os.path.join(folder, name)
        # UTF-8 带 BOM：Excel 直接打开中文不乱码。迁移用途，内容原样保留。
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([label for _, label in columns])
            for row in conn.execute(queries[name]):
                writer.writerow([_csv_value(row, key) for key, _ in columns])
        files[name] = path
    files["data.json"] = _write_json(conn, folder, created, version)
    return files


def _csv_value(row, key):
    if key == "record_no":
        return f"{row['id']:06d}"
    if key == "position":
        return row["position"] + 1
    value = row[key]
    return "" if value is None else value


def _pick(row, columns, skip=()):
    return {key: row[key] for key, _ in columns if key not in skip and key in row.keys()}


def _write_json(conn, folder, created, version):
    """全部数据的结构化 JSON：患者 → 就诊 → 处方；逐位患者写入，数据多时也不占用太多内存。"""
    path = os.path.join(folder, "data.json")
    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
    head = {
        "format": "wenzhen-data",
        "format_version": FORMAT_VERSION,
        "exported_at": created.isoformat(),
        "app_version": version,
        "schema_version": SCHEMA_VERSION,
        "clinic": {key: settings.get(key, "") for key in DEFAULT_SETTINGS},
        "field_labels": {
            "patient": dict(PATIENT_COLUMNS),
            "visit": dict(VISIT_COLUMNS),
            "prescription_item": dict(ITEM_COLUMNS),
            "formula": dict(FORMULA_COLUMNS),
        },
    }
    visit_skip = ("patient_id",) + _PRESCRIPTION_KEYS
    with open(path, "w", encoding="utf-8") as f:
        f.write("{\n")
        for key, value in head.items():
            f.write(f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},\n")

        f.write('"patients": [\n')
        for index, patient in enumerate(conn.execute("SELECT * FROM patients ORDER BY id").fetchall()):
            items = {}
            for item in conn.execute(
                "SELECT i.visit_id, i.herb, i.dose, i.unit, i.note FROM prescription_items i"
                " JOIN visits v ON v.id = i.visit_id WHERE v.patient_id = ?"
                " ORDER BY i.visit_id, i.position, i.id", (patient["id"],),
            ):
                items.setdefault(item["visit_id"], []).append(
                    {"herb": item["herb"], "dose": item["dose"], "unit": item["unit"], "note": item["note"]}
                )
            visits = []
            for visit in conn.execute(
                "SELECT * FROM visits WHERE patient_id = ? ORDER BY visit_date, id", (patient["id"],)
            ):
                record = _pick(visit, VISIT_COLUMNS, visit_skip)
                record["prescription"] = {
                    **{key: visit[key] for key in _PRESCRIPTION_KEYS},
                    "items": items.get(visit["id"], []),
                }
                visits.append(record)
            record = {"id": patient["id"], "record_no": f"{patient['id']:06d}"}
            record.update(_pick(patient, PATIENT_COLUMNS, skip=("id",)))
            record["visits"] = visits
            f.write(("" if index == 0 else ",\n") + json.dumps(record, ensure_ascii=False))

        f.write('\n],\n"formulas": [\n')
        formula_items = {}
        for item in conn.execute(
            "SELECT formula_id, herb, dose, unit, note FROM formula_items ORDER BY formula_id, position, id"
        ):
            formula_items.setdefault(item["formula_id"], []).append(
                {"herb": item["herb"], "dose": item["dose"], "unit": item["unit"], "note": item["note"]}
            )
        for index, formula in enumerate(conn.execute("SELECT * FROM formulas ORDER BY id")):
            record = _pick(formula, FORMULA_COLUMNS)
            record["items"] = formula_items.get(formula["id"], [])
            f.write(("" if index == 0 else ",\n") + json.dumps(record, ensure_ascii=False))
        f.write("\n]\n}\n")
    return path


def _write_readme(folder, manifest, full):
    counts = manifest["counts"]
    lines = [
        f"{manifest['clinic_name']} · 问诊记录系统 数据备份",
        "",
        f"备份时间：{manifest['created_at'].replace('T', ' ')}",
        f"软件版本：{manifest['app_version']}（数据格式 {manifest['schema_version']}）",
        f"记录数：患者 {counts['patients']} 人，就诊记录 {counts['visits']} 条，"
        f"处方药物 {counts['prescription_items']} 条，方剂 {counts['formulas']} 首",
        "",
        "【如何恢复】",
        "在本软件中选择「文件 → 从备份恢复…」（或「设置 → 数据备份与恢复」），选择本 .zip 文件即可。",
        "不需要解压。恢复前，软件会先把当前数据自动另存一份。",
        "",
        "【文件说明】",
        f"{'manifest.json':<28}备份信息与每个文件的 SHA-256 校验值（恢复时自动核对）",
        f"{DB_FILE:<28}完整数据库（SQLite 3 格式），包含全部数据和登录账户",
    ]
    if full:
        lines.append(f"{'data.json':<28}全部数据的结构化 JSON（UTF-8），便于导入其他系统")
        for name, title, _ in CSV_TABLES:
            lines.append(f"{name:<28}{title}")
        lines += [
            "",
            "【迁移到其他系统】",
            "· 用 Excel 查看：直接打开 csv 文件夹中的文件（UTF-8 编码，第一行为中文列名）。",
            "· 导入其他系统：各表用 ID 关联——就诊记录的“患者ID”对应患者档案的“患者ID”，",
            "  处方明细的“就诊ID”对应就诊记录的“就诊ID”，方剂组成的“方剂ID”对应方剂。",
            "· data.json 按“患者 → 就诊记录 → 处方”嵌套，field_labels 中列出每个字段的中文含义。",
            "· 日期格式为 年-月-日（如 2026-09-28），剂量单位默认为克（g）。",
            "· 为保护账户安全，登录账户和密码只保存在 wenzhen.sqlite3 中（密码为加密后的摘要）。",
        ]
    else:
        lines += [
            "",
            "这是自动备份，只包含数据库。需要 Excel 表格或迁移到其他系统时，",
            "请在软件中使用「导出完整数据包」。",
        ]
    lines += [
        "",
        "【注意】备份中包含患者的个人信息和病历，请妥善保管，不要发给无关人员。",
        "",
    ]
    path = os.path.join(folder, "README.txt")
    # 带 BOM 的 UTF-8，Windows 记事本能正确显示中文；换行用 CRLF
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(lines))
    return path


# ---------------------------------------------------------------- 读取、校验与恢复

def open_backup(path, workdir):
    """校验备份文件，返回 (可读取的数据库路径, manifest)。

    支持备份包（.zip）和 1.1.0 版的 .sqlite3 文件；备份包中的数据库会解压到 workdir。
    """
    if not zipfile.is_zipfile(path):
        return path, {}
    try:
        with zipfile.ZipFile(path) as zf:
            damaged = zf.testzip()  # 逐个核对 CRC，包内任何文件损坏都能发现
            if damaged:
                raise ValueError(f"备份包已损坏（{damaged}），无法使用。")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            if manifest.get("format") != FORMAT:
                raise ValueError("该文件不是本系统的数据备份。")
            if int(manifest.get("schema_version", 1)) > SCHEMA_VERSION:
                raise ValueError("该备份来自更新版本的软件，请先把本软件升级到最新版本再恢复。")
            db_path = os.path.join(workdir, DB_FILE)
            with zf.open(DB_FILE) as src, open(db_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except KeyError as exc:
        raise ValueError("备份包不完整，缺少必要的文件。") from exc
    except (zipfile.BadZipFile, ValueError, UnicodeDecodeError, OSError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("该", "备份")):
            raise
        raise ValueError("备份包已损坏，无法读取。") from exc
    expected = manifest.get("files", {}).get(DB_FILE, {}).get("sha256")
    if expected and _sha256(db_path) != expected:
        raise ValueError("备份包校验失败：文件内容与备份时不一致，可能已损坏。")
    return db_path, manifest


def check_database(path):
    """检查数据库文件完好且属于本系统，返回各类记录数。"""
    try:
        conn = _open_readonly(path)
    except sqlite3.Error as exc:
        raise ValueError("无法打开该文件。") from exc
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not _REQUIRED_TABLES <= tables:
            raise ValueError("该文件不是本系统的数据备份。")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("备份文件已损坏。")
        return {
            "patients": conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
            "visits": conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0],
        }
    except sqlite3.DatabaseError as exc:
        raise ValueError("该文件不是有效的数据库文件。") from exc
    finally:
        conn.close()


def inspect_backup(path):
    """检查备份文件，返回其中的记录数和备份时间；无效时抛出 ValueError。"""
    with tempfile.TemporaryDirectory(prefix="wenzhen-check-") as tmp:
        db_path, manifest = open_backup(path, tmp)
        counts = check_database(db_path)
    return {
        **counts,
        "created_at": manifest.get("created_at", ""),
        "app_version": manifest.get("app_version", ""),
        "clinic_name": manifest.get("clinic_name", ""),
    }


def restore_backup(path):
    """用备份替换当前全部数据。替换前自动把当前数据另存一份，返回该备份的路径。"""
    with _lock, tempfile.TemporaryDirectory(prefix="wenzhen-restore-") as tmp:
        db_path, _ = open_backup(path, tmp)
        check_database(db_path)
        machine = {key: get_setting(key) for key in MACHINE_SETTINGS}

        folder = backup_dir()
        os.makedirs(folder, exist_ok=True)
        safety = _unique_path(folder, f"before-restore-{_now():%Y%m%d-%H%M%S}")
        create_package(safety, kind="restore", full=False)

        source = _open_readonly(db_path)
        try:
            source.backup(get_db())
        finally:
            source.close()
        init_db()  # 旧版本的备份：补齐新增的列与设置
        for key, value in machine.items():
            set_setting(key, value)
        get_db().commit()
    log.info("已从 %s 恢复数据，原数据另存为 %s", path, safety)
    return safety


# ---------------------------------------------------------------- 自动备份与保留

def daily_backup(keep_days=KEEP_DAYS, refresh=False):
    """每日自动备份：当天第一次使用时建立；refresh=True（关闭程序时）用最新数据更新当天的备份。

    返回新写入的备份路径；无需备份时返回 None。
    """
    with _lock:
        folder = backup_dir()
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, f"auto-{date.today():%Y%m%d}.zip")
        if os.path.exists(target):
            database = current_app.config["DATABASE"]
            if not refresh or os.path.getmtime(database) <= os.path.getmtime(target):
                return None
        create_package(target, kind="auto", full=False)
        prune_backups(folder, date.today(), keep_days)
        mirror_backup(target, keep_days)
        return target


def manual_backup(keep_days=KEEP_DAYS):
    """立即备份（完整备份包），保存在备份文件夹并同步到外部文件夹。"""
    with _lock:
        folder = backup_dir()
        os.makedirs(folder, exist_ok=True)
        target = _unique_path(folder, f"manual-{_now():%Y%m%d-%H%M%S}")
        create_package(target, kind="manual", full=True)
        mirror_backup(target, keep_days)
        return target


def export_package(path):
    """导出完整数据包到指定位置（U 盘、其他电脑等）。"""
    manifest = create_package(path, kind="export", full=True)
    mark_offsite()
    return manifest


def prune_backups(folder, today, keep_days=KEEP_DAYS):
    """清理每日自动备份：最近 keep_days 天全部保留，更早的每个月只留最后一份。

    手动备份和恢复前的备份不删。返回删除的文件名。
    """
    cutoff = today - timedelta(days=keep_days)
    older = {}
    for name in os.listdir(folder):
        parsed = classify(name)
        if parsed and parsed[0] == "auto" and parsed[1] <= cutoff:
            day = parsed[1]
            older.setdefault((day.year, day.month), []).append((day, name))
    removed = []
    for backups in older.values():
        backups.sort()
        for _, name in backups[:-1]:
            os.remove(os.path.join(folder, name))
            removed.append(name)
    return sorted(removed)


# ---------------------------------------------------------------- 外部备份文件夹（U 盘、网盘同步文件夹等）

def mirror_root():
    return get_setting("backup_mirror_dir").strip()


def mirror_target_dir():
    root = mirror_root()
    return os.path.join(root, MIRROR_SUBDIR) if root else ""


def mirror_backup(path=None, keep_days=KEEP_DAYS):
    """把备份同步到外部备份文件夹：刚生成的备份 path，以及外部文件夹里还没有的其他备份
    （例如 U 盘没插时漏掉的）。未设置文件夹时跳过；失败时记录原因，不影响看诊。

    成功时返回外部备份所在的文件夹，失败或未设置时返回 None。
    """
    root = mirror_root()
    if not root:
        return None
    now = _now().isoformat(sep=" ")
    fresh = os.path.basename(path) if path else None
    try:
        if not os.path.isdir(root):
            raise OSError(f"找不到文件夹“{root}”，U 盘或移动硬盘是否已连接？")
        target_dir = os.path.join(root, MIRROR_SUBDIR)
        os.makedirs(target_dir, exist_ok=True)
        existing = set(os.listdir(target_dir))
        for item in list_backups():
            name = item["name"]
            if name in existing and name != fresh:
                continue
            dest = os.path.join(target_dir, name)
            shutil.copyfile(item["path"], dest + ".part")
            os.replace(dest + ".part", dest)
        prune_backups(target_dir, date.today(), keep_days)
    except OSError as exc:
        log.warning("同步备份到 %s 失败：%s", root, exc)
        set_setting("backup_mirror_error", f"{now}　{exc}")
        get_db().commit()
        return None
    set_setting("backup_mirror_ok_at", now)
    set_setting("backup_mirror_error", "")
    set_setting("last_offsite_backup", now)
    get_db().commit()
    return target_dir


def set_mirror_root(path):
    """设置外部备份文件夹（空字符串表示不同步），并立即把现有备份同步过去。

    返回外部备份所在的文件夹；同步失败时返回 None（原因见 status()["mirror_error"]）。
    """
    path = (path or "").strip()
    if path and not os.path.isdir(path):
        raise ValueError(f"找不到文件夹“{path}”。")
    set_setting("backup_mirror_dir", path)
    set_setting("backup_mirror_error", "")
    get_db().commit()
    if not path:
        return None
    if not list_backups():
        daily_backup()
    return mirror_backup()


def mark_offsite():
    """记录“已把数据备份到本机以外”（导出、另存到 U 盘等）。"""
    set_setting("last_offsite_backup", _now().isoformat(sep=" "))
    get_db().commit()


# ---------------------------------------------------------------- 列表与状态

def _read_manifest(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return json.loads(zf.read("manifest.json").decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


def list_backups(folder=None):
    """备份文件夹中的全部备份，最新的在前。"""
    folder = folder or backup_dir()
    if not os.path.isdir(folder):
        return []
    result = []
    for name in os.listdir(folder):
        parsed = classify(name)
        if not parsed:
            continue
        kind = parsed[0]
        path = os.path.join(folder, name)
        legacy = name.endswith(".sqlite3")
        manifest = {} if legacy else _read_manifest(path)
        created = _parse_time(manifest.get("created_at")) or datetime.fromtimestamp(os.path.getmtime(path))
        result.append({
            "name": name,
            "path": path,
            "kind": kind,
            "label": KIND_LABELS[kind] + ("（旧版）" if legacy else ""),
            "created": created,
            "size": os.path.getsize(path),
            "counts": manifest.get("counts"),
            "full": "data.json" in manifest.get("files", {}),
        })
    result.sort(key=lambda b: b["created"], reverse=True)
    return result


def status(backups=None):
    """备份概况：最近备份、占用空间、外部文件夹同步情况、是否需要提醒。"""
    backups = list_backups() if backups is None else backups
    last_offsite = _parse_time(get_setting("last_offsite_backup"))
    days_since_offsite = (_now() - last_offsite).days if last_offsite else None
    has_data = get_db().execute("SELECT 1 FROM patients LIMIT 1").fetchone() is not None
    return {
        "count": len(backups),
        "total_size": sum(b["size"] for b in backups),
        "latest": backups[0] if backups else None,
        "mirror_root": mirror_root(),
        "mirror_dir": mirror_target_dir(),
        "mirror_ok_at": get_setting("backup_mirror_ok_at"),
        "mirror_error": get_setting("backup_mirror_error"),
        "last_offsite": last_offsite,
        "days_since_offsite": days_since_offsite,
        "remind_offsite": has_data and (
            days_since_offsite is None or days_since_offsite >= OFFSITE_REMIND_DAYS
        ),
    }
