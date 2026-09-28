from conftest import make_patient, make_visit


def test_stats_page(client):
    a = make_patient(client, name="甲", gender="男", age="30")
    b = make_patient(client, name="乙", gender="女", age="65")
    make_visit(client, a, visit_date="2026-08-03", herbs=[("柴胡", "9"), ("白芍", "12")])
    make_visit(client, a, visit_date="2026-09-03", visit_type="复诊", herbs=[("柴胡", "6")])
    make_visit(client, b, visit_date="2026-09-05", syndrome="脾胃虚寒证", fee="80")
    page = client.get("/stats?start=2026-01-01&end=2026-12-31").get_data(as_text=True)
    assert "2026-08" in page and "2026-09" in page
    assert "肝胃不和证" in page and "脾胃虚寒证" in page
    assert "柴胡" in page
    assert "320.00" in page  # 120 + 120 + 80
    assert "60 岁以上" in page


def test_stats_empty(client):
    assert client.get("/stats").status_code == 200


def test_export_patients_csv(client):
    make_patient(client, name="张三", notes="=HYPERLINK(\"x\")")
    resp = client.get("/export/patients.csv")
    text = resp.get_data(as_text=True)
    assert resp.mimetype == "text/csv"
    assert text.startswith("﻿病历号")
    assert "张三" in text
    assert "'=HYPERLINK" in text  # 防止 Excel 公式注入


def test_export_visits_csv(client):
    pid = make_patient(client)
    make_visit(client, pid, visit_date="2026-09-01", herbs=[("砂仁", "6", "g", "后下")])
    text = client.get("/export/visits.csv").get_data(as_text=True)
    assert "主诉" in text and "胃脘胀痛3月" in text
    assert "砂仁6g（后下）" in text
    filtered = client.get("/export/visits.csv?start=2026-10-01&end=2026-10-31").get_data(as_text=True)
    assert "胃脘胀痛3月" not in filtered


def test_settings_update(client, app):
    resp = client.post("/settings", data={
        "clinic_name": "王艳霞中医诊所", "doctor_name": "王艳霞", "clinic_address": "幸福路 1 号",
        "clinic_phone": "0000-1234567", "default_usage": "外用", "default_dose_count": "x",
    })
    assert resp.status_code == 302
    page = client.get("/").get_data(as_text=True)
    assert "王艳霞中医诊所" in page

    settings_page = client.get("/settings").get_data(as_text=True)
    assert "默认煎服法" not in settings_page and "默认剂数" not in settings_page
    assert "打印在处方笺" in settings_page
    # 默认煎服法与剂数不在设置页修改，仍按原值用于新建问诊
    from wenzhen.db import get_settings
    with app.app_context():
        settings = get_settings()
    assert (settings["default_usage"], settings["default_dose_count"]) == ("水煎服，日一剂，早晚分服", "7")

    bad = client.post("/settings", data={"clinic_name": ""})
    assert "诊所名称不能为空" in bad.get_data(as_text=True)


def test_missing_visit_columns_are_added_on_startup(app):
    from wenzhen.db import get_db, init_db
    with app.app_context():
        conn = get_db()
        conn.execute("ALTER TABLE visits DROP COLUMN lab_results")
        conn.commit()
        init_db()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(visits)")}
    assert "lab_results" in columns


def test_every_page_renders(client, db):
    pid = make_patient(client, gender="女")
    vid = make_visit(client, pid, herbs=[("黄芪", "30")], next_visit_date="2026-12-01")
    fid = db.execute("SELECT id FROM formulas LIMIT 1").fetchone()[0]
    pages = [
        "/", "/patients/", "/patients/new", f"/patients/{pid}", f"/patients/{pid}/edit",
        f"/patients/{pid}/visits/new", f"/visits/{vid}", f"/visits/{vid}/edit", "/visits",
        "/followups?days=60", "/formulas/", "/formulas/new", f"/formulas/{fid}/edit",
        "/stats", "/settings", "/account",
    ]
    for url in pages:
        resp = client.get(url)
        assert resp.status_code == 200, url
    edit = client.get(f"/visits/{vid}/edit").get_data(as_text=True)
    assert "编辑问诊记录" in edit and 'value="黄芪"' in edit
    assert "经带胎产" in edit


def test_items_load_in_chunks(app, client):
    from wenzhen.records import items_for_visits
    pid = make_patient(client)
    ids = [make_visit(client, pid, herbs=[("黄芪", str(i + 1))]) for i in range(3)]
    with app.app_context():
        from wenzhen.records import _load_items
        grouped = _load_items("prescription_items", ids, chunk=2)
        assert [g[0]["dose"] for g in grouped.values()] == [1, 2, 3]
        assert items_for_visits(iter(ids)).keys() == set(ids)
