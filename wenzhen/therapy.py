"""理疗康复：疗程（方案、评估、小结）、每次治疗记录、疼痛评分变化、治疗单打印、理疗项目维护。

一个疗程对应一个病症的一段系统治疗（如“腰痛 针刺＋推拿 10 次”）。
每次治疗默认按上次治疗（第一次按疗程方案）填好项目、部位和穴位，改动后保存即可。
"""

from datetime import date

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   url_for)

from .db import ADMIN_USERNAME, get_db, get_settings
from .records import get_patient, get_visit
from .therapy_data import (ASSESSMENT_OPTIONS, COURSE_STATUSES, DEFAULT_GAP_DAYS,
                           FREQUENCIES, GAP_DAYS, OUTCOMES, REACTIONS, SITE_GROUPS,
                           TECHNIQUES, THERAPY_CATEGORIES)
from .utils import escape_like, format_number, paginate, parse_date

bp = Blueprint("therapy", __name__, url_prefix="/therapy")

PER_PAGE = 30
MAX_ITEMS = 20
PAIN_LEVELS = tuple(range(11))

# 疗程、治疗记录中可编辑的列
COURSE_FIELDS = (
    "start_date", "diagnosis", "body_part", "goal", "planned_sessions", "frequency",
    "initial_pain", "initial_assessment", "precautions", "fee",
    "status", "end_date", "final_pain", "final_assessment", "outcome",
)
SESSION_FIELDS = ("session_date", "pain_before", "pain_after", "reaction", "notes", "therapist", "fee")
TYPE_FIELDS = ("name", "category", "minutes", "price", "notes", "active")

# 治疗项目明细表 → 外键列
_ITEM_TABLES = {"therapy_course_items": "course_id", "therapy_session_items": "session_id"}


# ---------------------------------------------------------------- 查询

