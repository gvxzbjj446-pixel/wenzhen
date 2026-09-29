from html.parser import HTMLParser

import pytest

from werkzeug.datastructures import MultiDict

from conftest import make_patient, make_visit


class FormulaFormValues(HTMLParser):
    """Read the editor's actual controls, excluding the inert blank herb template."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.values = MultiDict()
        self.in_form = False
        self.in_template = False
        self.textarea = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = "data-dirty-guard" in attrs
        if not self.in_form:
            return
        if tag == "template":
            self.in_template = True
        if self.in_template or "name" not in attrs:
            return
        if tag == "input":
            self.values.add(attrs["name"], attrs.get("value", ""))
        elif tag == "textarea":
            self.textarea = (attrs["name"], [])

    def handle_data(self, data):
        if self.textarea:
            self.textarea[1].append(data)

    def handle_endtag(self, tag):
        if tag == "template":
            self.in_template = False
        elif tag == "form":
            self.in_form = False
        elif tag == "textarea" and self.textarea:
            name, parts = self.textarea
            self.values.add(name, "".join(parts))
            self.textarea = None


def make_formula(client, db, **overrides):
    data = {
        "name": "自定义测试方", "source": "自定义来源", "indication": "记录主治文本",
        "usage": "用法记录", "notes": "注意事项记录",
        "herb_name": ["测试药甲", "测试药乙"], "herb_dose": ["2.5", "3"],
        "herb_unit": ["g", "枚"], "herb_note": ["脚注甲", "脚注乙"],
    }
    data.update(overrides)
    response = client.post("/formulas/new", data=data)
    assert response.status_code == 302
    return db.execute("SELECT id FROM formulas WHERE name = ?", (data["name"],)).fetchone()[0]


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


def test_formula_detail_and_editor_roundtrip_all_fields(client, db):
    fid = make_formula(client, db, notes="多行备注\n第二行 <核对> & 保留")
    detail = client.get(f"/formulas/{fid}")
    assert detail.status_code == 200
    page = detail.get_data(as_text=True)
    for text in ("自定义测试方", "自定义来源", "记录主治文本", "用法记录", "脚注甲", "不是患者处方"):
        assert text in page
    assert "&lt;核对&gt; &amp; 保留" in page
    assert f'/formulas/{fid}/use' in page
    assert f'/formulas/new?from_formula={fid}' in page
    editor = FormulaFormValues(client.get(f"/formulas/{fid}/edit").get_data(as_text=True))
    assert editor.values["notes"] == "多行备注\n第二行 <核对> & 保留"
    assert editor.values.getlist("herb_name") == ["测试药甲", "测试药乙"]
    editor.values["name"] = "更新的测试方"
    saved = client.post(f"/formulas/{fid}/edit", data=editor.values)
    assert saved.status_code == 302
    row = db.execute("SELECT * FROM formulas WHERE id = ?", (fid,)).fetchone()
    for key in ("name", "source", "indication", "usage", "notes"):
        assert row[key] == editor.values[key]
    items = db.execute(
        "SELECT herb, dose, unit, note FROM formula_items WHERE formula_id = ? ORDER BY position", (fid,),
    ).fetchall()
    assert [tuple(row) for row in items] == [("测试药甲", 2.5, "g", "脚注甲"), ("测试药乙", 3, "枚", "脚注乙")]


def test_search_matches_all_fields_and_filters_exact_source(client, db):
    first = make_formula(client, db, source="经验方甲")
    second = make_formula(client, db, name="另一个测试方", source="经验方甲乙")
    for query in ("自定义测试", "经验方甲", "记录主治", "测试药甲"):
        page = client.get("/formulas/", query_string={"q": query}).get_data(as_text=True)
        assert f'/formulas/{first}/use' in page
    page = client.get("/formulas/", query_string={"source": "经验方甲", "q": "测试药甲"}).get_data(as_text=True)
    assert f'/formulas/{first}/use' in page
    assert f'/formulas/{second}/use' not in page
    assert '找到 1 首' in page and '清除筛选' in page
    assert '经验方甲（1）' in page
    page = client.get("/formulas/", query_string={"source": "不存在的出处"}).get_data(as_text=True)
    assert '没有找到符合条件的方剂' in page
    assert '<option value="不存在的出处" selected>' in page


@pytest.mark.parametrize("literal", ["%", "_", "\\"])
def test_search_escapes_sql_wildcards(client, db, literal):
    fid = make_formula(client, db, name=f"符号{literal}测试方")
    other = make_formula(client, db, name="不含符号的方")
    page = client.get("/formulas/", query_string={"q": literal}).get_data(as_text=True)
    assert f'/formulas/{fid}/use' in page
    assert f'/formulas/{other}/use' not in page


def test_copy_prefills_unique_name_and_only_saves_on_post(client, db):
    original_name = "长" * 50
    fid = make_formula(client, db, name=original_name)
    count = db.execute("SELECT COUNT(*) FROM formulas").fetchone()[0]
    path = f"/formulas/new?from_formula={fid}"
    form = FormulaFormValues(client.get(path).get_data(as_text=True))
    assert db.execute("SELECT COUNT(*) FROM formulas").fetchone()[0] == count
    assert len(form.values["name"]) == 50
    assert form.values["name"].endswith("（副本）")
    assert form.values["source"] == "自定义来源"
    assert form.values.getlist("herb_note") == ["脚注甲", "脚注乙"]
    assert client.post(path, data=form.values).status_code == 302
    assert db.execute("SELECT COUNT(*) FROM formulas").fetchone()[0] == count + 1
    assert db.execute("SELECT name FROM formulas WHERE id = ?", (fid,)).fetchone()[0] == original_name
    second = FormulaFormValues(client.get(path).get_data(as_text=True))
    assert second.values["name"].endswith("（副本 2）")
    assert len(second.values["name"]) == 50
    assert second.values["name"] != form.values["name"]
    assert db.execute("SELECT COUNT(*) FROM formulas").fetchone()[0] == count + 1


def test_invalid_copy_submission_preserves_user_changes(client, db):
    fid = make_formula(client, db)
    path = f"/formulas/new?from_formula={fid}"
    submitted = FormulaFormValues(client.get(path).get_data(as_text=True)).values
    submitted["name"] = ""
    submitted["source"] = "用户改过的出处"
    submitted["notes"] = "不能丢失的修改"
    submitted.setlist("herb_name", ["用户修改的药名"])
    submitted.setlist("herb_dose", ["4"])
    response = client.post(path, data=submitted)
    assert response.status_code == 200
    assert "请填写方名" in response.get_data(as_text=True)
    result = FormulaFormValues(response.get_data(as_text=True)).values
    assert result["name"] == ""
    assert result["source"] == "用户改过的出处"
    assert result["notes"] == "不能丢失的修改"
    assert result.getlist("herb_name") == ["用户修改的药名"]


def test_choose_patient_search_links_and_no_write(client, db):
    fid = make_formula(client, db)
    pid = make_patient(client, name="王测试", phone="13912345678", allergies="测试过敏史")
    other = make_patient(client, name="赵测试", phone="88887654320")
    before = db.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    for query in ("王测试", "139123", str(pid)):
        page = client.get(f"/formulas/{fid}/use", query_string={"q": query}).get_data(as_text=True)
        assert f'/patients/{pid}/visits/new?from_formula={fid}' in page
        assert f'/patients/{other}/visits/new?from_formula={fid}' not in page
        assert '有过敏史' in page
        assert f'/patients/new?from_formula={fid}' in page
    assert db.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == before
    page = client.get(f"/formulas/{fid}/use", query_string={"q": "%"}).get_data(as_text=True)
    assert "没有找到匹配的患者" in page


def test_choose_patient_pagination_keeps_formula_and_query(client, db):
    fid = make_formula(client, db)
    db.executemany("INSERT INTO patients(name) VALUES (?)", [(f"待选患者{i}",) for i in range(31)])
    db.commit()
    page = client.get(f"/formulas/{fid}/use", query_string={"q": "待选患者"}).get_data(as_text=True)
    assert page.count("引用并新建问诊</a>") == 30
    assert f'/formulas/{fid}/use?page=2&amp;q=' in page
    second = client.get(f"/formulas/{fid}/use", query_string={"q": "待选患者", "page": 2}).get_data(as_text=True)
    assert second.count("引用并新建问诊</a>") == 1


@pytest.mark.parametrize("path", [
    "/formulas/999999", "/formulas/999999/use", "/formulas/new?from_formula=999999",
    "/formulas/new?from_formula=bad", "/formulas/new?from_formula=0",
    "/formulas/new?from_formula=", "/formulas/new?from_visit=999999",
])
def test_formula_sources_must_exist(client, path):
    assert client.get(path).status_code == 404


def test_formula_copy_sources_cannot_be_ambiguous(client, db):
    fid = make_formula(client, db)
    assert client.get(f"/formulas/new?from_formula={fid}&from_visit=1").status_code == 400
