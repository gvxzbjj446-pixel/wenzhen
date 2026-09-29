"""患者档案：列表、搜索、新建、编辑、删除、详情（含历次就诊）。"""

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .db import get_db
from .records import get_formula, get_patient, items_for_visits
from .therapy import patient_courses
from .utils import escape_like, paginate, parse_date

bp = Blueprint("patients", __name__, url_prefix="/patients")

PER_PAGE = 30
PATIENT_FIELDS = (
    "name", "gender", "birth_date", "phone", "address", "occupation",
    "allergies", "past_history", "family_history", "notes",
)
GENDERS = ("男", "女")


def parse_patient_form(form):
    data = {key: form.get(key, "").strip() for key in PATIENT_FIELDS}
    errors = []
    if not data["name"]:
        errors.append("请填写姓名。")
    elif len(data["name"]) > 50:
        errors.append("姓名不超过 50 个字符。")
    if data["gender"] not in GENDERS:
        data["gender"] = ""
    today = date.today()
    if data["birth_date"]:
        born = parse_date(data["birth_date"])
        if not born or born > today:
            errors.append("出生日期不正确。")
    elif form.get("age", "").strip():
        # 只知道年龄时，按当年 1 月 1 日估算出生日期
        try:
            age = int(form["age"])
        except ValueError:
            age = -1
        if 0 <= age <= 130:
            data["birth_date"] = f"{today.year - age:04d}-01-01"
        else:
            errors.append("年龄应为 0–130 之间的整数。")
    return data, errors


def search_clause(q):
    """按姓名、电话或病历号搜索。"""
    if not q:
        return "", []
    like = f"%{escape_like(q)}%"
    clause = "WHERE (p.name LIKE ? ESCAPE '\\' OR p.phone LIKE ? ESCAPE '\\'"
    params = [like, like]
    if q.isdigit():
        clause += " OR p.id = ?"
        params.append(int(q))
    return clause + ")", params


@bp.route("/")
def index():
    q = request.args.get("q", "").strip()
    where, params = search_clause(q)
    conn = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM patients p {where}", params).fetchone()[0]
    pager = paginate(total, request.args.get("page", 1, type=int), PER_PAGE)
    patients = conn.execute(
        f"""
        SELECT p.*, COUNT(v.id) AS visit_count, MAX(v.visit_date) AS last_visit
        FROM patients p LEFT JOIN visits v ON v.patient_id = p.id
        {where}
        GROUP BY p.id
        ORDER BY COALESCE(MAX(v.visit_date), substr(p.created_at, 1, 10)) DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        params + [PER_PAGE, pager["offset"]],
    ).fetchall()
    return render_template("patients/index.html", patients=patients, q=q, pager=pager)


@bp.route("/new", methods=("GET", "POST"))
def new():
    errors = []
    source = request.form if request.method == "POST" else request.args
    applied_formula = None
    if "from_formula" in source:
        formula_id = source.get("from_formula", type=int)
        if not formula_id:
            abort(404)
        applied_formula = get_formula(formula_id)
    patient = {key: "" for key in PATIENT_FIELDS}
    patient["name"] = request.args.get("name", "").strip()
    if request.method == "POST":
        patient, errors = parse_patient_form(request.form)
        if not errors:
            conn = get_db()
            cur = conn.execute(
                f"INSERT INTO patients ({', '.join(PATIENT_FIELDS)})"
                f" VALUES ({', '.join('?' * len(PATIENT_FIELDS))})",
                [patient[k] for k in PATIENT_FIELDS],
            )
            conn.commit()
            same_name = conn.execute(
                "SELECT COUNT(*) FROM patients WHERE name = ? AND id != ?",
                (patient["name"], cur.lastrowid),
            ).fetchone()[0]
            flash(f"已建立患者档案：{patient['name']}。", "success")
            if same_name:
                flash(f"提示：另有 {same_name} 位同名患者，请确认没有重复建档。", "warning")
            if request.form.get("after") == "visit":
                return redirect(url_for("visits.new", patient_id=cur.lastrowid,
                                        from_formula=applied_formula["id"] if applied_formula else None))
            return redirect(url_for("patients.detail", patient_id=cur.lastrowid))
    return render_template("patients/form.html", patient=patient, errors=errors, is_new=True,
                           applied_formula=applied_formula)


@bp.route("/<int:patient_id>")
def detail(patient_id):
    patient = get_patient(patient_id)
    visits = get_db().execute(
        "SELECT * FROM visits WHERE patient_id = ? ORDER BY visit_date DESC, id DESC",
        (patient_id,),
    ).fetchall()
    items = items_for_visits(v["id"] for v in visits)
    total_fee = sum(v["fee"] for v in visits)
    return render_template(
        "patients/detail.html", patient=patient, visits=visits, items=items,
        total_fee=total_fee, courses=patient_courses(patient_id),
    )


@bp.route("/<int:patient_id>/edit", methods=("GET", "POST"))
def edit(patient_id):
    patient = dict(get_patient(patient_id))
    errors = []
    if request.method == "POST":
        data, errors = parse_patient_form(request.form)
        if not errors:
            conn = get_db()
            conn.execute(
                f"UPDATE patients SET {', '.join(f'{k} = ?' for k in PATIENT_FIELDS)},"
                " updated_at = datetime('now', 'localtime') WHERE id = ?",
                [data[k] for k in PATIENT_FIELDS] + [patient_id],
            )
            conn.commit()
            flash("患者信息已更新。", "success")
            return redirect(url_for("patients.detail", patient_id=patient_id))
        data["id"] = patient_id
        patient = data
    return render_template("patients/form.html", patient=patient, errors=errors, is_new=False)


@bp.route("/<int:patient_id>/delete", methods=("POST",))
def delete(patient_id):
    patient = get_patient(patient_id)
    conn = get_db()
    conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
    conn.commit()
    flash(f"已删除患者 {patient['name']} 及其全部就诊、理疗记录。", "success")
    return redirect(url_for("patients.index"))
