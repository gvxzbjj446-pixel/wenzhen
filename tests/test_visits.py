from datetime import date, timedelta

from conftest import make_patient, make_visit

HERBS = [("柴胡", "9"), ("白芍", "12"), ("枳壳", "10"), ("炙甘草", "6"), ("砂仁", "6", "g", "后下")]


def test_new_visit_form_prefills_defaults(client):
    pid = make_patient(client, gender="男")
    page = client.get(f"/patients/{pid}/visits/new").get_data(as_text=True)
    assert date.today().isoformat() in page
    assert "水煎服，日一剂，早晚分服" in page
    assert 'value="初诊" checked' in page
    assert "寒热往来" in page  # 快选标签


def test_create_visit_with_prescription(client, db):
    pid = make_patient(client)
    vid = make_visit(client, pid, herbs=HERBS)
    visit = db.execute("SELECT * FROM visits WHERE id = ?", (vid,)).fetchone()
    assert visit["patient_id"] == pid
    assert visit["dose_count"] == 7
    assert visit["fee"] == 120
    items = db.execute(
        "SELECT herb, dose, unit, note FROM prescription_items WHERE visit_id = ? ORDER BY position",
        (vid,),
    ).fetchall()
    assert [tuple(i) for i in items] == [
        ("柴胡", 9, "g", ""), ("白芍", 12, "g", ""), ("枳壳", 10, "g", ""),
        ("炙甘草", 6, "g", ""), ("砂仁", 6, "g", "后下"),
    ]

    page = client.get(f"/visits/{vid}").get_data(as_text=True)
    assert "柴胡疏肝散加减" in page
    assert "共 5 味" in page
    assert "每剂约 43 g" in page
    assert "淡红" in page


def test_visit_validation_keeps_input(client, db):
    pid = make_patient(client)
    resp = client.post(f"/patients/{pid}/visits/new", data={
        "visit_date": "2026-13-40", "chief_complaint": "头痛",
        "herb_name": ["川芎"], "herb_dose": ["abc"], "herb_unit": ["g"], "herb_note": [""],
        "next_visit_date": "2026-01-01",
    })
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "请填写正确的就诊日期" in page
    assert "不是有效数字" in page
    assert 'value="头痛"' in page and 'value="abc"' in page
    assert db.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0


def test_next_visit_must_be_after_visit_date(client):
    pid = make_patient(client)
    resp = client.post(f"/patients/{pid}/visits/new", data={
        "visit_date": "2026-09-10", "next_visit_date": "2026-09-01",
    })
    assert "复诊日期应晚于就诊日期" in resp.get_data(as_text=True)


def test_edit_visit_replaces_herbs(client, db):
    pid = make_patient(client)
    vid = make_visit(client, pid, herbs=HERBS)
    resp = client.post(f"/visits/{vid}/edit", data={
        "visit_date": "2026-09-01", "visit_type": "初诊", "syndrome": "肝郁脾虚证",
        "herb_name": ["柴胡", "", "白术"], "herb_dose": ["6", "", "15"],
        "herb_unit": ["g", "g", "g"], "herb_note": ["", "", ""],
    })
    assert resp.status_code == 302
    assert db.execute("SELECT syndrome FROM visits WHERE id = ?", (vid,)).fetchone()[0] == "肝郁脾虚证"
    herbs = [r[0] for r in db.execute(
        "SELECT herb FROM prescription_items WHERE visit_id = ? ORDER BY position", (vid,))]
    assert herbs == ["柴胡", "白术"]


def test_copy_from_previous_visit(client):
    pid = make_patient(client)
    vid = make_visit(client, pid, herbs=HERBS, tongue_body="胖大齿痕")
    page = client.get(f"/patients/{pid}/visits/new?copy_from={vid}").get_data(as_text=True)
    assert 'value="复诊" checked' in page
    assert 'value="肝胃不和证"' in page
    assert 'value="砂仁"' in page and 'value="后下"' in page
    # 舌象需重新诊察，不带入表单（只在“上次就诊”栏显示）
    assert 'name="tongue_body" value=""' in page


