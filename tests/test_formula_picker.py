"""Formula templates are searchable, safely embedded and read-only until applied."""

import json
from html.parser import HTMLParser

from conftest import make_patient
from wenzhen.records import formula_payload


class PickerMarkup(HTMLParser):
    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.controls = []
        self.formula_json = []
        self.formula_script_count = 0
        self.in_formula_script = False
        self.injected_images = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("input", "select", "button"):
            self.controls.append((tag, attrs))
        if tag == "script" and attrs.get("id") == "formula-data":
            assert attrs.get("type") == "application/json"
            self.formula_script_count += 1
            self.in_formula_script = True
        if tag == "img" and "onerror" in attrs:
            self.injected_images.append(attrs)

    def handle_data(self, data):
        if self.in_formula_script:
            self.formula_json.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_formula_script = False

    def payload(self):
        assert self.formula_script_count == 1
        return json.loads("".join(self.formula_json))


def make_preview_formula(client, db):
    unsafe_text = '</script><img src=x onerror="alert(1)"> & 药方备注'
    response = client.post("/formulas/new", data={
        "name": "预览测试 <方>", "source": unsafe_text,
        "indication": "仅用于软件测试的主治", "usage": "仅用于测试的用法",
        "notes": unsafe_text,
        "herb_name": ["测试药甲", "测试药乙"], "herb_dose": ["1.5", ""],
        "herb_unit": ["g", "枚"], "herb_note": ["测试脚注", ""],
    })
    assert response.status_code == 302
    fid = db.execute("SELECT id FROM formulas WHERE name = ?", ("预览测试 <方>",)).fetchone()[0]
    return fid, unsafe_text


def test_formula_payload_includes_search_and_preview_fields(app, client, db):
    fid, unsafe_text = make_preview_formula(client, db)
    with app.app_context():
        formula = next(item for item in formula_payload() if item["id"] == fid)
    assert formula == {
        "id": fid, "name": "预览测试 <方>", "source": unsafe_text,
        "indication": "仅用于软件测试的主治", "usage": "仅用于测试的用法",
        "notes": unsafe_text,
        "items": [
            {"herb": "测试药甲", "dose": 1.5, "unit": "g", "note": "测试脚注"},
            {"herb": "测试药乙", "dose": None, "unit": "枚", "note": ""},
        ],
    }


def test_picker_safely_embeds_formula_data_without_changing_library(client, db):
    fid, unsafe_text = make_preview_formula(client, db)
    pid = make_patient(client)
    before = [tuple(row) for row in db.execute("SELECT * FROM formula_items WHERE formula_id = ?", (fid,))]
    page = client.get(f"/patients/{pid}/visits/new").get_data(as_text=True)
    markup = PickerMarkup(page)
    formula = next(item for item in markup.payload() if item["id"] == fid)
    assert formula["source"] == unsafe_text and formula["notes"] == unsafe_text
    assert not markup.injected_images
    assert "\\u003c/script\\u003e" in "".join(markup.formula_json)
    search = [attrs for tag, attrs in markup.controls if "data-formula-search" in attrs]
    assert len(search) == 1 and search[0]["type"] == "search" and "name" not in search[0]
    buttons = [attrs for tag, attrs in markup.controls if "data-formula-apply" in attrs]
    assert {button["data-formula-apply"] for button in buttons} == {"append", "replace"}
    assert all(button["type"] == "button" and "disabled" in button for button in buttons)
    assert 'data-formula-preview' in page
    assert "引用仅填写本次处方，不修改方剂库" in page
    after = [tuple(row) for row in db.execute("SELECT * FROM formula_items WHERE formula_id = ?", (fid,))]
    assert after == before


def test_picker_empty_library_has_clear_message_and_disabled_actions(client, db):
    db.execute("DELETE FROM formulas")
    db.commit()
    pid = make_patient(client)
    page = client.get(f"/patients/{pid}/visits/new").get_data(as_text=True)
    markup = PickerMarkup(page)
    assert markup.payload() == []
    assert "方剂库暂无方剂，请先添加方剂。" in page
    assert all("disabled" in attrs for tag, attrs in markup.controls if "data-formula-apply" in attrs)
