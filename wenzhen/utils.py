"""通用小工具：日期、年龄、数字格式、处方行解析等。"""

from datetime import date, datetime
from math import ceil

from jinja2 import Undefined

MAX_HERBS = 60


def parse_date(value):
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def age_text(birth_date, on=None):
    """按出生日期计算年龄，3 岁以下精确到月。"""
    born = parse_date(birth_date)
    if not born:
        return ""
    on = parse_date(on) or date.today()
    if on < born:
        return ""
    months = (on.year - born.year) * 12 + on.month - born.month - (on.day < born.day)
    if months < 1:
        return "不足1个月"
    if months < 12:
        return f"{months}个月"
    years, rest = divmod(months, 12)
    if years < 3 and rest:
        return f"{years}岁{rest}个月"
    return f"{years}岁"


def format_number(value):
    """9.0 → "9"，4.50 → "4.5"；空值返回空字符串。"""
    if value is None or isinstance(value, Undefined) or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def format_money(value):
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def record_no(patient_id):
    return f"{int(patient_id):06d}"


def herb_text(items, sep="、"):
    """处方明细转为一行文字，如：黄芪30g、砂仁6g（后下）。"""
    parts = []
    for item in items:
        text = item["herb"]
        if item.get("dose") not in (None, ""):
            text += format_number(item["dose"]) + (item.get("unit") or "")
        if item.get("note"):
            text += f"（{item['note']}）"
        parts.append(text)
    return sep.join(parts)


def tongue_pulse(visit):
    """舌脉合写，如：舌淡红，苔薄白，脉弦细。"""
    parts = []
    for key, prefix in (("tongue_body", "舌"), ("tongue_coating", "苔"), ("pulse", "脉")):
        if visit[key]:
            parts.append(prefix + visit[key])
    return "，".join(parts)


def total_grams(items):
    return sum(
        float(item["dose"]) for item in items
        if item.get("dose") not in (None, "") and (item.get("unit") or "g") == "g"
    )


def escape_like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def safe_next(target):
    """只允许站内相对路径，防止登录后被跳转到外部网站。"""
    if target and target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return None


def paginate(total, page, per_page):
    pages = max(ceil(total / per_page), 1)
    page = min(max(page, 1), pages)
    return {"page": page, "pages": pages, "total": total,
            "offset": (page - 1) * per_page, "per_page": per_page}


def _at(values, index):
    return values[index] if index < len(values) else ""


def parse_herb_rows(form):
    """从表单读取处方药物行（herb_name / herb_dose / herb_unit / herb_note）。"""
    names = form.getlist("herb_name")
    doses = form.getlist("herb_dose")
    units = form.getlist("herb_unit")
    notes = form.getlist("herb_note")
    items, errors = [], []
    for i, raw_name in enumerate(names):
        herb = raw_name.strip()[:30]
        if not herb:
            continue
        dose_raw = _at(doses, i).strip()
        dose = None
        if dose_raw:
            try:
                dose = float(dose_raw)
            except ValueError:
                dose = None
            if dose is None or not 0 < dose < 10000:
                errors.append(f"「{herb}」的剂量「{dose_raw}」不是有效数字。")
                dose = dose_raw  # 保留原输入，方便修改
        items.append({
            "herb": herb,
            "dose": dose,
            "unit": (_at(units, i).strip() or "g")[:6],
            "note": _at(notes, i).strip()[:20],
        })
    if len(items) > MAX_HERBS:
        errors.append(f"一张处方最多 {MAX_HERBS} 味药。")
    return items, errors


def register_template_filters(app):
    app.jinja_env.filters.update(
        num=format_number,
        money=format_money,
        record_no=record_no,
        herb_text=herb_text,
        tongue_pulse=tongue_pulse,
    )
    app.jinja_env.globals.update(age_text=age_text)
