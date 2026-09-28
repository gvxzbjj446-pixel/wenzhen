import csv
import io
import json
import sqlite3
import zipfile
from datetime import date, timedelta

from conftest import _follow, make_patient, make_visit

from wenzhen import backup


def make_course(client, patient_id, items=(("针刺", "肾俞、委中", "30", "平补平泻"),), **fields):
    data = {
        "start_date": "2026-09-01", "diagnosis": "腰痛·寒湿痹阻证", "body_part": "腰部",
        "planned_sessions": "3", "frequency": "每日1次", "initial_pain": "7",
        "initial_assessment": "腰部压痛，前屈受限",
        "item_therapy": [i[0] for i in items], "item_site": [i[1] for i in items],
        "item_minutes": [i[2] for i in items], "item_note": [i[3] for i in items],
    }
    data.update(fields)
    resp = client.post(f"/therapy/patients/{patient_id}/courses/new", data=data)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return _follow(client, resp)


def make_session(client, course_id, items=(("针刺", "肾俞、委中", "30", ""),), **fields):
    data = {
        "session_date": "2026-09-02", "pain_before": "6", "pain_after": "4",
        "reaction": "局部酸胀", "therapist": "王艳霞", "fee": "80",
        "item_therapy": [i[0] for i in items], "item_site": [i[1] for i in items],
        "item_minutes": [i[2] for i in items], "item_note": [i[3] for i in items],
    }
    data.update(fields)
    resp = client.post(f"/therapy/courses/{course_id}/sessions/new", data=data)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    client.get(resp.headers["Location"])
    return resp


def text(resp):
    return resp.get_data(as_text=True)


# ---------------------------------------------------------------- 理疗项目

def test_default_therapy_types_and_management(client, db):
    names = [r["name"] for r in db.execute("SELECT name FROM therapy_types ORDER BY position")]
    assert names[:3] == ["针刺", "电针", "温针灸"] and "推拿" in names and "耳穴压豆" in names
    page = text(client.get("/therapy/types"))
    assert "针刺" in page and "30 分钟" in page

    resp = client.post("/therapy/types/new", data={
        "name": "浮针", "category": "针法", "minutes": "20", "price": "60", "active": "1",
    })
    assert resp.status_code == 302
    client.get(resp.headers["Location"])
    row = db.execute("SELECT * FROM therapy_types WHERE name = '浮针'").fetchone()
    assert (row["minutes"], row["price"], row["active"]) == (20, 60, 1)

    dup = client.post("/therapy/types/new", data={"name": "浮针"})
    assert "已有同名" in text(dup)
    bad = client.post(f"/therapy/types/{row['id']}/edit", data={"name": "浮针", "minutes": "x"})
    assert "默认时长应为" in text(bad)

    # 停用后录入时不再列出
    client.post(f"/therapy/types/{row['id']}/edit", data={"name": "浮针", "category": "针法"})
    assert db.execute("SELECT active FROM therapy_types WHERE id = ?", (row["id"],)).fetchone()[0] == 0
    pid = make_patient(client)
    form = text(client.get(f"/therapy/patients/{pid}/courses/new"))
    assert '<option value="针刺">' in form and '<option value="浮针">' not in form

    client.post(f"/therapy/types/{row['id']}/delete")
    assert db.execute("SELECT 1 FROM therapy_types WHERE name = '浮针'").fetchone() is None


def test_types_seeded_only_once(app, client, db):
    db.execute("DELETE FROM therapy_types WHERE name = '刮痧'")
    db.commit()
    from wenzhen.db import init_db
    with app.app_context():
        init_db()
    assert db.execute("SELECT 1 FROM therapy_types WHERE name = '刮痧'").fetchone() is None


# ---------------------------------------------------------------- 疗程

