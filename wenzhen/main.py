"""工作台、统计、系统设置、账户管理、表格导出。备份与恢复见 backup_views.py。"""

import csv
import io
from datetime import date, timedelta
from urllib.parse import quote

from flask import (Blueprint, Response, current_app, flash, g, redirect,
                   render_template, request, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import backup
from .auth import create_user, validate_new_account, validate_password
from .db import get_db, get_settings, set_setting
from .fields import FIELD_LABELS, VISIT_TEXT_FIELDS
from .records import due_followups, items_for_visits
from .utils import age_text, herb_text, parse_date, record_no

bp = Blueprint("main", __name__)

SETTING_FIELDS = (
    ("clinic_name", "诊所名称"),
    ("doctor_name", "医师姓名"),
    ("clinic_address", "诊所地址"),
    ("clinic_phone", "诊所电话"),
    ("default_usage", "默认煎服法"),
    ("default_dose_count", "默认剂数"),
)
AGE_GROUPS = (("0–14 岁", 0, 14), ("15–44 岁", 15, 44), ("45–59 岁", 45, 59), ("60 岁以上", 60, 200))


@bp.app_context_processor
def inject_globals():
    from . import __version__
    return {
        "clinic": get_settings(),
        "today": date.today().isoformat(),
        "desktop": current_app.config["DESKTOP"],
        "app_version": __version__,
    }


@bp.app_errorhandler(400)
@bp.app_errorhandler(404)
def handle_error(error):
    return render_template("error.html", error=error), error.code


# ---------------------------------------------------------------- 工作台

@bp.route("/")
def index():
    conn = get_db()
    today = date.today()
    today_s = today.isoformat()
    month_start = today.replace(day=1).isoformat()
    today_visits = conn.execute(
        """
        SELECT v.id, v.visit_type, v.chief_complaint, v.tcm_disease, v.syndrome,
               v.formula_name, v.fee, p.id AS patient_id, p.name, p.gender, p.birth_date
        FROM visits v JOIN patients p ON p.id = v.patient_id
        WHERE v.visit_date = ? ORDER BY v.id DESC
        """,
        (today_s,),
    ).fetchall()
    month = conn.execute(
        "SELECT COUNT(*) AS visits, COALESCE(SUM(fee), 0) AS revenue"
        " FROM visits WHERE visit_date BETWEEN ? AND ?",
        (month_start, today_s),
    ).fetchone()
    kpis = {
        "today_visits": len(today_visits),
        "today_revenue": sum(v["fee"] for v in today_visits),
        "month_visits": month["visits"],
        "month_revenue": month["revenue"],
        "patients": conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
        "month_new_patients": conn.execute(
            "SELECT COUNT(*) FROM patients WHERE created_at >= ?", (month_start,)
        ).fetchone()[0],
    }
    return render_template(
        "index.html",
        today_visits=today_visits, kpis=kpis,
        due_today=due_followups(today_s, today_s),
        overdue=due_followups((today - timedelta(days=30)).isoformat(),
                              (today - timedelta(days=1)).isoformat()),
        upcoming=due_followups((today + timedelta(days=1)).isoformat(),
                               (today + timedelta(days=7)).isoformat()),
        backup_status=backup.status(),
    )


# ---------------------------------------------------------------- 统计

def _date_range(default_start):
    today = date.today()
    start = parse_date(request.args.get("start")) or default_start
    end = parse_date(request.args.get("end")) or today
    if start > end:
        start, end = end, start
    return start.isoformat(), end.isoformat()


def _top(column, params, limit=10):
    assert column in ("tcm_disease", "syndrome", "formula_name")
    return get_db().execute(
        f"SELECT {column} AS label, COUNT(*) AS hits FROM visits"
        f" WHERE {column} != '' AND visit_date BETWEEN ? AND ?"
        f" GROUP BY {column} ORDER BY hits DESC, label LIMIT ?",
        (*params, limit),
    ).fetchall()


@bp.route("/stats")
def stats():
    conn = get_db()
    params = _date_range(date.today().replace(month=1, day=1))
    summary = conn.execute(
        """
        SELECT COUNT(*) AS visits, COUNT(DISTINCT patient_id) AS patients,
               COALESCE(SUM(visit_type = '初诊'), 0) AS first_visits,
               COALESCE(SUM(fee), 0) AS revenue
        FROM visits WHERE visit_date BETWEEN ? AND ?
        """,
        params,
    ).fetchone()
    monthly = conn.execute(
        """
        SELECT substr(visit_date, 1, 7) AS month, COUNT(*) AS visits,
               COUNT(DISTINCT patient_id) AS patients,
               COALESCE(SUM(visit_type = '初诊'), 0) AS first_visits,
               COALESCE(SUM(fee), 0) AS revenue
        FROM visits WHERE visit_date BETWEEN ? AND ?
        GROUP BY month ORDER BY month
        """,
        params,
    ).fetchall()
    herbs = conn.execute(
        """
        SELECT i.herb, COUNT(DISTINCT i.visit_id) AS uses,
               AVG(CASE WHEN i.unit = 'g' THEN i.dose END) AS avg_dose
        FROM prescription_items i JOIN visits v ON v.id = i.visit_id
        WHERE v.visit_date BETWEEN ? AND ?
        GROUP BY i.herb ORDER BY uses DESC, i.herb LIMIT 20
        """,
        params,
    ).fetchall()

    # 就诊患者的性别、年龄构成（年龄按统计截止日计算）
    seen = conn.execute(
        "SELECT gender, birth_date FROM patients WHERE id IN"
        " (SELECT patient_id FROM visits WHERE visit_date BETWEEN ? AND ?)",
        params,
    ).fetchall()
    genders = {"男": 0, "女": 0, "未填": 0}
    ages = {label: 0 for label, _, _ in AGE_GROUPS}
    ages["未填"] = 0
    end = parse_date(params[1])
    for row in seen:
        genders[row["gender"] if row["gender"] in genders else "未填"] += 1
        born = parse_date(row["birth_date"])
        years = None
        if born and born <= end:
            years = end.year - born.year - ((end.month, end.day) < (born.month, born.day))
        label = next((lbl for lbl, lo, hi in AGE_GROUPS if years is not None and lo <= years <= hi), "未填")
        ages[label] += 1

    return render_template(
        "stats.html", start=params[0], end=params[1], summary=summary, monthly=monthly,
        diseases=_top("tcm_disease", params), syndromes=_top("syndrome", params),
        formulas=_top("formula_name", params), herbs=herbs,
        genders=genders, ages=ages, patient_count=len(seen),
    )


# ---------------------------------------------------------------- 设置

@bp.route("/settings", methods=("GET", "POST"))
def settings():
    errors = []
    values = get_settings()
    if request.method == "POST":
        values = {key: request.form.get(key, "").strip() for key, _ in SETTING_FIELDS}
        if not values["clinic_name"]:
            errors.append("诊所名称不能为空。")
        count = values["default_dose_count"]
        if count and not (count.isdigit() and int(count) <= 999):
            errors.append("默认剂数应为 0–999 的整数。")
        if not errors:
            for key, value in values.items():
                set_setting(key, value)
            get_db().commit()
            flash("设置已保存。", "success")
            return redirect(url_for("main.settings"))

    conn = get_db()
    counts = {
        "patients": conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
        "visits": conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0],
        "formulas": conn.execute("SELECT COUNT(*) FROM formulas").fetchone()[0],
    }
    return render_template(
        "settings.html", fields=SETTING_FIELDS, values=values, errors=errors,
        counts=counts, backup_status=backup.status(),
        data_folder=current_app.instance_path,
    )


