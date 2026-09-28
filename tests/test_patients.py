from datetime import date

from conftest import make_patient, make_visit


def test_create_patient_with_age_only(client, db):
    pid = make_patient(client, name="李四", gender="女", age="45", allergies="青霉素")
    row = db.execute("SELECT * FROM patients WHERE id = ?", (pid,)).fetchone()
    assert row["birth_date"] == f"{date.today().year - 45}-01-01"
    page = client.get(f"/patients/{pid}").get_data(as_text=True)
    assert "李四" in page
    assert "过敏史：青霉素" in page


def test_create_patient_requires_name(client, db):
    resp = client.post("/patients/new", data={"name": "  "})
    assert resp.status_code == 200
    assert "请填写姓名" in resp.get_data(as_text=True)
    assert db.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 0


def test_invalid_birth_date_rejected(client):
    resp = client.post("/patients/new", data={"name": "王五", "birth_date": "2999-01-01"})
    assert "出生日期不正确" in resp.get_data(as_text=True)


def test_save_and_start_visit(client):
    resp = client.post("/patients/new", data={"name": "赵六", "after": "visit"})
    assert resp.headers["Location"].endswith("/visits/new")


def test_duplicate_name_warning(client):
    make_patient(client, name="张三")
    resp = client.post("/patients/new", data={"name": "张三"}, follow_redirects=True)
    assert "另有 1 位同名患者" in resp.get_data(as_text=True)


def test_search_by_name_phone_and_record_no(client):
    pid = make_patient(client, name="张三", phone="13912345678")
    make_patient(client, name="李四", phone="13700000000")
    by_name = client.get("/patients/?q=张").get_data(as_text=True)
    assert "张三" in by_name and "李四" not in by_name
    by_phone = client.get("/patients/?q=1234567").get_data(as_text=True)
    assert "张三" in by_phone and "李四" not in by_phone
    by_no = client.get(f"/patients/?q={pid:06d}").get_data(as_text=True)
    assert "张三" in by_no


def test_search_treats_wildcards_literally(client):
    make_patient(client, name="张三")
    page = client.get("/patients/?q=%25").get_data(as_text=True)
    assert "张三" not in page


def test_edit_patient(client, db):
    pid = make_patient(client)
    resp = client.post(f"/patients/{pid}/edit", data={"name": "张三丰", "gender": "男", "phone": "1"})
    assert resp.status_code == 302
    assert db.execute("SELECT name FROM patients WHERE id = ?", (pid,)).fetchone()["name"] == "张三丰"


def test_delete_patient_removes_visits(client, db):
    pid = make_patient(client)
    make_visit(client, pid, herbs=[("黄芪", "30")])
    client.post(f"/patients/{pid}/delete")
    assert db.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM prescription_items").fetchone()[0] == 0


def test_missing_patient_404(client):
    assert client.get("/patients/999").status_code == 404