def test_copy_from_other_patients_visit_is_rejected(client):
    a = make_patient(client, name="甲")
    b = make_patient(client, name="乙")
    vid = make_visit(client, a)
    assert client.get(f"/patients/{b}/visits/new?copy_from={vid}").status_code == 404


def test_incompatibility_warning(client):
    pid = make_patient(client)
    vid = make_visit(client, pid, herbs=[("甘草", "6"), ("海藻", "15"), ("附子", "9"), ("法半夏", "9")])
    page = client.get(f"/visits/{vid}").get_data(as_text=True)
    assert "甘草反甘遂、大戟、海藻、芫花" in page
    assert "附子、法半夏" in page


def test_print_pages(client):
    pid = make_patient(client, allergies="磺胺类")
    vid = make_visit(client, pid, herbs=HERBS)
    rx = client.get(f"/visits/{vid}/print/prescription").get_data(as_text=True)
    assert "处方笺" in rx and "砂仁" in rx and "后下" in rx and "磺胺类" in rx
    assert "王艳霞" in rx
    record = client.get(f"/visits/{vid}/print/record").get_data(as_text=True)
    assert "门诊病历" in record and "胃脘胀痛3月" in record
    assert client.get(f"/visits/{vid}/print/other").status_code == 404


def test_save_and_print_redirect(client):
    pid = make_patient(client)
    resp = client.post(f"/patients/{pid}/visits/new", data={
        "visit_date": "2026-09-01", "after": "print", "herb_name": ["黄芪"], "herb_dose": ["30"],
    })
    assert resp.headers["Location"].endswith("/print/prescription")


def test_delete_visit(client, db):
    pid = make_patient(client)
    vid = make_visit(client, pid, herbs=HERBS)
    resp = client.post(f"/visits/{vid}/delete")
    assert resp.headers["Location"].endswith(f"/patients/{pid}")
    assert db.execute("SELECT COUNT(*) FROM prescription_items").fetchone()[0] == 0


def test_patient_timeline_and_visit_navigation(client):
    pid = make_patient(client)
    first = make_visit(client, pid, visit_date="2026-08-01")
    second = make_visit(client, pid, visit_date="2026-08-15", visit_type="复诊")
    page = client.get(f"/patients/{pid}").get_data(as_text=True)
    assert "就诊记录（2 次）" in page
    detail = client.get(f"/visits/{second}").get_data(as_text=True)
    assert "第 2 / 2 诊" in detail
    assert f"/visits/{first}" in detail


def test_visit_list_filters(client):
    pid = make_patient(client, name="张三")
    make_visit(client, pid, visit_date="2026-09-01", syndrome="肝胃不和证")
    other = make_patient(client, name="李四")
    make_visit(client, other, visit_date="2026-09-01", syndrome="脾胃虚寒证")
    page = client.get("/visits?start=2026-09-01&end=2026-09-01&q=虚寒").get_data(as_text=True)
    assert "李四" in page and "张三" not in page


def test_followups(client):
    today = date.today()
    pid = make_patient(client, name="随访患者")
    make_visit(client, pid, visit_date=today.isoformat(),
               next_visit_date=(today + timedelta(days=3)).isoformat())
    page = client.get("/followups").get_data(as_text=True)
    assert "随访患者" in page

    overdue_pid = make_patient(client, name="逾期患者")
    make_visit(client, overdue_pid, visit_date=(today - timedelta(days=20)).isoformat(),
               next_visit_date=(today - timedelta(days=5)).isoformat())
    assert "逾期患者" in client.get("/followups").get_data(as_text=True)
    assert "逾期患者" in client.get("/").get_data(as_text=True)

    # 患者之后来诊过，提醒自动消失
    make_visit(client, overdue_pid, visit_date=today.isoformat())
    assert "逾期患者" not in client.get("/followups").get_data(as_text=True)


def test_dashboard_today(client):
    pid = make_patient(client, name="今日患者")
    make_visit(client, pid, visit_date=date.today().isoformat(), fee="88.5")
    page = client.get("/").get_data(as_text=True)
    assert "今日患者" in page
    assert "88.50" in page