@bp.route("/account", methods=("GET", "POST"))
def account():
    conn = get_db()
    errors = {"password": [], "add_user": []}
    action = request.form.get("action")
    if request.method == "POST" and action == "password":
        user = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)
        ).fetchone()
        if not check_password_hash(user["password_hash"], request.form.get("old_password", "")):
            errors["password"].append("原密码不正确。")
        else:
            errors["password"] = validate_password(
                request.form.get("new_password", ""), request.form.get("new_password2", "")
            )
        if not errors["password"]:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(request.form["new_password"]), g.user["id"]),
            )
            conn.commit()
            flash("密码已修改。", "success")
            return redirect(url_for("main.account"))
    elif request.method == "POST" and action == "add_user":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        errors["add_user"] = validate_new_account(
            username, password, request.form.get("password2", "")
        )
        if not errors["add_user"]:
            create_user(username, request.form.get("display_name", "").strip(), password)
            conn.commit()
            flash(f"已添加账户：{username}。", "success")
            return redirect(url_for("main.account"))
    users = conn.execute(
        "SELECT id, username, display_name, created_at FROM users ORDER BY id"
    ).fetchall()
    return render_template("account.html", users=users, errors=errors)


@bp.route("/account/users/<int:user_id>/delete", methods=("POST",))
def delete_user(user_id):
    if user_id == g.user["id"]:
        flash("不能删除当前登录的账户。", "error")
    else:
        conn = get_db()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        flash("账户已删除。", "success")
    return redirect(url_for("main.account"))


