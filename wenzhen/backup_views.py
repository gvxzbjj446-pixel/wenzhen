"""数据备份与恢复页面：备份列表、立即备份、导出完整数据包、恢复、外部备份文件夹。"""

import os
import tempfile
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, send_file, session, url_for)

from . import backup
from .db import get_settings


bp = Blueprint("backups", __name__, url_prefix="/backups")


def _package_name(created=None, suffix=".zip"):
    created = created or datetime.now()
    return f"{get_settings()['clinic_name']}-数据备份-{created:%Y%m%d-%H%M}{suffix}"


def _after_restore(safety):
    """恢复后账户可能已变化，需要重新登录。"""
    session.clear()
    flash(f"数据已从备份恢复，请重新登录。恢复前的数据已另存为 {os.path.basename(safety)}。", "success")
    return redirect(url_for("auth.login"))


@bp.route("/")
def index():
    backups = backup.list_backups()
    return render_template(
        "backups.html",
        backups=backups,
        status=backup.status(backups),
        backup_folder=backup.backup_dir(),
        keep_days=current_app.config["BACKUP_KEEP_DAYS"],
        remind_days=backup.OFFSITE_REMIND_DAYS,
    )


@bp.route("/now", methods=("POST",))
def backup_now():
    path = backup.manual_backup(current_app.config["BACKUP_KEEP_DAYS"])
    flash(f"已备份：{os.path.basename(path)}", "success")
    status = backup.status()
    if status["mirror_root"] and status["mirror_error"]:
        flash(f"同步到外部备份文件夹失败：{status['mirror_error']}", "warning")
    return redirect(url_for("backups.index"))


@bp.route("/export")
def export():
    """下载完整数据包（含数据库、JSON 和 CSV），用于存档或迁移到其他系统。"""
    fd, path = tempfile.mkstemp(suffix=".zip", prefix="wenzhen-export-")
    os.close(fd)
    try:
        backup.export_package(path)
        response = send_file(path, mimetype="application/zip", as_attachment=True,
                             download_name=_package_name())
    except Exception:
        os.remove(path)
        raise
    response.call_on_close(lambda: _remove_quietly(path))
    return response


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


@bp.route("/files/<name>")
def download(name):
    path = backup.backup_path(name)
    if path is None:
        abort(404)
    info = next((b for b in backup.list_backups() if b["name"] == name), None)
    suffix = os.path.splitext(name)[1]
    backup.mark_offsite()
    return send_file(path, as_attachment=True,
                     download_name=_package_name(info["created"] if info else None, suffix))


@bp.route("/files/<name>/restore", methods=("POST",))
def restore(name):
    path = backup.backup_path(name)
    if path is None:
        abort(404)
    try:
        safety = backup.restore_backup(path)
    except ValueError as exc:
        flash(f"无法恢复：{exc}", "error")
        return redirect(url_for("backups.index"))
    return _after_restore(safety)


@bp.route("/upload", methods=("POST",))
def upload():
    """上传备份文件并恢复（在浏览器中使用时）。桌面版用“从备份文件恢复…”选择文件。"""
    file = request.files.get("backup")
    if not file or not file.filename:
        flash("请选择备份文件。", "error")
        return redirect(url_for("backups.index"))
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(file.filename)[1] or ".zip",
                                prefix="wenzhen-upload-")
    os.close(fd)
    try:
        file.save(path)
        safety = backup.restore_backup(path)
    except ValueError as exc:
        flash(f"无法恢复：{exc}", "error")
        return redirect(url_for("backups.index"))
    finally:
        _remove_quietly(path)
    return _after_restore(safety)


@bp.route("/mirror", methods=("POST",))
def mirror():
    """设置或取消外部备份文件夹（U 盘、网盘同步文件夹等）。"""
    folder = "" if request.form.get("action") == "clear" else request.form.get("folder", "")
    try:
        copied = backup.set_mirror_root(folder)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("backups.index"))
    if not folder.strip():
        flash("已取消同步到外部备份文件夹。", "success")
    elif copied:
        flash(f"已设置外部备份文件夹，现有备份已同步到 {copied}。", "success")
    else:
        flash(f"已设置外部备份文件夹，但复制失败：{backup.status()['mirror_error']}", "warning")
    return redirect(url_for("backups.index"))
