"""方剂库：常用方与经验方模板，开处方时可一键引用。"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .compat import rules_for_js
from .db import get_db
from .herbs import HERB_NOTES, UNITS
from .records import (formula_items, get_formula, get_visit, herb_suggestions,
                      items_for_formulas, save_items, visit_items)
from .utils import escape_like, paginate, parse_herb_rows

bp = Blueprint("formulas", __name__, url_prefix="/formulas")

FORMULA_FIELDS = ("name", "source", "indication", "usage", "notes")


def parse_formula_form(form, formula_id=None):
    data = {key: form.get(key, "").strip() for key in FORMULA_FIELDS}
    items, errors = parse_herb_rows(form)
    if not data["name"]:
        errors.insert(0, "请填写方名。")
    elif len(data["name"]) > 50:
        errors.insert(0, "方名不超过 50 个字符。")
    elif get_db().execute(
        "SELECT 1 FROM formulas WHERE name = ? AND id != ?", (data["name"], formula_id or 0)
    ).fetchone():
        errors.insert(0, "方剂库中已有同名方剂。")
    if not items:
        errors.append("请至少录入一味药。")
    return data, items, errors


def render_form(formula, items, errors, is_new):
    return render_template(
        "formulas/form.html", formula=formula, items=items, errors=errors, is_new=is_new,
        herbs=herb_suggestions(), units=UNITS, herb_notes=HERB_NOTES,
        compat_rules=rules_for_js(),
    )


@bp.route("/")
def index():
    q = request.args.get("q", "").strip()
    source = request.args.get("source", "").strip()
    clauses, params = [], []
    if q:
        like = f"%{escape_like(q)}%"
        clauses.append(
            "(name LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\'"
            " OR indication LIKE ? ESCAPE '\\'"
            " OR id IN (SELECT formula_id FROM formula_items WHERE herb LIKE ? ESCAPE '\\'))"
        )
        params.extend([like] * 4)
    if source:
        clauses.append("source = ?")
        params.append(source)
    conn = get_db()
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    formulas = conn.execute("SELECT * FROM formulas" + where + " ORDER BY name, id", params).fetchall()
    items = items_for_formulas(f["id"] for f in formulas)
    total = conn.execute("SELECT COUNT(*) FROM formulas").fetchone()[0]
    sources = conn.execute(
        "SELECT source, COUNT(*) AS count FROM formulas WHERE source != '' GROUP BY source ORDER BY source"
    ).fetchall()
    return render_template(
        "formulas/index.html", formulas=formulas, items=items, q=q,
        source=source, sources=sources, total=total,
    )


def copied_formula_name(name):
    """提供不重名且符合长度限制的副本名，不写入数据库。"""
    names = {row[0] for row in get_db().execute("SELECT name FROM formulas")}
    number = 1
    while True:
        suffix = "（副本）" if number == 1 else f"（副本 {number}）"
        candidate = name[:50 - len(suffix)] + suffix
        if candidate not in names:
            return candidate
        number += 1


@bp.route("/<int:formula_id>")
def detail(formula_id):
    return render_template(
        "formulas/detail.html", formula=get_formula(formula_id), items=formula_items(formula_id),
    )


@bp.route("/<int:formula_id>/use")
def use(formula_id):
    """只选择患者；实际问诊和处方由医师检查后另行保存。"""
    from .patients import search_clause

    formula = get_formula(formula_id)
    q = request.args.get("q", "").strip()
    where, params = search_clause(q)
    conn = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM patients p {where}", params).fetchone()[0]
    pager = paginate(total, request.args.get("page", 1, type=int), 30)
    patients = conn.execute(
        f"SELECT p.* FROM patients p {where} ORDER BY p.id DESC LIMIT ? OFFSET ?",
        params + [30, pager["offset"]],
    ).fetchall()
    return render_template(
        "formulas/use.html", formula=formula, patients=patients, q=q, pager=pager,
    )


@bp.route("/new", methods=("GET", "POST"))
def new():
    if request.method == "POST":
        formula, items, errors = parse_formula_form(request.form)
        if not errors:
            conn = get_db()
            cur = conn.execute(
                f"INSERT INTO formulas ({', '.join(FORMULA_FIELDS)})"
                f" VALUES ({', '.join('?' * len(FORMULA_FIELDS))})",
                [formula[key] for key in FORMULA_FIELDS],
            )
            save_items("formula_items", cur.lastrowid, items)
            conn.commit()
            flash(f"已保存方剂：{formula['name']}。", "success")
            return redirect(url_for("formulas.index"))
        return render_form(formula, items, errors, is_new=True)

    formula = {key: "" for key in FORMULA_FIELDS}
    items = []
    # 复制模板只预填表单，提交失败时保留用户输入，不重新载入原方。
    if "from_formula" in request.args:
        if "from_visit" in request.args:
            abort(400)
        source_id = request.args.get("from_formula", type=int)
        if source_id is None:
            abort(404)
        original = get_formula(source_id)
        formula = {key: original[key] for key in FORMULA_FIELDS}
        formula["name"] = copied_formula_name(original["name"])
        items = formula_items(source_id)
    # 从某次就诊的处方另存为经验方
    visit_id = request.args.get("from_visit", type=int)
    if visit_id:
        visit = get_visit(visit_id)
        formula.update(name=visit["formula_name"], usage=visit["usage"],
                       indication=visit["syndrome"])
        items = visit_items(visit_id)
    return render_form(formula, items, [], is_new=True)


@bp.route("/<int:formula_id>/edit", methods=("GET", "POST"))
def edit(formula_id):
    formula = get_formula(formula_id)
    if request.method == "POST":
        data, items, errors = parse_formula_form(request.form, formula_id)
        if not errors:
            conn = get_db()
            conn.execute(
                f"UPDATE formulas SET {', '.join(f'{key} = ?' for key in FORMULA_FIELDS)},"
                " updated_at = datetime('now', 'localtime') WHERE id = ?",
                [data[key] for key in FORMULA_FIELDS] + [formula_id],
            )
            save_items("formula_items", formula_id, items)
            conn.commit()
            flash(f"已更新方剂：{data['name']}。", "success")
            return redirect(url_for("formulas.index"))
        data["id"] = formula_id
        return render_form(data, items, errors, is_new=False)
    return render_form(dict(formula), formula_items(formula_id), [], is_new=False)


@bp.route("/<int:formula_id>/delete", methods=("POST",))
def delete(formula_id):
    formula = get_formula(formula_id)
    conn = get_db()
    conn.execute("DELETE FROM formulas WHERE id = ?", (formula_id,))
    conn.commit()
    flash(f"已删除方剂：{formula['name']}。", "success")
    return redirect(url_for("formulas.index"))