def get_course(course_id):
    row = get_db().execute("SELECT * FROM therapy_courses WHERE id = ?", (course_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def get_session(session_id):
    row = get_db().execute("SELECT * FROM therapy_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def load_items(table, ids, chunk=500):
    fk = _ITEM_TABLES[table]
    ids = list(ids)
    grouped = {i: [] for i in ids}
    for start in range(0, len(ids), chunk):
        part = ids[start:start + chunk]
        rows = get_db().execute(
            f"SELECT {fk} AS owner, therapy, site, minutes, note FROM {table}"
            f" WHERE {fk} IN ({','.join('?' * len(part))}) ORDER BY position, id",
            part,
        )
        for row in rows:
            grouped[row["owner"]].append({
                "therapy": row["therapy"], "site": row["site"],
                "minutes": row["minutes"], "note": row["note"],
            })
    return grouped


def save_items(table, owner_id, items):
    fk = _ITEM_TABLES[table]
    conn = get_db()
    conn.execute(f"DELETE FROM {table} WHERE {fk} = ?", (owner_id,))
    conn.executemany(
        f"INSERT INTO {table} ({fk}, position, therapy, site, minutes, note)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(owner_id, i, it["therapy"], it["site"], it["minutes"], it["note"])
         for i, it in enumerate(items)],
    )


def course_sessions(course_id):
    """疗程的全部治疗记录（按日期先后），附带第几次与项目明细。"""
    rows = get_db().execute(
        "SELECT * FROM therapy_sessions WHERE course_id = ? ORDER BY session_date, id",
        (course_id,),
    ).fetchall()
    items = load_items("therapy_session_items", [r["id"] for r in rows])
    return [dict(r, no=i + 1, items=items[r["id"]]) for i, r in enumerate(rows)]


_COURSE_LIST_SQL = """
    SELECT c.*, p.name, p.gender, p.birth_date, p.phone,
           (SELECT COUNT(*) FROM therapy_sessions s WHERE s.course_id = c.id) AS done,
           (SELECT MAX(session_date) FROM therapy_sessions s WHERE s.course_id = c.id) AS last_date
    FROM therapy_courses c JOIN patients p ON p.id = c.patient_id
"""


def _with_progress(rows, today=None):
    """补充进度与“中断”提醒：进行中的疗程超过一定天数没来治疗。"""
    today = today or date.today()
    result = []
    for row in rows:
        course = dict(row)
        planned = course["planned_sessions"] or 0
        course["remaining"] = max(planned - course["done"], 0)
        course["percent"] = min(round(course["done"] / planned * 100), 100) if planned else 0
        last = parse_date(course["last_date"] or course["start_date"])
        course["idle_days"] = (today - last).days if last else None
        limit = GAP_DAYS.get(course["frequency"], DEFAULT_GAP_DAYS)
        course["stalled"] = (course["status"] == "进行中" and course["idle_days"] is not None
                             and course["idle_days"] > limit)
        result.append(course)
    return result


def patient_courses(patient_id):
    rows = get_db().execute(
        _COURSE_LIST_SQL + " WHERE c.patient_id = ? ORDER BY c.start_date DESC, c.id DESC",
        (patient_id,),
    ).fetchall()
    return _with_progress(rows)


def active_courses():
    rows = get_db().execute(
        _COURSE_LIST_SQL + " WHERE c.status = '进行中' ORDER BY c.start_date, c.id"
    ).fetchall()
    return _with_progress(rows)


def today_sessions(day=None):
    day = (day or date.today()).isoformat()
    rows = get_db().execute(
        """
        SELECT s.*, c.patient_id, c.diagnosis, c.body_part, c.planned_sessions,
               p.name, p.gender, p.birth_date,
               (SELECT COUNT(*) FROM therapy_sessions e WHERE e.course_id = s.course_id
                  AND (e.session_date < s.session_date
                       OR (e.session_date = s.session_date AND e.id <= s.id))) AS no
        FROM therapy_sessions s
        JOIN therapy_courses c ON c.id = s.course_id
        JOIN patients p ON p.id = c.patient_id
        WHERE s.session_date = ? ORDER BY s.id DESC
        """,
        (day,),
    ).fetchall()
    items = load_items("therapy_session_items", [r["id"] for r in rows])
    return [dict(r, items=items[r["id"]]) for r in rows]


def dashboard_summary():
    """工作台用：今日理疗人次与收费、进行中的疗程、中断未来的疗程。"""
    sessions = today_sessions()
    courses = active_courses()
    return {
        "today": sessions,
        "today_fee": sum(s["fee"] for s in sessions),
        "active": len(courses),
        "stalled": [c for c in courses if c["stalled"]],
    }


def therapy_types(active_only=False):
    sql = "SELECT * FROM therapy_types"
    if active_only:
        sql += " WHERE active = 1"
    return get_db().execute(sql + " ORDER BY position, id").fetchall()


def therapy_stats(start, end):
    """统计页用：[start, end] 期间的治疗人次、患者数、收费、常用项目、疗效。"""
    conn = get_db()
    summary = conn.execute(
        """
        SELECT COUNT(*) AS sessions, COUNT(DISTINCT c.patient_id) AS patients,
               COALESCE(SUM(s.fee), 0) AS session_fee
        FROM therapy_sessions s JOIN therapy_courses c ON c.id = s.course_id
        WHERE s.session_date BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()
    courses = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(fee), 0) AS fee FROM therapy_courses"
        " WHERE start_date BETWEEN ? AND ?",
        (start, end),
    ).fetchone()
    types = conn.execute(
        """
        SELECT i.therapy AS label, COUNT(*) AS hits
        FROM therapy_session_items i JOIN therapy_sessions s ON s.id = i.session_id
        WHERE s.session_date BETWEEN ? AND ?
        GROUP BY i.therapy ORDER BY hits DESC, label LIMIT 10
        """,
        (start, end),
    ).fetchall()
    outcomes = {label: 0 for label in OUTCOMES}
    pain = []
    for row in conn.execute(
        "SELECT outcome, initial_pain, final_pain FROM therapy_courses"
        " WHERE status = '已完成' AND end_date BETWEEN ? AND ?",
        (start, end),
    ):
        if row["outcome"] in outcomes:
            outcomes[row["outcome"]] += 1
        if row["initial_pain"] is not None and row["final_pain"] is not None:
            pain.append((row["initial_pain"], row["final_pain"]))
    return {
        "sessions": summary["sessions"],
        "patients": summary["patients"],
        "new_courses": courses["n"],
        "revenue": summary["session_fee"] + courses["fee"],
        "types": types,
        "outcomes": outcomes,
        "finished": sum(outcomes.values()),
        "pain_before": sum(a for a, _ in pain) / len(pain) if pain else None,
        "pain_after": sum(b for _, b in pain) / len(pain) if pain else None,
        "pain_courses": len(pain),
    }


# ---------------------------------------------------------------- 表单解析

def _int(value, label, low, high, errors, unit=""):
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit() and low <= int(value) <= high:
        return int(value)
    errors.append(f"{label}应为 {low}–{high}{unit} 的整数。")
    return value  # 保留原输入，方便修改


def _money(value, label, errors):
    value = (value or "").strip()
    if not value:
        return ""
    try:
        amount = float(value)
    except ValueError:
        amount = -1
    if not 0 <= amount < 10_000_000:
        errors.append(f"{label}不正确。")
    return value


def _date(value, label, errors, required=True):
    value = (value or "").strip()
    parsed = parse_date(value)
    if parsed:
        return parsed.isoformat()
    if value or required:
        errors.append(f"请填写正确的{label}。")
    return value


def parse_item_rows(form):
    """从表单读取治疗项目行（item_therapy / item_site / item_minutes / item_note）。"""
    names = form.getlist("item_therapy")
    sites = form.getlist("item_site")
    minutes = form.getlist("item_minutes")
    notes = form.getlist("item_note")

    def at(values, i):
        return values[i].strip() if i < len(values) else ""

    items, errors = [], []
    for i, raw in enumerate(names):
        therapy = raw.strip()[:30]
        site = at(sites, i)[:200]
        if not therapy:
            if site:
                errors.append(f"第 {i + 1} 行填写了部位 / 穴位，但没有填写治疗项目。")
            continue
        items.append({
            "therapy": therapy,
            "site": site,
            "minutes": _int(at(minutes, i), f"「{therapy}」的时长", 0, 600, errors, " 分钟"),
            "note": at(notes, i)[:100],
        })
    if len(items) > MAX_ITEMS:
        errors.append(f"一次最多记录 {MAX_ITEMS} 个治疗项目。")
    return items, errors


def parse_course_form(form):
    errors = []
    data = {key: form.get(key, "").strip() for key in COURSE_FIELDS}
    data["start_date"] = _date(data["start_date"], "开始日期", errors)
    data["planned_sessions"] = _int(data["planned_sessions"], "计划次数", 1, 365, errors, " 次")
    if data["planned_sessions"] is None:
        errors.append("请填写计划治疗次数。")
    data["initial_pain"] = _int(data["initial_pain"], "治疗前疼痛评分", 0, 10, errors)
    data["final_pain"] = _int(data["final_pain"], "结束时疼痛评分", 0, 10, errors)
    data["fee"] = _money(data["fee"], "疗程收费", errors)
    if data["status"] not in COURSE_STATUSES:
        data["status"] = COURSE_STATUSES[0]
    if data["outcome"] not in OUTCOMES:
        data["outcome"] = ""
    if data["status"] == "进行中":
        data["end_date"] = ""
    else:
        data["end_date"] = _date(data["end_date"], "结束日期", errors, required=False)
        start = parse_date(data["start_date"])
        end = parse_date(data["end_date"])
        if start and end and end < start:
            errors.append("结束日期不能早于开始日期。")
    items, item_errors = parse_item_rows(form)
    errors.extend(item_errors)
    return data, items, errors


def parse_session_form(form, course):
    errors = []
    data = {key: form.get(key, "").strip() for key in SESSION_FIELDS}
    data["session_date"] = _date(data["session_date"], "治疗日期", errors)
    if data["session_date"] and data["session_date"] < course["start_date"] and not errors:
        errors.append(f"治疗日期早于疗程开始日期（{course['start_date']}）。")
    data["pain_before"] = _int(data["pain_before"], "治疗前疼痛评分", 0, 10, errors)
    data["pain_after"] = _int(data["pain_after"], "治疗后疼痛评分", 0, 10, errors)
    data["therapist"] = data["therapist"][:30]
    data["fee"] = _money(data["fee"], "收费", errors)
    items, item_errors = parse_item_rows(form)
    errors.extend(item_errors)
    if not items:
        errors.append("请至少填写一个治疗项目。")
    return data, items, errors


def _db_values(data, fields):
    values = dict(data)
    if "fee" in values:
        values["fee"] = float(values["fee"] or 0)
    return [values[key] for key in fields]


# ---------------------------------------------------------------- 模板共用

def therapy_text(items, sep="；"):
    """治疗项目转为一行文字，如：针刺：肾俞、委中，30分钟，平补平泻；推拿：腰部。"""
    parts = []
    for item in items:
        text = item["therapy"]
        if item.get("site"):
            text += "：" + item["site"]
        if item.get("minutes"):
            text += f"，{item['minutes']}分钟"
        if item.get("note"):
            text += "，" + item["note"]
        parts.append(text)
    return sep.join(parts)


bp.add_app_template_filter(therapy_text, "therapy_text")


def pain_text(value):
    return "—" if value is None or value == "" else str(value)


bp.add_app_template_filter(pain_text, "pain")


# 疼痛评分变化图（服务器端生成 SVG 坐标，不依赖外部图表库）
CHART_W, CHART_H = 640, 220
CHART_PAD = {"left": 34, "right": 64, "top": 14, "bottom": 30}
CHART_SERIES = (("before", "治疗前"), ("after", "治疗后"))


def pain_chart(sessions, course):
    """疗程的疼痛评分变化：初评、每次治疗前后、末评。少于 2 个点时返回 None。"""
    points = []
    if course["initial_pain"] is not None:
        points.append({"label": "初评", "date": course["start_date"],
                       "before": course["initial_pain"], "after": None})
    for s in sessions:
        if s["pain_before"] is not None or s["pain_after"] is not None:
            points.append({"label": f"第{s['no']}次", "date": s["session_date"],
                           "before": s["pain_before"], "after": s["pain_after"]})
    if course["final_pain"] is not None:
        points.append({"label": "末评", "date": course["end_date"],
                       "before": course["final_pain"], "after": None})
    if len(points) < 2:
        return None

    left, top = CHART_PAD["left"], CHART_PAD["top"]
    plot_w = CHART_W - left - CHART_PAD["right"]
    plot_h = CHART_H - top - CHART_PAD["bottom"]
    step = plot_w / (len(points) - 1)

    def x(i):
        return round(left + i * step, 1)

    def y(value):
        return round(top + (10 - value) / 10 * plot_h, 1)

    series = []
    for key, label in CHART_SERIES:
        dots = [(x(i), y(pt[key]), pt[key]) for i, pt in enumerate(points) if pt[key] is not None]
        if not dots:
            continue
        # 缺少评分的次数处断开，不凭空连线
        path, prev = [], None
        for i, pt in enumerate(points):
            if pt[key] is None:
                prev = None
                continue
            path.append(f"{'L' if prev is not None else 'M'}{x(i)} {y(pt[key])}")
            prev = i
        series.append({"key": key, "label": label, "path": " ".join(path), "dots": dots,
                       "last": dots[-1]})
    # 末尾标签太近时错开，避免重叠
    if len(series) == 2 and abs(series[0]["last"][1] - series[1]["last"][1]) < 14:
        upper, lower = sorted(series, key=lambda s: s["last"][1])
        mid = (upper["last"][1] + lower["last"][1]) / 2
        upper["label_y"], lower["label_y"] = mid - 7, mid + 7
    every = max(1, -(-len(points) // 10))  # 最多约 10 个横轴标签
    return {
        "width": CHART_W, "height": CHART_H, "left": left, "right": CHART_W - CHART_PAD["right"],
        "top": top, "bottom": top + plot_h,
        "grid": [(y(v), v) for v in (0, 2, 4, 6, 8, 10)],
        "xlabels": [(x(i), pt["label"]) for i, pt in enumerate(points)
                    if i % every == 0 or i == len(points) - 1],
        "series": series,
        "columns": [(round(x(i) - step / 2, 1), round(step, 1), x(i)) for i in range(len(points))],
        "points": points,
    }


def _form_context():
    types = therapy_types(active_only=True)
    return {
        "therapy_types": types,
        "type_minutes": {t["name"]: t["minutes"] for t in types if t["minutes"]},
        "site_groups": SITE_GROUPS, "techniques": TECHNIQUES,
        "pain_levels": PAIN_LEVELS,
    }


def _default_therapist():
    if g.user is None:
        return ""
    if g.user["username"] == ADMIN_USERNAME:
        return get_settings()["doctor_name"]
    return g.user["display_name"] or g.user["username"]


def _visit_diagnosis(visit):
    parts = [visit["tcm_disease"], visit["syndrome"]]
    text = "·".join(p for p in parts if p)
    if visit["western_diagnosis"]:
        text = f"{text}；{visit['western_diagnosis']}" if text else visit["western_diagnosis"]
    return text


# ---------------------------------------------------------------- 理疗首页

@bp.route("/")
def index():
    conn = get_db()
    today = date.today()
    status = request.args.get("status", "进行中")
    if status not in COURSE_STATUSES + ("全部",):
        status = "进行中"
    q = request.args.get("q", "").strip()

    where, params = [], []
    if status != "全部":
        where.append("c.status = ?")
        params.append(status)
    if q:
        like = f"%{escape_like(q)}%"
        where.append("(p.name LIKE ? ESCAPE '\\' OR p.phone LIKE ? ESCAPE '\\'"
                     " OR c.diagnosis LIKE ? ESCAPE '\\' OR c.body_part LIKE ? ESCAPE '\\')")
        params += [like] * 4
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM therapy_courses c JOIN patients p ON p.id = c.patient_id{clause}",
        params,
    ).fetchone()[0]
    pager = paginate(total, request.args.get("page", 1, type=int), PER_PAGE)
    # 进行中的疗程：中断时间长的在前；其余按开始日期倒序
    order = ("COALESCE(last_date, c.start_date), c.id" if status == "进行中"
             else "c.start_date DESC, c.id DESC")
    courses = _with_progress(conn.execute(
        _COURSE_LIST_SQL + clause + f" ORDER BY {order} LIMIT ? OFFSET ?",
        params + [PER_PAGE, pager["offset"]],
    ).fetchall(), today)

    month_start = today.replace(day=1).isoformat()
    month = therapy_stats(month_start, today.isoformat())
    sessions = today_sessions(today)
    kpis = {
        "today": len(sessions),
        "today_fee": sum(s["fee"] for s in sessions),
        "active": conn.execute(
            "SELECT COUNT(*) FROM therapy_courses WHERE status = '进行中'").fetchone()[0],
        "month_sessions": month["sessions"],
        "month_revenue": month["revenue"],
    }
    return render_template(
        "therapy/index.html", courses=courses, sessions=sessions, kpis=kpis,
        status=status, statuses=COURSE_STATUSES + ("全部",), q=q, pager=pager,
    )


# ---------------------------------------------------------------- 疗程

def _render_course_form(patient, course, items, errors, editing=False, visit=None, last_visit=None):
    return render_template(
        "therapy/course_form.html", patient=patient, course=course, items=items,
        errors=errors, editing=editing, visit=visit, last_visit=last_visit,
        frequencies=FREQUENCIES, assessment_options=ASSESSMENT_OPTIONS,
        body_parts=SITE_GROUPS[0][1], statuses=COURSE_STATUSES, outcomes=OUTCOMES,
        finishing=request.args.get("finish") == "1",
        **_form_context(),
    )


@bp.route("/patients/<int:patient_id>/courses/new", methods=("GET", "POST"))
def new_course(patient_id):
    patient = get_patient(patient_id)
    conn = get_db()
    visit = None
    visit_id = request.values.get("visit_id", type=int)
    if visit_id:
        visit = get_visit(visit_id)
        if visit["patient_id"] != patient_id:
            abort(404)
    if request.method == "POST":
        course, items, errors = parse_course_form(request.form)
        course["status"], course["end_date"] = "进行中", ""
        if not errors:
            fields = ("patient_id", "visit_id") + COURSE_FIELDS
            cur = conn.execute(
                f"INSERT INTO therapy_courses ({', '.join(fields)})"
                f" VALUES ({', '.join('?' * len(fields))})",
                [patient_id, visit_id] + _db_values(course, COURSE_FIELDS),
            )
            save_items("therapy_course_items", cur.lastrowid, items)
            conn.commit()
            flash("理疗疗程已建立。", "success")
            if request.form.get("after") == "session":
                return redirect(url_for("therapy.new_session", course_id=cur.lastrowid))
            return redirect(url_for("therapy.course_detail", course_id=cur.lastrowid))
        return _render_course_form(patient, course, items, errors, visit=visit)

    course = {key: "" for key in COURSE_FIELDS}
    course.update(start_date=date.today().isoformat(), planned_sessions=10,
                  frequency=FREQUENCIES[0], status="进行中", initial_pain=None, final_pain=None)
    items = []
    copy_id = request.args.get("copy_from", type=int)
    if copy_id:
        # 上一疗程结束后再开一个疗程：沿用诊断、部位与方案
        source = get_course(copy_id)
        if source["patient_id"] != patient_id:
            abort(404)
        for key in ("diagnosis", "body_part", "goal", "planned_sessions", "frequency", "precautions"):
            course[key] = source[key]
        items = load_items("therapy_course_items", [copy_id])[copy_id]
    elif visit:
        course["diagnosis"] = _visit_diagnosis(visit)
        course["start_date"] = visit["visit_date"]
    last_visit = None if visit or copy_id else conn.execute(
        "SELECT * FROM visits WHERE patient_id = ? ORDER BY visit_date DESC, id DESC LIMIT 1",
        (patient_id,),
    ).fetchone()
    return _render_course_form(patient, course, items, [], visit=visit, last_visit=last_visit)


@bp.route("/courses/<int:course_id>")
def course_detail(course_id):
    course = get_course(course_id)
    patient = get_patient(course["patient_id"])
    sessions = course_sessions(course_id)
    progress = _with_progress([dict(course, name=patient["name"], gender=patient["gender"],
                                    birth_date=patient["birth_date"], phone=patient["phone"],
                                    done=len(sessions),
                                    last_date=sessions[-1]["session_date"] if sessions else None)])[0]
    visit = None
    if course["visit_id"]:
        visit = get_db().execute("SELECT * FROM visits WHERE id = ?", (course["visit_id"],)).fetchone()
    return render_template(
        "therapy/course_detail.html", course=course, patient=patient, sessions=sessions,
        plan=load_items("therapy_course_items", [course_id])[course_id],
        progress=progress, visit=visit, chart=pain_chart(sessions, course),
        total_fee=course["fee"] + sum(s["fee"] for s in sessions),
    )


@bp.route("/courses/<int:course_id>/edit", methods=("GET", "POST"))
def edit_course(course_id):
    existing = get_course(course_id)
    patient = get_patient(existing["patient_id"])
    if request.method == "POST":
        course, items, errors = parse_course_form(request.form)
        if not errors:
            if course["status"] != "进行中" and not course["end_date"]:
                last = get_db().execute(
                    "SELECT MAX(session_date) FROM therapy_sessions WHERE course_id = ?", (course_id,)
                ).fetchone()[0]
                course["end_date"] = last or date.today().isoformat()
            conn = get_db()
            conn.execute(
                f"UPDATE therapy_courses SET {', '.join(f'{k} = ?' for k in COURSE_FIELDS)},"
                " updated_at = datetime('now', 'localtime') WHERE id = ?",
                _db_values(course, COURSE_FIELDS) + [course_id],
            )
            save_items("therapy_course_items", course_id, items)
            conn.commit()
            flash("疗程已结束。" if existing["status"] == "进行中" and course["status"] != "进行中"
                  else "疗程已更新。", "success")
            return redirect(url_for("therapy.course_detail", course_id=course_id))
    else:
        course, errors = dict(existing), []
        items = load_items("therapy_course_items", [course_id])[course_id]
        if request.args.get("finish") == "1" and course["status"] == "进行中":
            # 结束疗程：默认“已完成”，结束日期为最后一次治疗日期，末评沿用最后一次治疗后的评分
            sessions = course_sessions(course_id)
            course["status"] = "已完成"
            course["end_date"] = sessions[-1]["session_date"] if sessions else date.today().isoformat()
            if course["final_pain"] is None:
                course["final_pain"] = next(
                    (s["pain_after"] for s in reversed(sessions) if s["pain_after"] is not None), None)
    course["id"] = course_id
    return _render_course_form(patient, course, items, errors, editing=True)


@bp.route("/courses/<int:course_id>/delete", methods=("POST",))
def delete_course(course_id):
    course = get_course(course_id)
    conn = get_db()
    conn.execute("DELETE FROM therapy_courses WHERE id = ?", (course_id,))
    conn.commit()
    flash(f"已删除 {course['start_date']} 开始的理疗疗程及其全部治疗记录。", "success")
    return redirect(url_for("patients.detail", patient_id=course["patient_id"]))


@bp.route("/courses/<int:course_id>/print")
def print_course(course_id):
    course = get_course(course_id)
    sessions = course_sessions(course_id)
    return render_template(
        "print/therapy.html", course=course, patient=get_patient(course["patient_id"]),
        sessions=sessions, plan=load_items("therapy_course_items", [course_id])[course_id],
        # 未做完的次数留空行，可打印出来手写
        blank_rows=max(min(course["planned_sessions"], 40) - len(sessions), 0),
    )


# ---------------------------------------------------------------- 每次治疗

def _render_session_form(course, session, items, errors, sessions, editing=False):
    patient = get_patient(course["patient_id"])
    no = (next((s["no"] for s in sessions if s["id"] == session.get("id")), len(sessions))
          if editing else len(sessions) + 1)
    return render_template(
        "therapy/session_form.html", course=course, patient=patient, session=session,
        items=items, errors=errors, editing=editing, sessions=sessions, no=no,
        last=sessions[-1] if sessions and not editing else None,
        reactions=REACTIONS, **_form_context(),
    )


@bp.route("/courses/<int:course_id>/sessions/new", methods=("GET", "POST"))
def new_session(course_id):
    course = get_course(course_id)
    sessions = course_sessions(course_id)
    if request.method == "POST":
        session, items, errors = parse_session_form(request.form, course)
        if not errors:
            conn = get_db()
            fields = ("course_id",) + SESSION_FIELDS
            cur = conn.execute(
                f"INSERT INTO therapy_sessions ({', '.join(fields)})"
                f" VALUES ({', '.join('?' * len(fields))})",
                [course_id] + _db_values(session, SESSION_FIELDS),
            )
            save_items("therapy_session_items", cur.lastrowid, items)
            conn.commit()
            done = len(sessions) + 1
            flash(f"已记录第 {done} 次治疗。", "success")
            if course["status"] == "进行中" and done >= course["planned_sessions"]:
                flash(f"本疗程计划的 {course['planned_sessions']} 次治疗已全部完成，"
                      "可点“结束疗程”填写疗效与小结。", "info")
            return redirect(url_for("therapy.course_detail", course_id=course_id))
        return _render_session_form(course, session, items, errors, sessions)

    last = sessions[-1] if sessions else None
    if last:
        items = [dict(it) for it in last["items"]]
    else:
        items = load_items("therapy_course_items", [course_id])[course_id]
    if course["fee"]:
        fee = ""  # 疗程已预收费用
    elif last:
        fee = format_number(last["fee"]) if last["fee"] else ""
    else:
        prices = {t["name"]: t["price"] for t in therapy_types()}
        fee = format_number(sum(prices.get(it["therapy"], 0) for it in items)) or ""
        fee = "" if fee == "0" else fee
    session = {key: "" for key in SESSION_FIELDS}
    session.update(session_date=date.today().isoformat(), pain_before=None, pain_after=None,
                   therapist=_default_therapist(), fee=fee)
    return _render_session_form(course, session, items, [], sessions)


@bp.route("/sessions/<int:session_id>/edit", methods=("GET", "POST"))
def edit_session(session_id):
    existing = get_session(session_id)
    course = get_course(existing["course_id"])
    sessions = course_sessions(course["id"])
    if request.method == "POST":
        session, items, errors = parse_session_form(request.form, course)
        if not errors:
            conn = get_db()
            conn.execute(
                f"UPDATE therapy_sessions SET {', '.join(f'{k} = ?' for k in SESSION_FIELDS)},"
                " updated_at = datetime('now', 'localtime') WHERE id = ?",
                _db_values(session, SESSION_FIELDS) + [session_id],
            )
            save_items("therapy_session_items", session_id, items)
            conn.commit()
            flash("治疗记录已更新。", "success")
            return redirect(url_for("therapy.course_detail", course_id=course["id"]))
    else:
        session, errors = dict(existing), []
        session["fee"] = format_number(existing["fee"]) if existing["fee"] else ""
        items = load_items("therapy_session_items", [session_id])[session_id]
    session["id"] = session_id
    return _render_session_form(course, session, items, errors, sessions, editing=True)


@bp.route("/sessions/<int:session_id>/delete", methods=("POST",))
def delete_session(session_id):
    session = get_session(session_id)
    conn = get_db()
    conn.execute("DELETE FROM therapy_sessions WHERE id = ?", (session_id,))
    conn.commit()
    flash(f"已删除 {session['session_date']} 的治疗记录。", "success")
    return redirect(url_for("therapy.course_detail", course_id=session["course_id"]))


# ---------------------------------------------------------------- 理疗项目

def parse_type_form(form, type_id=None):
    errors = []
    data = {key: form.get(key, "").strip() for key in TYPE_FIELDS}
    if not data["name"]:
        errors.append("请填写项目名称。")
    elif len(data["name"]) > 30:
        errors.append("项目名称不超过 30 个字符。")
    elif get_db().execute(
        "SELECT 1 FROM therapy_types WHERE name = ? AND id != ?", (data["name"], type_id or 0)
    ).fetchone():
        errors.append("已有同名的理疗项目。")
    data["minutes"] = _int(data["minutes"], "默认时长", 0, 600, errors, " 分钟")
    data["price"] = _money(data["price"], "参考价格", errors)
    data["active"] = 1 if form.get("active") else 0
    return data, errors


def _type_values(data):
    return [data["name"], data["category"][:10], data["minutes"] or 0,
            float(data["price"] or 0), data["notes"], data["active"]]


@bp.route("/types")
def types():
    rows = therapy_types()
    used = dict(get_db().execute(
        "SELECT therapy, COUNT(*) FROM therapy_session_items GROUP BY therapy"
    ).fetchall())
    return render_template("therapy/types.html", types=rows, used=used)


@bp.route("/types/new", methods=("GET", "POST"))
@bp.route("/types/<int:type_id>/edit", methods=("GET", "POST"))
def edit_type(type_id=None):
    conn = get_db()
    existing = None
    if type_id is not None:
        existing = conn.execute("SELECT * FROM therapy_types WHERE id = ?", (type_id,)).fetchone()
        if existing is None:
            abort(404)
    errors = []
    if request.method == "POST":
        data, errors = parse_type_form(request.form, type_id)
        if not errors:
            if existing is None:
                position = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM therapy_types").fetchone()[0]
                conn.execute(
                    "INSERT INTO therapy_types (name, category, minutes, price, notes, active, position)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)", _type_values(data) + [position],
                )
                flash(f"已新增理疗项目：{data['name']}。", "success")
            else:
                conn.execute(
                    "UPDATE therapy_types SET name = ?, category = ?, minutes = ?, price = ?,"
                    " notes = ?, active = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                    _type_values(data) + [type_id],
                )
                flash(f"已更新理疗项目：{data['name']}。", "success")
            conn.commit()
            return redirect(url_for("therapy.types"))
        item = data
    elif existing is not None:
        item = dict(existing)
        item["price"] = format_number(existing["price"]) if existing["price"] else ""
    else:
        item = {"name": "", "category": "", "minutes": "", "price": "", "notes": "", "active": 1}
    return render_template(
        "therapy/type_form.html", item=item, errors=errors, type_id=type_id,
        categories=THERAPY_CATEGORIES,
    )


@bp.route("/types/<int:type_id>/delete", methods=("POST",))
def delete_type(type_id):
    conn = get_db()
    row = conn.execute("SELECT name FROM therapy_types WHERE id = ?", (type_id,)).fetchone()
    if row is None:
        abort(404)
    conn.execute("DELETE FROM therapy_types WHERE id = ?", (type_id,))
    conn.commit()
    flash(f"已删除理疗项目：{row['name']}。以往的治疗记录不受影响。", "success")
    return redirect(url_for("therapy.types"))


# ---------------------------------------------------------------- 导出

def _blank(value):
    return "" if value is None else value


@bp.route("/export.csv")
def export_csv():
    from .main import _csv_response, _date_range
    from .utils import age_text, record_no

    params = _date_range(date(1900, 1, 1))
    rows = get_db().execute(
        """
        SELECT s.*, c.diagnosis, c.body_part, c.planned_sessions, c.patient_id,
               p.name, p.gender, p.birth_date, p.phone,
               (SELECT COUNT(*) FROM therapy_sessions e WHERE e.course_id = s.course_id
                  AND (e.session_date < s.session_date
                       OR (e.session_date = s.session_date AND e.id <= s.id))) AS no
        FROM therapy_sessions s
        JOIN therapy_courses c ON c.id = s.course_id
        JOIN patients p ON p.id = c.patient_id
        WHERE s.session_date BETWEEN ? AND ?
        ORDER BY s.session_date, s.id
        """,
        params,
    ).fetchall()
    items = load_items("therapy_session_items", [r["id"] for r in rows])
    header = ["治疗日期", "病历号", "姓名", "性别", "年龄", "电话", "疗程ID", "诊断", "治疗部位",
              "第几次", "计划次数", "治疗项目", "治疗前疼痛", "治疗后疼痛", "治疗反应",
              "病情记录", "治疗者", "收费"]
    data = (
        [r["session_date"], record_no(r["patient_id"]), r["name"], r["gender"],
         age_text(r["birth_date"], r["session_date"]), r["phone"], r["course_id"],
         r["diagnosis"], r["body_part"], r["no"], r["planned_sessions"],
         therapy_text(items[r["id"]]), _blank(r["pain_before"]), _blank(r["pain_after"]),
         r["reaction"], r["notes"],
         r["therapist"], r["fee"]]
        for r in rows
    )
    return _csv_response(f"理疗记录-{date.today():%Y%m%d}.csv", header, data)
