from conftest import make_patient, make_visit


def test_seed_formulas_available(client):
    page = client.get("/formulas/").get_data(as_text=True)
    assert "桂枝汤" in page and "小柴胡汤" in page
    # 新建问诊时，方剂数据随页面提供给“引用方剂”功能
    pid = make_patient(client)
    form = client.get(f"/patients/{pid}/visits/new").get_data(as_text=True)
    assert 'id="formula-data"' in form and "桂枝汤" in form


def test_seed_runs_only_once(app, client, db):
    client.post(f"/formulas/{db.execute('SELECT id FROM formulas WHERE name = ?', ('桂枝汤',)).fetchone()[0]}/delete")
    with app.app_context():
        from wenzhen.db import init_db
        init_db()
    assert db.execute("SELECT COUNT(*) FROM formulas WHERE name = '桂枝汤'").fetchone()[0] == 0


def test_create_edit_delete_formula(client, db):
    resp = client.post("/formulas/new", data={
        "name": "胃痛经验方", "source": "经验方", "indication": "肝胃不和之胃痛",
        "herb_name": ["柴胡", "白芍", "延胡索"], "herb_dose": ["10", "15", "10"],
        "herb_unit": ["g", "g", "g"], "herb_note": ["", "", ""],
    })
    assert resp.status_code == 302
    fid = db.execute("SELECT id FROM formulas WHERE name = '胃痛经验方'").fetchone()[0]
    page = client.get("/formulas/?q=延胡索").get_data(as_text=True)
    assert "胃痛经验方" in page

    resp = client.post(f"/formulas/{fid}/edit", data={
        "name": "胃痛经验方", "herb_name": ["柴胡"], "herb_dose": ["12"],
    })
    assert resp.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM formula_items WHERE formula_id = ?", (fid,)).fetchone()[0] == 1

    client.post(f"/formulas/{fid}/delete")
    assert db.execute("SELECT COUNT(*) FROM formula_items WHERE formula_id = ?", (fid,)).fetchone()[0] == 0


def test_formula_validation(client):
    resp = client.post("/formulas/new", data={"name": "桂枝汤", "herb_name": [""]})
    page = resp.get_data(as_text=True)
    assert "已有同名方剂" in page
    assert "请至少录入一味药" in page


def test_save_visit_prescription_as_formula(client):
    pid = make_patient(client)
    vid = make_visit(client, pid, herbs=[("黄芪", "30"), ("当归", "10")])
    page = client.get(f"/formulas/new?from_visit={vid}").get_data(as_text=True)
    assert 'value="柴胡疏肝散加减"' in page
    assert 'value="黄芪"' in page and 'value="当归"' in page