def test_course_form_has_pickers(client):
    pid = make_patient(client)
    page = text(client.get(f"/therapy/patients/{pid}/courses/new"))
    assert "足三里" in page and "阿是穴" in page and "腰部" in page
    start = page.index('id="therapy-minutes">') + len('id="therapy-minutes">')
    minutes = json.loads(page[start:page.index("</script>", start)])
    assert minutes["针刺"] == 30 and "穴位贴敷" not in minutes  # 不计时的项目不预填
    assert "每日1次" in page and "未评" in page


def test_create_course_and_detail(client, db):
    pid = make_patient(client, name="李秀兰")
    cid = make_course(client, pid, items=(
        ("针刺", "肾俞、大肠俞、委中", "30", "平补平泻"), ("推拿", "腰部", "20", ""),
    ))
    course = db.execute("SELECT * FROM therapy_courses WHERE id = ?", (cid,)).fetchone()
    assert course["status"] == "进行中" and course["initial_pain"] == 7 and course["planned_sessions"] == 3
    plan = db.execute(
        "SELECT therapy, site, minutes FROM therapy_course_items WHERE course_id = ? ORDER BY position",
        (cid,),
    ).fetchall()
    assert [tuple(r) for r in plan] == [("针刺", "肾俞、大肠俞、委中", 30), ("推拿", "腰部", 20)]

    page = text(client.get(f"/therapy/courses/{cid}"))
    assert "腰痛·寒湿痹阻证" in page and "针刺：肾俞、大肠俞、委中，30分钟，平补平泻" in page
    assert "0 / 3 次" in page and "记录第 1 次治疗" in page

    detail = text(client.get(f"/patients/{pid}"))
    assert "理疗康复（1 个疗程）" in detail and "记录治疗" in detail


def test_course_validation(client):
    pid = make_patient(client)
    resp = client.post(f"/therapy/patients/{pid}/courses/new", data={
        "start_date": "2026-13-01", "planned_sessions": "0", "initial_pain": "11", "fee": "abc",
        "item_therapy": [""], "item_site": ["足三里"], "item_minutes": [""], "item_note": [""],
    })
    page = text(resp)
    assert resp.status_code == 200
    for message in ("开始日期", "计划次数应为", "治疗前疼痛评分应为", "疗程收费不正确", "没有填写治疗项目"):
        assert message in page


def test_course_from_visit(client, db):
    pid = make_patient(client)
    vid = make_visit(client, pid, visit_date="2026-09-05", tcm_disease="痹证", syndrome="风寒湿痹证",
                     western_diagnosis="膝骨关节炎")
    assert "开理疗疗程" in text(client.get(f"/visits/{vid}"))
    form = text(client.get(f"/therapy/patients/{pid}/courses/new?visit_id={vid}"))
    assert "痹证·风寒湿痹证；膝骨关节炎" in form and 'value="2026-09-05"' in form
    cid = make_course(client, pid, visit_id=str(vid))
    assert db.execute("SELECT visit_id FROM therapy_courses WHERE id = ?", (cid,)).fetchone()[0] == vid

    other = make_patient(client, name="别人")
    assert client.get(f"/therapy/patients/{other}/courses/new?visit_id={vid}").status_code == 404
    # 删除就诊记录不会删除疗程
    client.post(f"/visits/{vid}/delete")
    assert db.execute("SELECT visit_id FROM therapy_courses WHERE id = ?", (cid,)).fetchone()[0] is None


def test_new_course_suggests_latest_diagnosis(client):
    pid = make_patient(client)
    vid = make_visit(client, pid, tcm_disease="项痹")
    page = text(client.get(f"/therapy/patients/{pid}/courses/new"))
    assert "带入此诊断" in page and f"visit_id={vid}" in page


# ---------------------------------------------------------------- 每次治疗

