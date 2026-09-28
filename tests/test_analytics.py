import csv
import io

from wenzhen.analytics import build_analytics


def patient(db, name, gender="", birth_date=""):
    return db.execute(
        "INSERT INTO patients (name, gender, birth_date) VALUES (?, ?, ?)",
        (name, gender, birth_date),
    ).lastrowid


def visit(db, pid, day, fee=0):
    return db.execute(
        "INSERT INTO visits (patient_id, visit_date, fee) VALUES (?, ?, ?)",
        (pid, day, fee),
    ).lastrowid


def course(db, pid, day, fee=0, status="进行中", end="", outcome="", before=None, after=None):
    return db.execute(
        """INSERT INTO therapy_courses
           (patient_id, start_date, fee, status, end_date, outcome, initial_pain, final_pain)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (pid, day, fee, status, end, outcome, before, after),
    ).lastrowid


def session(db, cid, day, fee=0, before=None, after=None, items=()):
    sid = db.execute(
        """INSERT INTO therapy_sessions (course_id, session_date, fee, pain_before, pain_after)
           VALUES (?, ?, ?, ?, ?)""", (cid, day, fee, before, after),
    ).lastrowid
    db.executemany("INSERT INTO therapy_session_items (session_id, therapy) VALUES (?, ?)",
                   [(sid, item) for item in items])
    return sid


def analyse(app, db, start="2026-09-01", end="2026-09-30"):
    db.commit()
    with app.app_context():
        return build_analytics(start, end)


def test_service_patients_and_fees_are_not_multiplied_by_joins(app, db):
    shared = patient(db, "问诊兼理疗")
    therapy_only = patient(db, "纯理疗")
    plan_only = patient(db, "仅开方案")
    visit(db, shared, "2026-09-01", 100)
    visit(db, shared, "2026-09-02", 80)
    cid = course(db, shared, "2026-09-01", 500)
    session(db, cid, "2026-09-01", 20, items=("针刺", "针刺", "艾灸"))
    session(db, cid, "2026-09-02", 30, items=("针刺",))
    old = course(db, therapy_only, "2026-08-01", 900)
    session(db, old, "2026-09-02", 40, items=("推拿",))
    course(db, plan_only, "2026-09-03", 200)
    result = analyse(app, db)
    summary = result["summary"]
    assert (summary["visits"], summary["sessions"], summary["patients"]) == (2, 3, 2)
    assert (summary["visit_patients"], summary["therapy_patients"]) == (1, 2)
    assert (summary["visit_fee"], summary["session_fee"], summary["course_fee"]) == (180, 90, 700)
    assert summary["revenue"] == 970  # 不含期外预收 900；同次多个项目不重复计费。
    assert {r["label"]: r["hits"] for r in result["types"]} == {"针刺": 2, "艾灸": 1, "推拿": 1}
    assert result["monthly"][0]["revenue"] == 970
    assert result["monthly"][0]["patients"] == 2


def test_therapy_only_month_is_present_and_patients_are_deduplicated_across_months(app, db):
    pid = patient(db, "跨月患者")
    visit(db, pid, "2026-08-31", 10)
    cid = course(db, pid, "2026-09-01", 80)
    session(db, cid, "2026-09-30", 20)
    result = analyse(app, db, "2026-08-01", "2026-09-30")
    assert [(r["month"], r["visits"], r["sessions"], r["patients"])
            for r in result["monthly"]] == [("2026-08", 1, 0, 1), ("2026-09", 0, 1, 1)]
    assert result["summary"]["patients"] == 1
    assert sum(r["revenue"] for r in result["monthly"]) == result["summary"]["revenue"] == 110


def test_completion_evaluation_and_pain_use_explicit_denominators(app, db):
    pid = patient(db, "疗程评价")
    completed = course(db, pid, "2026-08-01", status="已完成", end="2026-09-01",
                       outcome="有效", before=8, after=3)
    course(db, pid, "2026-08-02", status="已完成", end="2026-09-30", before=0, after=0)
    course(db, pid, "2026-08-03", status="已完成", end="2026-09-10", before=4)
    course(db, pid, "2026-08-04", status="已中止", end="2026-09-15", before=9, after=1)
    course(db, pid, "2026-08-05", status="已完成", end="2026-10-01", before=9, after=1)
    session(db, completed, "2026-09-01", before=0, after=0)
    session(db, completed, "2026-09-02", before=8, after=4)
    session(db, completed, "2026-09-03", before=8)
    result = analyse(app, db)
    courses = result["courses"]
    assert (courses["ended"], courses["completed"], courses["stopped"]) == (4, 3, 1)
    assert (courses["evaluated"], courses["unassessed"], courses["completion_rate"]) == (1, 2, 75)
    assert result["course_pain"] == {
        "total": 3, "paired": 2, "missing": 1, "before": 4, "after": 1.5, "change": -2.5,
    }
    assert result["session_pain"] == {
        "total": 3, "paired": 2, "missing": 1, "before": 4, "after": 2, "change": -2,
    }
    assert result["without_items"] == 3


def test_empty_period_does_not_report_zero_as_a_pain_change(client, app, db):
    result = analyse(app, db)
    assert result["summary"]["patients"] == 0
    assert result["monthly"] == []
    assert result["courses"]["completion_rate"] is None
    assert result["session_pain"]["change"] is None
    page = client.get("/stats?start=2026-09-01&end=2026-09-30").get_data(as_text=True)
    assert "完成比例暂不计算" in page
    assert "暂无前后均填写的有效疼痛评分" in page
    assert "nan" not in page.lower()
    rows = list(csv.reader(io.StringIO(client.get(
        "/stats/export.csv?start=2026-09-01&end=2026-09-30").get_data(as_text=True).lstrip("\ufeff"))))
    assert next(r for r in rows if r[2:4] == ["单次治疗疼痛", "平均变化（后－前）"])[4] == ""


def test_csv_matches_filter_handles_reversed_dates_and_escapes_project_names(client, db):
    pid = patient(db, "仅有理疗", gender="女", birth_date="1950-01-01")
    cid = course(db, pid, "2026-08-01", 999)
    session(db, cid, "2026-09-01", 35, items=("=1+1",))
    session(db, cid, "2026-10-01", 600, items=("期外项目",))
    db.commit()
    response = client.get("/stats/export.csv?start=2026-09-30&end=2026-09-01")
    assert response.status_code == 200 and response.mimetype == "text/csv"
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))
    assert rows[0] == ["统计开始", "统计结束", "分类", "指标或月份", "数值", "单位", "分母", "口径"]
    assert all(r[:2] == ["2026-09-01", "2026-09-30"] for r in rows[1:])
    assert next(r for r in rows if r[2:4] == ["期间汇总", "登记收费合计"])[4] == "35.00"
    assert next(r for r in rows if r[2:4] == ["期间汇总", "服务患者"])[4] == "1"
    assert any(r[3] == "'=1+1" for r in rows)
    assert not any(r[3] == "期外项目" for r in rows)
    page = client.get("/stats?start=2026-09-30&end=2026-09-01").get_data(as_text=True)
    assert "服务患者构成（1 人）" in page and "60 岁以上" in page
    assert "35.00" in page and "期外项目" not in page


def test_summary_csv_requires_authentication(anon):
    assert anon.get("/stats/export.csv").status_code == 302
