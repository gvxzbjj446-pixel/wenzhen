"""问诊记录（门诊病历）：新建、复诊带入、编辑、查看、打印、删除；就诊列表与复诊提醒。"""

from datetime import date, timedelta

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

from .compat import find_conflicts, rules_for_js
from .db import get_db, get_settings
from .fields import COPY_FIELDS, VISIT_SECTIONS, VISIT_TEXT_FIELDS, VISIT_TYPES
from .herbs import COMMON_USAGES, HERB_NOTES, UNITS
from .records import (due_followups, formula_payload, get_patient, get_visit,
                      herb_suggestions, previous_visit, save_items, visit_items)
from .utils import escape_like, parse_date, parse_herb_rows, total_grams

bp = Blueprint("visits", __name__)

EXTRA_FIELDS = (
    "visit_date", "visit_type", "formula_name", "dose_count", "usage", "fee",
    "next_visit_date",
)
ALL_FIELDS = VISIT_TEXT_FIELDS + EXTRA_FIELDS
SEARCH_COLUMNS = (
    "p.name", "v.chief_complaint", "v.tcm_disease", "v.syndrome",
    "v.western_diagnosis", "v.formula_name",
)


def blank_visit(has_history):
    settings = get_settings()
    visit = {key: "" for key in ALL_FIELDS}
    visit.update(
        visit_date=date.today().isoformat(),
        visit_type="复诊" if has_history else "初诊",
        dose_count=settings["default_dose_count"],
        usage=settings["default_usage"],
    )
    return visit


def parse_visit_form(form):
    errors = []
    data = {key: form.get(key, "").strip() for key in VISIT_TEXT_FIELDS}
    data["formula_name"] = form.get("formula_name", "").strip()[:50]
    data["usage"] = form.get("usage", "").strip()
    data["visit_type"] = form.get("visit_type") if form.get("visit_type") in VISIT_TYPES else VISIT_TYPES[0]

    raw_date = form.get("visit_date", "").strip()
    visit_date = parse_date(raw_date)
    data["visit_date"] = visit_date.isoformat() if visit_date else raw_date
    if not visit_date:
        errors.append("请填写正确的就诊日期。")

    data["dose_count"] = form.get("dose_count", "").strip()
    if data["dose_count"] and not (data["dose_count"].isdigit() and int(data["dose_count"]) <= 999):
        errors.append("剂数应为 0–999 的整数。")

    data["fee"] = form.get("fee", "").strip()
    if data["fee"]:
        try:
            fee = float(data["fee"])
        except ValueError:
            fee = -1
        if not 0 <= fee < 10_000_000:
            errors.append("收费金额不正确。")

    data["next_visit_date"] = form.get("next_visit_date", "").strip()
    if data["next_visit_date"]:
        next_date = parse_date(data["next_visit_date"])
        if not next_date:
            errors.append("复诊日期不正确。")
        elif visit_date and next_date <= visit_date:
            errors.append("复诊日期应晚于就诊日期。")
        else:
            data["next_visit_date"] = next_date.isoformat()

    items, item_errors = parse_herb_rows(form)
    errors.extend(item_errors)
    return data, items, errors


def _db_values(data):
    values = dict(data)
    values["dose_count"] = int(data["dose_count"] or 0)
    values["fee"] = float(data["fee"] or 0)
    return [values[key] for key in ALL_FIELDS]


def insert_visit(patient_id, data, items):
    conn = get_db()
    cur = conn.execute(
        f"INSERT INTO visits (patient_id, {', '.join(ALL_FIELDS)})"
        f" VALUES (?, {', '.join('?' * len(ALL_FIELDS))})",
        [patient_id] + _db_values(data),
    )
    save_items("prescription_items", cur.lastrowid, items)
    conn.commit()
    return cur.lastrowid


def update_visit(visit_id, data, items):
    conn = get_db()
    conn.execute(
        f"UPDATE visits SET {', '.join(f'{key} = ?' for key in ALL_FIELDS)},"
        " updated_at = datetime('now', 'localtime') WHERE id = ?",
        _db_values(data) + [visit_id],
    )
    save_items("prescription_items", visit_id, items)
    conn.commit()


def filled_sections(visit):
    """按分组列出已填写的字段：[(分组名, [(字段名, 内容), ...]), ...]。"""
    return [
        (title, [(f.label, visit[f.key]) for f in fields if visit[f.key]])
        for title, fields in VISIT_SECTIONS
    ]


def after_save(visit_id):
    if request.form.get("after") == "therapy":
        visit = get_visit(visit_id)
        return redirect(url_for("therapy.new_course", patient_id=visit["patient_id"], visit_id=visit_id))
    if request.form.get("after") == "record":
        return redirect(url_for("visits.print_view", visit_id=visit_id, kind="record"))
    if request.form.get("after") == "print":
        return redirect(url_for("visits.print_view", visit_id=visit_id, kind="prescription"))
    return redirect(url_for("visits.detail", visit_id=visit_id))


def render_form(patient, visit, items, errors, last, copied_from=None):
    # 折叠区仍渲染全部原字段，编辑旧病历时不会清空未展开的内容。
    primary = tuple(s for s in VISIT_SECTIONS if s[0] in ("主诉与病史", "诊断与治法", "其他治疗与医嘱"))
    detailed = tuple(s for s in VISIT_SECTIONS if s not in primary)
    return render_template(
        "visits/form.html",
        patient=patient, visit=visit, items=items, errors=errors,
        last=last, last_items=visit_items(last["id"]) if last else [],
        copied_from=copied_from, sections=VISIT_SECTIONS, visit_types=VISIT_TYPES,
        primary_sections=primary, detailed_sections=detailed,
        has_details=any(visit[f.key] for _, fields in detailed for f in fields),
        formulas=formula_payload(), herbs=herb_suggestions(), units=UNITS,
        herb_notes=HERB_NOTES, usages=COMMON_USAGES, compat_rules=rules_for_js(),
    )


