from datetime import date

from werkzeug.datastructures import MultiDict

from wenzhen.compat import find_conflicts
from wenzhen.utils import age_text, format_number, herb_text, parse_herb_rows


def test_age_text():
    on = date(2026, 9, 28)
    assert age_text("1980-10-01", on) == "45岁"
    assert age_text("1980-09-28", on) == "46岁"
    assert age_text("2025-03-10", on) == "1岁6个月"
    assert age_text("2026-06-01", on) == "3个月"
    assert age_text("2026-09-20", on) == "不足1个月"
    assert age_text("", on) == ""
    assert age_text("2030-01-01", on) == ""


def test_format_number():
    assert format_number(9.0) == "9"
    assert format_number(4.5) == "4.5"
    assert format_number(0.25) == "0.25"
    assert format_number(None) == ""
    assert format_number("abc") == "abc"


def test_parse_herb_rows():
    form = MultiDict([
        ("herb_name", "黄芪"), ("herb_dose", "30"), ("herb_unit", ""), ("herb_note", ""),
        ("herb_name", ""), ("herb_dose", "5"), ("herb_unit", "g"), ("herb_note", ""),
        ("herb_name", "大枣"), ("herb_dose", "3"), ("herb_unit", "枚"), ("herb_note", ""),
        ("herb_name", "砂仁"), ("herb_dose", "-1"), ("herb_unit", "g"), ("herb_note", "后下"),
    ])
    items, errors = parse_herb_rows(form)
    assert [i["herb"] for i in items] == ["黄芪", "大枣", "砂仁"]
    assert items[0] == {"herb": "黄芪", "dose": 30.0, "unit": "g", "note": ""}
    assert items[1]["unit"] == "枚"
    assert len(errors) == 1 and "砂仁" in errors[0]
    assert herb_text(items[:2]) == "黄芪30g、大枣3枚"


def test_find_conflicts():
    assert find_conflicts(["炙甘草", "海藻"])[0]["kind"] == "十八反"
    assert find_conflicts(["制附子", "姜半夏"])[0]["herbs"] == ["制附子", "姜半夏"]
    assert find_conflicts(["藜芦", "赤芍"])
    assert find_conflicts(["人参", "五灵脂"])[0]["kind"] == "十九畏"
    # 白附子不属乌头类
    assert find_conflicts(["白附子", "半夏"]) == []
    assert find_conflicts(["柴胡", "白芍", "甘草"]) == []
