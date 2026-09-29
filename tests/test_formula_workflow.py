"""Formula templates are copied into an unsaved visit, never into the source library."""

import pytest

from conftest import make_patient, make_visit
from test_simple_workflow import consultation_form


def formula_fixture(client, db):
    response = client.post("/formulas/new", data={
        "name": "回归测试经验方", "source": "测试资料", "indication": "仅用于软件测试",
        "usage": "测试用法", "notes": "测试备注",
        "herb_name": ["测试药甲", "测试药乙"], "herb_dose": ["1.5", "2"],
        "herb_unit": ["g", "枚"], "herb_note": ["测试脚注", ""],
    })
    assert response.status_code == 302
    return db.execute("SELECT id FROM formulas WHERE name = '回归测试经验方'").fetchone()[0]


def test_formula_starts_unsaved_visit_and_keeps_library_unchanged(client, db):
    fid = formula_fixture(client, db)
    pid = make_patient(client)
    path = f"/patients/{pid}/visits/new?from_formula={fid}"
    form = consultation_form(client, path)
    assert form.details["prescription"] is True
    assert form.data["formula_name"] == "回归测试经验方"
    assert form.data["usage"] == "测试用法"
    assert form.data["syndrome"] == ""  # Indications are not a patient diagnosis.
    assert form.data.getlist("herb_name") == ["测试药甲", "测试药乙"]
    assert form.data.getlist("herb_dose") == ["1.5", "2"]
    assert form.data.getlist("herb_unit") == ["g", "枚"]
    assert form.data.getlist("herb_note") == ["测试脚注", ""]
    assert db.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0

    # Each consultation owns its prescription snapshot.
    form.data.setlist("herb_dose", ["3", "2"])
    form.data["chief_complaint"] = "测试主诉"
    response = client.post(path, data=form.data)
    assert response.status_code == 302
    assert db.execute("SELECT dose FROM prescription_items ORDER BY position").fetchone()[0] == 3
    assert db.execute("SELECT dose FROM formula_items WHERE formula_id = ? ORDER BY position", (fid,)).fetchone()[0] == 1.5
    # Editing or deleting a library template cannot alter an already saved visit.
    client.post(f"/formulas/{fid}/delete")
    assert db.execute("SELECT COUNT(*) FROM prescription_items").fetchone()[0] == 2


def test_formula_query_does_not_overwrite_validation_retry(client, db):
    fid = formula_fixture(client, db)
    pid = make_patient(client)
    path = f"/patients/{pid}/visits/new?from_formula={fid}"
    form = consultation_form(client, path)
    form.data["visit_date"] = "invalid"
    form.data["usage"] = "医师修改的用法"
    form.data.setlist("herb_dose", ["7", "2"])
    response = client.post(path, data=form.data)
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'value="医师修改的用法"' in page
    assert 'value="7"' in page
    assert "已从方剂库带入" not in page
    assert db.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0


@pytest.mark.parametrize("formula_id", ["999999", "nope", "0", ""])
def test_invalid_formula_cannot_start_a_visit(client, formula_id):
    pid = make_patient(client)
    assert client.get(f"/patients/{pid}/visits/new?from_formula={formula_id}").status_code == 404


def test_formula_cannot_silently_override_previous_visit(client, db):
    fid = formula_fixture(client, db)
    pid = make_patient(client)
    vid = make_visit(client, pid)
    response = client.get(f"/patients/{pid}/visits/new?from_formula={fid}&copy_from={vid}")
    assert response.status_code == 400


def test_formula_library_visible_in_primary_navigation(client):
    page = client.get("/").get_data(as_text=True)
    nav = page.split('aria-label="主导航"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/formulas/"' in nav and "方剂库" in nav


def test_new_patient_can_keep_selected_formula(client, db):
    fid = formula_fixture(client, db)
    page = client.get(f"/patients/new?from_formula={fid}").get_data(as_text=True)
    assert f'name="from_formula" value="{fid}"' in page
    retry = client.post("/patients/new", data={"from_formula": fid, "after": "visit"})
    assert retry.status_code == 200
    assert f'name="from_formula" value="{fid}"' in retry.get_data(as_text=True)
    response = client.post("/patients/new", data={
        "name": "方剂流程测试患者", "from_formula": fid, "after": "visit",
    })
    assert response.status_code == 302
    assert f"from_formula={fid}" in response.location
    form = consultation_form(client, response.location)
    assert form.data["formula_name"] == "回归测试经验方"
    assert db.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0


def test_missing_formula_does_not_create_patient(client, db):
    response = client.post("/patients/new", data={
        "name": "不应创建", "from_formula": "999999", "after": "visit",
    })
    assert response.status_code == 404
    assert db.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 0