@bp.route("/patients/<int:patient_id>/visits/new", methods=("GET", "POST"))
def new(patient_id):
    patient = get_patient(patient_id)
    last = previous_visit(patient_id)
    copied_from = None
    if request.method == "POST":
        visit, items, errors = parse_visit_form(request.form)
        if not errors:
            visit_id = insert_visit(patient_id, visit, items)
            flash("问诊记录已保存。", "success")
            return after_save(visit_id)
    else:
        errors, items = [], []
        visit = blank_visit(last is not None)
        copy_id = request.args.get("copy_from", type=int)
        if copy_id:
            copied_from = get_visit(copy_id)
            if copied_from["patient_id"] != patient_id:
                abort(404)
            for key in COPY_FIELDS:
                visit[key] = copied_from[key]
            visit["visit_type"] = "复诊"
            items = visit_items(copy_id)
    return render_form(patient, visit, items, errors, last, copied_from)


@bp.route("/visits/<int:visit_id>")
def detail(visit_id):
    visit = get_visit(visit_id)
    patient = get_patient(visit["patient_id"])
    items = visit_items(visit_id)
    ids = [row["id"] for row in get_db().execute(
        "SELECT id FROM visits WHERE patient_id = ? ORDER BY visit_date, id",
        (patient["id"],),
    )]
    pos = ids.index(visit_id)
    return render_template(
        "visits/detail.html",
        visit=visit, patient=patient, items=items, sections=filled_sections(visit),
        visit_no=pos + 1, visit_total=len(ids),
        prev_id=ids[pos - 1] if pos > 0 else None,
        next_id=ids[pos + 1] if pos + 1 < len(ids) else None,
        conflicts=find_conflicts(item["herb"] for item in items),
        total_grams=total_grams(items),
        related_courses=get_db().execute(
            "SELECT id, diagnosis, body_part, status, planned_sessions FROM therapy_courses"
            " WHERE visit_id = ? ORDER BY start_date DESC, id DESC", (visit_id,)
        ).fetchall(),
    )


@bp.route("/visits/<int:visit_id>/edit", methods=("GET", "POST"))
def edit(visit_id):
    existing = get_visit(visit_id)
    patient = get_patient(existing["patient_id"])
    if request.method == "POST":
        visit, items, errors = parse_visit_form(request.form)
        if not errors:
            update_visit(visit_id, visit, items)
            flash("问诊记录已更新。", "success")
            return after_save(visit_id)
    else:
        visit, items, errors = dict(existing), visit_items(visit_id), []
    visit["id"] = visit_id
    return render_form(patient, visit, items, errors, previous_visit(patient["id"], existing))


@bp.route("/visits/<int:visit_id>/delete", methods=("POST",))
def delete(visit_id):
    visit = get_visit(visit_id)
    conn = get_db()
    conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
    conn.commit()
    flash(f"已删除 {visit['visit_date']} 的就诊记录。", "success")
    return redirect(url_for("patients.detail", patient_id=visit["patient_id"]))


@bp.route("/visits/<int:visit_id>/print/<any(prescription, record):kind>")
def print_view(visit_id, kind):
    visit = get_visit(visit_id)
    items = visit_items(visit_id)
    return render_template(
        f"print/{kind}.html",
        visit=visit, patient=get_patient(visit["patient_id"]), items=items,
        sections=filled_sections(visit), total_grams=total_grams(items),
    )


@bp.route("/visits")
def index():
    today = date.today()
    start = parse_date(request.args.get("start")) or today
    end = parse_date(request.args.get("end")) or start
    if start > end:
        start, end = end, start
    q = request.args.get("q", "").strip()
    sql = """
        SELECT v.id, v.visit_date, v.visit_type, v.chief_complaint, v.tcm_disease,
               v.syndrome, v.formula_name, v.dose_count, v.fee,
               p.id AS patient_id, p.name, p.gender, p.birth_date
        FROM visits v JOIN patients p ON p.id = v.patient_id
        WHERE v.visit_date BETWEEN ? AND ?
    """
    params = [start.isoformat(), end.isoformat()]
    if q:
        like = f"%{escape_like(q)}%"
        sql += " AND (" + " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in SEARCH_COLUMNS) + ")"
        params += [like] * len(SEARCH_COLUMNS)
    sql += " ORDER BY v.visit_date DESC, v.id DESC LIMIT 1001"
    visits = get_db().execute(sql, params).fetchall()
    truncated = len(visits) > 1000
    visits = visits[:1000]
    week_start = today - timedelta(days=today.weekday())
    ranges = {
        "今天": (today, today),
        "昨天": (today - timedelta(days=1), today - timedelta(days=1)),
        "本周": (week_start, today),
        "本月": (today.replace(day=1), today),
    }
    return render_template(
        "visits/index.html", visits=visits, start=start.isoformat(), end=end.isoformat(),
        q=q, truncated=truncated, total_fee=sum(v["fee"] for v in visits),
        ranges={label: (a.isoformat(), b.isoformat()) for label, (a, b) in ranges.items()},
    )


@bp.route("/followups")
def followups():
    today = date.today()
    days = min(max(request.args.get("days", 14, type=int), 1), 365)
    overdue = due_followups(
        (today - timedelta(days=60)).isoformat(), (today - timedelta(days=1)).isoformat()
    )
    upcoming = due_followups(today.isoformat(), (today + timedelta(days=days)).isoformat())
    return render_template(
        "visits/followups.html", overdue=list(reversed(overdue)), upcoming=upcoming,
        days=days,
    )