def test_sessions_prefill_number_and_finish(client, db):
    pid = make_patient(client)
    cid = make_course(client, pid, planned_sessions="2")

    form = text(client.get(f"/therapy/courses/{cid}/sessions/new"))
    assert "第 1 次治疗" in form and "已按疗程方案填好" in form and 'value="肾俞、委中"' in form
    make_session(client, cid, session_date="2026-09-02", pain_before="6", pain_after="4",
                 items=(("电针", "肾俞、委中、阿是穴", "25", "疏密波"),))

    form = text(client.get(f"/therapy/courses/{cid}/sessions/new"))
    assert "第 2 次治疗" in form and "已按上次治疗填好" in form and 'value="肾俞、委中、阿是穴"' in form
    resp = make_session(client, cid, session_date="2026-09-03", pain_before="4", pain_after="2")
    page = text(client.get(resp.headers["Location"]))
    assert "2 / 2 次" in page and "疼痛评分变化" in page and "data-pain-chart" in page

    with client.session_transaction() as sess:
        assert not sess.get("_flashes")

    finish = text(client.get(f"/therapy/courses/{cid}/edit?finish=1"))
    assert "结束疗程" in finish
    assert 'value="已完成" checked' in finish and 'value="2026-09-03"' in finish
    assert 'name="final_pain" value="2" checked' in finish

    resp = client.post(f"/therapy/courses/{cid}/edit", data={
        "start_date": "2026-09-01", "planned_sessions": "2", "frequency": "每日1次",
        "status": "已完成", "end_date": "", "final_pain": "1", "outcome": "显效",
        "final_assessment": "腰痛明显减轻", "initial_pain": "7",
        "item_therapy": ["针刺"], "item_site": ["肾俞"], "item_minutes": ["30"], "item_note": [""],
    })
    assert resp.status_code == 302
    course = db.execute("SELECT * FROM therapy_courses WHERE id = ?", (cid,)).fetchone()
    assert (course["status"], course["end_date"], course["outcome"], course["final_pain"]) == \
        ("已完成", "2026-09-03", "显效", 1)
    page = text(client.get(f"/therapy/courses/{cid}"))
    assert "疗程已结束" in page and "腰痛明显减轻" in page and "按此方案再开疗程" in page

    again = text(client.get(f"/therapy/patients/{pid}/courses/new?copy_from={cid}"))
    assert "腰痛·寒湿痹阻证" in again and 'value="肾俞"' in again


def test_session_completion_notice(client):
    pid = make_patient(client)
    cid = make_course(client, pid, planned_sessions="1")
    resp = client.post(f"/therapy/courses/{cid}/sessions/new", data={
        "session_date": "2026-09-02", "item_therapy": ["推拿"], "item_site": ["腰部"],
        "item_minutes": ["20"], "item_note": [""],
    })
    page = text(client.get(resp.headers["Location"]))
    assert "已记录第 1 次治疗" in page and "已全部完成" in page


def test_session_validation(client):
    pid = make_patient(client)
    cid = make_course(client, pid, start_date="2026-09-05")
    resp = client.post(f"/therapy/courses/{cid}/sessions/new", data={
        "session_date": "2026-09-01", "pain_before": "12",
        "item_therapy": [""], "item_site": [""], "item_minutes": [""], "item_note": [""],
    })
    page = text(resp)
    assert "早于疗程开始日期" in page and "治疗前疼痛评分应为" in page and "请至少填写一个治疗项目" in page


def test_edit_and_delete_session(client, db):
    pid = make_patient(client)
    cid = make_course(client, pid)
    make_session(client, cid)
    sid = db.execute("SELECT id FROM therapy_sessions").fetchone()[0]
    form = text(client.get(f"/therapy/sessions/{sid}/edit"))
    assert "编辑第 1 次治疗" in form and 'value="80"' in form
    client.post(f"/therapy/sessions/{sid}/edit", data={
        "session_date": "2026-09-04", "pain_before": "", "pain_after": "3", "fee": "",
        "item_therapy": ["艾灸"], "item_site": ["命门"], "item_minutes": [""], "item_note": ["温和灸"],
    })
    row = db.execute("SELECT * FROM therapy_sessions WHERE id = ?", (sid,)).fetchone()
    assert (row["session_date"], row["pain_before"], row["pain_after"], row["fee"]) == ("2026-09-04", None, 3, 0)
    item = db.execute("SELECT therapy, minutes, note FROM therapy_session_items WHERE session_id = ?", (sid,)).fetchone()
    assert tuple(item) == ("艾灸", None, "温和灸")

    client.post(f"/therapy/sessions/{sid}/delete")
    assert db.execute("SELECT COUNT(*) FROM therapy_sessions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM therapy_session_items").fetchone()[0] == 0


