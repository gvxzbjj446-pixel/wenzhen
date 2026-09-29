"""Exercise the simplified consultation form using its actual submitted controls."""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from werkzeug.datastructures import MultiDict

from conftest import make_patient, make_visit
from wenzhen.fields import VISIT_TEXT_FIELDS


class ConsultationForm(HTMLParser):
    """Read successful controls, including those inside closed details elements."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.data = MultiDict()
        self.details = {}
        self.submitters = set()
        self.submit_order = []
        self.in_form = False
        self.template_depth = 0
        self.textarea = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = "consultation-form" in attrs.get("class", "").split()
        if not self.in_form:
            return
        if tag == "template":
            self.template_depth += 1
        if self.template_depth:
            return
        if tag == "details" and "id" in attrs:
            self.details[attrs["id"]] = "open" in attrs
        name = attrs.get("name")
        if tag == "button" and attrs.get("type", "submit") == "submit" and "disabled" not in attrs:
            self.submit_order.append((name, attrs.get("value", "")))
        if not name or "disabled" in attrs:
            return
        if tag == "button":
            if attrs.get("type", "submit") == "submit":
                self.submitters.add((name, attrs.get("value", "")))
        elif tag == "input":
            kind = attrs.get("type", "text")
            if kind in ("submit", "reset", "button", "file", "image"):
                return
            if kind in ("checkbox", "radio") and "checked" not in attrs:
                return
            self.data.add(name, attrs.get("value", ""))
        elif tag == "textarea":
            self.textarea = (name, [])
        elif tag == "select":
            raise AssertionError("Extend this form reader if named selects are introduced")

    def handle_data(self, data):
        if self.textarea is not None:
            self.textarea[1].append(data)

    def handle_endtag(self, tag):
        if not self.in_form:
            return
        if tag == "template":
            self.template_depth -= 1
            return
        if self.template_depth:
            return
        if tag == "textarea" and self.textarea is not None:
            name, parts = self.textarea
            self.data.add(name, "".join(parts))
            self.textarea = None
        elif tag == "form":
            self.in_form = False

    def click(self, value):
        assert ("after", value) in self.submitters
        data = self.data.copy()
        data["after"] = value
        return data


def consultation_form(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return ConsultationForm(response.get_data(as_text=True))


def test_new_consultation_keeps_optional_sections_collapsed(client):
    pid = make_patient(client)
    form = consultation_form(client, f"/patients/{pid}/visits/new")

    assert form.details["detailed-exam"] is False
    assert form.details["prescription"] is False
    assert set(VISIT_TEXT_FIELDS) <= set(form.data)
    assert form.data.getlist("visit_type") == ["初诊"]
    assert form.data.getlist("herb_name") == []  # Inert template rows are not submitted.
    assert {("after", "therapy"), ("after", "record")} <= form.submitters
    # Enter in a normal text field activates the first submitter: save, not print.
    assert form.submit_order[0] == (None, "")


def test_edit_roundtrip_preserves_all_historical_fields_and_prescription(client, db):
    pid = make_patient(client, gender="女")
    original = {key: f"旧记录 {key} & <保留>" for key in VISIT_TEXT_FIELDS}
    original["present_illness"] = "既往详细病史\n第二行 & <原文>"
    herbs = [("砂仁", "6.5", "g", "后下 & 保留"), ("大枣", "3", "枚", "掰开")]
    vid = make_visit(client, pid, herbs=herbs, **original)
    path = f"/visits/{vid}/edit"
    form = consultation_form(client, path)

    for key, value in original.items():
        assert form.data.getlist(key) == [value], key
    assert form.details["detailed-exam"] is True
    assert form.details["prescription"] is True
    assert form.data.getlist("herb_name") == ["砂仁", "大枣"]
    assert form.data.getlist("herb_unit") == ["g", "枚"]

    # Closing details does not disable its controls. Submit only parsed HTML values.
    form.data["chief_complaint"] = "本次主诉已更新"
    response = client.post(path, data=form.data)
    assert response.status_code == 302
    saved = db.execute("SELECT * FROM visits WHERE id = ?", (vid,)).fetchone()
    for key, value in original.items():
        assert saved[key] == ("本次主诉已更新" if key == "chief_complaint" else value), key
    assert (saved["formula_name"], saved["dose_count"], saved["usage"]) == (
        "柴胡疏肝散加减", 7, "水煎服，日一剂",
    )
    items = db.execute(
        "SELECT herb, dose, unit, note FROM prescription_items"
        " WHERE visit_id = ? ORDER BY position", (vid,),
    ).fetchall()
    assert [tuple(row) for row in items] == [
        ("砂仁", 6.5, "g", "后下 & 保留"), ("大枣", 3, "枚", "掰开"),
    ]


def test_save_consultation_then_create_and_view_linked_therapy(client, db):
    pid = make_patient(client)
    path = f"/patients/{pid}/visits/new"
    form = consultation_form(client, path)
    form.data["chief_complaint"] = "腰痛三日"
    form.data["tcm_disease"] = "腰痛"
    response = client.post(path, data=form.click("therapy"))
    assert response.status_code == 302
    destination = urlsplit(response.headers["Location"])
    assert destination.path == f"/therapy/patients/{pid}/courses/new"
    visit_id = int(parse_qs(destination.query)["visit_id"][0])
    visit = db.execute("SELECT * FROM visits WHERE id = ?", (visit_id,)).fetchone()
    assert visit["patient_id"] == pid and visit["chief_complaint"] == "腰痛三日"
    course_page = client.get(response.headers["Location"])
    assert course_page.status_code == 200
    assert "腰痛三日" in course_page.get_data(as_text=True)

    course_data = {
        "start_date": visit["visit_date"], "planned_sessions": "6",
        "diagnosis": "本次腰痛方案", "item_therapy": ["针灸", "艾灸"],
        "item_site": ["肾俞、委中", "腰部"], "item_minutes": ["30", "15"],
        "item_note": ["", ""],
    }
    created = client.post(response.headers["Location"], data=course_data)
    assert created.status_code == 302
    course_id = int(created.headers["Location"].rstrip("/").split("/")[-1])
    course = db.execute("SELECT * FROM therapy_courses WHERE id = ?", (course_id,)).fetchone()
    assert (course["patient_id"], course["visit_id"]) == (pid, visit_id)

    # Another plan for the same patient must not be presented as linked to this visit.
    course_data["diagnosis"] = "未关联的另一次方案"
    unlinked = client.post(f"/therapy/patients/{pid}/courses/new", data=course_data)
    assert unlinked.status_code == 302
    detail = client.get(f"/visits/{visit_id}").get_data(as_text=True)
    assert "关联理疗方案" in detail and "本次腰痛方案" in detail
    assert f'href="/therapy/courses/{course_id}"' in detail
    assert f'href="/therapy/courses/{course_id}/sessions/new"' in detail
    assert "未关联的另一次方案" not in detail


def test_save_and_print_record_uses_the_saved_consultation(client, db):
    pid = make_patient(client)
    path = f"/patients/{pid}/visits/new"
    form = consultation_form(client, path)
    form.data["chief_complaint"] = "肩痛两周，活动受限"
    response = client.post(path, data=form.click("record"))
    assert response.status_code == 302
    visit_id = db.execute("SELECT id FROM visits WHERE patient_id = ?", (pid,)).fetchone()[0]
    assert response.headers["Location"] == f"/visits/{visit_id}/print/record"
    printed = client.get(response.headers["Location"])
    assert printed.status_code == 200
    assert "门诊病历" in printed.get_data(as_text=True)
    assert "肩痛两周，活动受限" in printed.get_data(as_text=True)