# ---------------------------------------------------------------- 导出与备份

def _csv_cell(value):
    # 防止 Excel 把以 = + - @ 开头的文字当作公式执行
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _attachment(filename):
    """下载文件名：中文名（filename*）＋英文兜底（filename），各浏览器都能正确显示。"""
    fallback = filename.encode("ascii", "ignore").decode() or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def _csv_response(filename, header, rows):
    buf = io.StringIO()
    buf.write("﻿")  # BOM：Excel 直接打开时中文不乱码
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows([_csv_cell(v) for v in row] for row in rows)
    return Response(
        buf.getvalue(), mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": _attachment(filename)},
    )


@bp.route("/export/patients.csv")
def export_patients():
    rows = get_db().execute(
        """
        SELECT p.*, COUNT(v.id) AS visit_count, MAX(v.visit_date) AS last_visit
        FROM patients p LEFT JOIN visits v ON v.patient_id = p.id
        GROUP BY p.id ORDER BY p.id
        """
    ).fetchall()
    header = ["病历号", "姓名", "性别", "出生日期", "年龄", "电话", "地址", "职业",
              "过敏史", "既往史", "家族史", "备注", "建档时间", "就诊次数", "最近就诊"]
    data = (
        [record_no(p["id"]), p["name"], p["gender"], p["birth_date"], age_text(p["birth_date"]),
         p["phone"], p["address"], p["occupation"], p["allergies"], p["past_history"],
         p["family_history"], p["notes"], p["created_at"], p["visit_count"], p["last_visit"] or ""]
        for p in rows
    )
    return _csv_response(f"患者档案-{date.today():%Y%m%d}.csv", header, data)


@bp.route("/export/visits.csv")
def export_visits():
    params = _date_range(date(1900, 1, 1))
    visits = get_db().execute(
        """
        SELECT v.*, p.name, p.gender, p.birth_date, p.phone
        FROM visits v JOIN patients p ON p.id = v.patient_id
        WHERE v.visit_date BETWEEN ? AND ?
        ORDER BY v.visit_date, v.id
        """,
        params,
    ).fetchall()
    items = items_for_visits(v["id"] for v in visits)
    header = (["就诊编号", "就诊日期", "病历号", "姓名", "性别", "就诊时年龄", "电话", "初/复诊"]
              + [FIELD_LABELS[key] for key in VISIT_TEXT_FIELDS]
              + ["方名", "处方", "剂数", "煎服法", "收费", "预约复诊"])
    data = (
        [v["id"], v["visit_date"], record_no(v["patient_id"]), v["name"], v["gender"],
         age_text(v["birth_date"], v["visit_date"]), v["phone"], v["visit_type"]]
        + [v[key] for key in VISIT_TEXT_FIELDS]
        + [v["formula_name"], herb_text(items[v["id"]]), v["dose_count"], v["usage"],
           v["fee"], v["next_visit_date"]]
        for v in visits
    )
    return _csv_response(f"就诊记录-{date.today():%Y%m%d}.csv", header, data)