def test_fee_prefill(client, db):
    db.execute("UPDATE therapy_types SET price = 50 WHERE name = '针刺'")
    db.execute("UPDATE therapy_types SET price = 30 WHERE name = '拔罐'")
    db.commit()
    pid = make_patient(client)
    cid = make_course(client, pid, items=(("针刺", "", "", ""), ("拔罐", "背部", "", "")))
    assert 'name="fee" inputmode="decimal" value="80"' in text(client.get(f"/therapy/courses/{cid}/sessions/new"))

    prepaid = make_course(client, pid, fee="800")
    form = text(client.get(f"/therapy/courses/{prepaid}/sessions/new"))
    assert 'name="fee" inputmode="decimal" value=""' in form and "已预收 ¥800.00" in form


def test_delete_course_and_patient_cascade(client, db):
    pid = make_patient(client)
    cid = make_course(client, pid)
    make_session(client, cid)
    client.post(f"/therapy/courses/{cid}/delete")
    assert db.execute("SELECT COUNT(*) FROM therapy_sessions").fetchone()[0] == 0

    cid = make_course(client, pid)
    make_session(client, cid)
    client.post(f"/patients/{pid}/delete")
    for table in ("therapy_courses", "therapy_course_items", "therapy_sessions", "therapy_session_items"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# ---------------------------------------------------------------- 首页、工作台、打印、统计、导出

def test_therapy_index_and_dashboard(client):
    today = date.today()
    pid = make_patient(client, name="赵大爷", phone="13911112222")
    idle = make_course(client, pid, start_date=(today - timedelta(days=10)).isoformat(),
                       diagnosis="肩凝症")
    other = make_patient(client, name="钱阿姨")
    busy = make_course(client, other, start_date=today.isoformat(), diagnosis="项痹")
    make_session(client, busy, session_date=today.isoformat())

    page = text(client.get("/therapy/"))
    assert "今日治疗" in page and "钱阿姨" in page and "赵大爷" in page
    assert "已 10 天未来" in page
    assert page.index("赵大爷") > page.index("今日治疗")
    assert "肩凝症" in text(client.get("/therapy/?q=赵"))
    assert "肩凝症" not in text(client.get("/therapy/?q=钱"))
    assert "没有已完成疗程" in text(client.get("/therapy/?status=已完成"))

    home = text(client.get("/"))
    assert "今日理疗" in home and "10 天未来" in home and "赵大爷" in home
    assert f"/therapy/courses/{idle}" in home


def test_print_course(client):
    pid = make_patient(client, allergies="酒精")
    cid = make_course(client, pid, planned_sessions="5")
    make_session(client, cid)
    page = text(client.get(f"/therapy/courses/{cid}/print"))
    assert "理疗治疗单" in page and "酒精" in page and "患者签字" in page
    assert page.count('class="blank"') == 4


def test_stats_include_therapy(client):
    pid = make_patient(client)
    cid = make_course(client, pid, fee="500")
    make_session(client, cid, fee="20")
    client.post(f"/therapy/courses/{cid}/edit", data={
        "start_date": "2026-09-01", "planned_sessions": "3", "status": "已完成", "fee": "500",
        "end_date": "2026-09-10", "outcome": "有效", "initial_pain": "7", "final_pain": "3",
        "item_therapy": ["针刺"], "item_site": [""], "item_minutes": [""], "item_note": [""],
    })
    page = text(client.get("/stats?start=2026-01-01&end=2026-12-31"))
    assert "理疗人次" in page and "520.00" in page and "常用理疗项目" in page
    assert "疗效评价（1 个疗程）" in page and "7.0" in page and "3.0" in page


def test_export_therapy_csv(client):
    pid = make_patient(client, name="孙六")
    cid = make_course(client, pid)
    make_session(client, cid, session_date="2026-09-02", pain_after="")
    resp = client.get("/therapy/export.csv")
    assert resp.mimetype == "text/csv"
    rows = list(csv.reader(io.StringIO(text(resp).lstrip("﻿"))))
    header, row = rows[0], rows[1]
    record = dict(zip(header, row))
    assert record["姓名"] == "孙六" and record["第几次"] == "1"
    assert record["治疗项目"] == "针刺：肾俞、委中，30分钟"
    assert record["治疗前疼痛"] == "6" and record["治疗后疼痛"] == ""
    from urllib.parse import quote
    assert quote("理疗记录") in resp.headers["Content-Disposition"]
    empty = text(client.get("/therapy/export.csv?start=2026-10-01&end=2026-10-31"))
    assert "孙六" not in empty


# ---------------------------------------------------------------- 备份

def test_backup_contains_therapy(app, client, tmp_path):
    pid = make_patient(client, name="周七")
    cid = make_course(client, pid)
    make_session(client, cid)
    target = tmp_path / "package.zip"
    with app.app_context():
        manifest = backup.create_package(str(target), kind="export", full=True)
    assert manifest["counts"]["therapy_courses"] == 1 and manifest["counts"]["therapy_sessions"] == 1
    assert manifest["schema_version"] == 2
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        data = json.loads(zf.read("data.json"))
        readme = zf.read("README.txt").decode("utf-8-sig")
        sessions_csv = zf.read("csv/therapy_sessions.csv").decode("utf-8-sig")
    assert {"csv/therapy_courses.csv", "csv/therapy_course_items.csv", "csv/therapy_sessions.csv",
            "csv/therapy_session_items.csv", "csv/therapy_types.csv"} <= names
    course = data["patients"][0]["therapy_courses"][0]
    assert course["diagnosis"] == "腰痛·寒湿痹阻证" and course["plan"][0]["therapy"] == "针刺"
    assert course["sessions"][0]["items"][0]["site"] == "肾俞、委中"
    assert course["sessions"][0]["pain_before"] == 6
    assert any(t["name"] == "推拿" for t in data["therapy_types"])
    assert "理疗疗程 1 个、治疗 1 次" in readme
    assert sessions_csv.startswith("治疗ID,疗程ID,治疗日期")


def test_restore_backup_from_before_therapy(app, client, db, tmp_path):
    """恢复 1.2 版（没有理疗各表）的备份后，理疗功能照常可用。"""
    make_patient(client, name="老患者")
    old = tmp_path / "old.sqlite3"
    src = sqlite3.connect(app.config["DATABASE"])
    dst = sqlite3.connect(old)
    src.backup(dst)
    src.close()
    for table in ("therapy_session_items", "therapy_sessions", "therapy_course_items",
                  "therapy_courses", "therapy_types"):
        dst.execute(f"DROP TABLE {table}")
    dst.execute("DELETE FROM settings WHERE key = 'therapy_types_seeded'")
    dst.execute("PRAGMA user_version = 1")
    dst.commit()
    dst.close()

    with app.app_context():
        info = backup.inspect_backup(str(old))
        assert info["therapy_sessions"] == 0 and info["patients"] == 1
        backup.restore_backup(str(old))
    assert db.execute("SELECT COUNT(*) FROM therapy_types").fetchone()[0] > 10
    client.post("/login", data={"username": "wang", "password": "secret1"})
    pid = db.execute("SELECT id FROM patients WHERE name = '老患者'").fetchone()[0]
    cid = make_course(client, pid)
    assert client.get(f"/therapy/courses/{cid}").status_code == 200
