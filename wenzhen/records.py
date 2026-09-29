"""各页面共用的数据查询。"""

from flask import abort

from .db import get_db
from .herbs import COMMON_HERBS

# 允许写入处方明细的表：表名 → 外键列
_ITEM_TABLES = {"prescription_items": "visit_id", "formula_items": "formula_id"}


def get_patient(patient_id):
    row = get_db().execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def get_visit(visit_id):
    row = get_db().execute("SELECT * FROM visits WHERE id = ?", (visit_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def get_formula(formula_id):
    row = get_db().execute("SELECT * FROM formulas WHERE id = ?", (formula_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def previous_visit(patient_id, visit=None):
    """患者在 visit 之前的最近一次就诊；visit 为空时返回最近一次就诊。"""
    if visit is None:
        return get_db().execute(
            "SELECT * FROM visits WHERE patient_id = ?"
            " ORDER BY visit_date DESC, id DESC LIMIT 1",
            (patient_id,),
        ).fetchone()
    return get_db().execute(
        "SELECT * FROM visits WHERE patient_id = ?"
        " AND (visit_date < ? OR (visit_date = ? AND id < ?))"
        " ORDER BY visit_date DESC, id DESC LIMIT 1",
        (patient_id, visit["visit_date"], visit["visit_date"], visit["id"]),
    ).fetchone()


def _load_items(table, ids, chunk=500):
    fk = _ITEM_TABLES[table]
    grouped = {i: [] for i in ids}
    # 分批查询：SQLite 单条语句的参数个数有上限，导出多年记录时会超出
    for start in range(0, len(ids), chunk):
        part = ids[start:start + chunk]
        rows = get_db().execute(
            f"SELECT {fk} AS owner, herb, dose, unit, note FROM {table}"
            f" WHERE {fk} IN ({','.join('?' * len(part))}) ORDER BY position, id",
            part,
        )
        for row in rows:
            grouped[row["owner"]].append(
                {"herb": row["herb"], "dose": row["dose"], "unit": row["unit"], "note": row["note"]}
            )
    return grouped


def visit_items(visit_id):
    return _load_items("prescription_items", [visit_id])[visit_id]


def items_for_visits(visit_ids):
    return _load_items("prescription_items", list(visit_ids))


def formula_items(formula_id):
    return _load_items("formula_items", [formula_id])[formula_id]


def items_for_formulas(formula_ids):
    return _load_items("formula_items", list(formula_ids))


def save_items(table, owner_id, items):
    fk = _ITEM_TABLES[table]
    conn = get_db()
    conn.execute(f"DELETE FROM {table} WHERE {fk} = ?", (owner_id,))
    conn.executemany(
        f"INSERT INTO {table} ({fk}, position, herb, dose, unit, note) VALUES (?, ?, ?, ?, ?, ?)",
        [(owner_id, i, it["herb"], it["dose"], it["unit"], it["note"])
         for i, it in enumerate(items)],
    )


def herb_suggestions():
    """药名联想：常用药在前，其后是本诊所用过的其他药名。"""
    used = [row[0] for row in get_db().execute(
        "SELECT herb FROM prescription_items UNION SELECT herb FROM formula_items"
    )]
    return list(dict.fromkeys(list(COMMON_HERBS) + used))


def formula_payload():
    """供处方编辑器“引用方剂”使用的方剂数据。"""
    formulas = get_db().execute(
        "SELECT id, name, source, indication, usage, notes FROM formulas ORDER BY name"
    ).fetchall()
    items = items_for_formulas([f["id"] for f in formulas])
    return [
        {"id": f["id"], "name": f["name"], "source": f["source"],
         "indication": f["indication"], "usage": f["usage"], "notes": f["notes"],
         "items": items[f["id"]]}
        for f in formulas
    ]


def due_followups(start, end):
    """预约复诊日期在 [start, end] 之间、且之后未再来诊的记录。"""
    return get_db().execute(
        """
        SELECT v.id, v.patient_id, v.visit_date, v.next_visit_date, v.tcm_disease,
               v.syndrome, p.name, p.gender, p.birth_date, p.phone
        FROM visits v JOIN patients p ON p.id = v.patient_id
        WHERE v.next_visit_date != '' AND v.next_visit_date BETWEEN ? AND ?
          AND NOT EXISTS (
              SELECT 1 FROM visits later
              WHERE later.patient_id = v.patient_id
                AND (later.visit_date > v.visit_date
                     OR (later.visit_date = v.visit_date AND later.id > v.id)))
        ORDER BY v.next_visit_date, p.name
        """,
        (start, end),
    ).fetchall()
