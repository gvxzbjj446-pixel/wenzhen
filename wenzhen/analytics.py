"""问诊和理疗的统计口径；收费先分别汇总，避免明细关联造成重复。"""

from .db import get_db
from .therapy_data import OUTCOMES


def _pain_summary(rows, before, after):
    """只比较同一记录内两次均有效的 0–10 分评分；零分不是缺失。"""
    pairs = [(r[before], r[after]) for r in rows
             if all(isinstance(r[k], (int, float)) and 0 <= r[k] <= 10
                    for k in (before, after))]
    count = len(pairs)
    return {
        "total": len(rows), "paired": count, "missing": len(rows) - count,
        "before": sum(a for a, _ in pairs) / count if count else None,
        "after": sum(b for _, b in pairs) / count if count else None,
        "change": sum(b - a for a, b in pairs) / count if count else None,
    }


def build_analytics(start, end):
    """包含起止日；服务患者指期内有问诊或实际治疗记录的去重患者。"""
    conn = get_db()
    params = (start, end)
    visits = dict(conn.execute(
        """SELECT COUNT(*) AS visits, COUNT(DISTINCT patient_id) AS patients,
                  COALESCE(SUM(visit_type = '初诊'), 0) AS first_visits,
                  COALESCE(SUM(fee), 0) AS revenue
           FROM visits WHERE visit_date BETWEEN ? AND ?""", params).fetchone())
    sessions = dict(conn.execute(
        """SELECT COUNT(*) AS sessions, COUNT(DISTINCT c.patient_id) AS patients,
                  COALESCE(SUM(s.fee), 0) AS revenue
           FROM therapy_sessions s JOIN therapy_courses c ON c.id = s.course_id
           WHERE s.session_date BETWEEN ? AND ?""", params).fetchone())
    new_courses = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(fee), 0) AS revenue
           FROM therapy_courses WHERE start_date BETWEEN ? AND ?""", params).fetchone()
    people = conn.execute(
        """SELECT gender, birth_date FROM patients WHERE id IN (
               SELECT patient_id FROM visits WHERE visit_date BETWEEN ? AND ?
               UNION
               SELECT c.patient_id FROM therapy_sessions s
               JOIN therapy_courses c ON c.id = s.course_id
               WHERE s.session_date BETWEEN ? AND ?
           )""", params * 2).fetchall()
    summary = {
        "visits": visits["visits"], "sessions": sessions["sessions"],
        "patients": len(people), "visit_patients": visits["patients"],
        "therapy_patients": sessions["patients"], "first_visits": visits["first_visits"],
        "new_courses": new_courses["n"],
        "visit_fee": visits["revenue"], "session_fee": sessions["revenue"],
        "course_fee": new_courses["revenue"],
        "therapy_fee": sessions["revenue"] + new_courses["revenue"],
        "revenue": visits["revenue"] + sessions["revenue"] + new_courses["revenue"],
    }
    # 三类事件独立进入流水。疗程预收归开立日，不跟随每次治疗再次入账。
    monthly = conn.execute(
        """WITH events AS (
               SELECT visit_date AS event_date, patient_id, 1 AS visits, 0 AS sessions,
                      fee AS visit_fee, 0 AS session_fee, 0 AS course_fee
               FROM visits WHERE visit_date BETWEEN ? AND ?
               UNION ALL
               SELECT s.session_date, c.patient_id, 0, 1, 0, s.fee, 0
               FROM therapy_sessions s JOIN therapy_courses c ON c.id = s.course_id
               WHERE s.session_date BETWEEN ? AND ?
               UNION ALL
               SELECT start_date, NULL, 0, 0, 0, 0, fee
               FROM therapy_courses WHERE start_date BETWEEN ? AND ?
           )
           SELECT substr(event_date, 1, 7) AS month, SUM(visits) AS visits,
                  SUM(sessions) AS sessions, COUNT(DISTINCT patient_id) AS patients,
                  SUM(visit_fee) AS visit_fee, SUM(session_fee) AS session_fee,
                  SUM(course_fee) AS course_fee,
                  SUM(visit_fee + session_fee + course_fee) AS revenue
           FROM events GROUP BY month ORDER BY month""", params * 3).fetchall()
    types = conn.execute(
        """SELECT i.therapy AS label, COUNT(DISTINCT s.id) AS hits
           FROM therapy_session_items i JOIN therapy_sessions s ON s.id = i.session_id
           WHERE s.session_date BETWEEN ? AND ? AND trim(i.therapy) != ''
           GROUP BY i.therapy ORDER BY hits DESC, label""", params).fetchall()
    without_items = conn.execute(
        """SELECT COUNT(*) FROM therapy_sessions s
           WHERE s.session_date BETWEEN ? AND ? AND NOT EXISTS (
               SELECT 1 FROM therapy_session_items i
               WHERE i.session_id = s.id AND trim(i.therapy) != ''
           )""", params).fetchone()[0]
    ended = conn.execute(
        """SELECT status, outcome, initial_pain, final_pain FROM therapy_courses
           WHERE status IN ('已完成', '已中止') AND end_date BETWEEN ? AND ?""",
        params).fetchall()
    completed = [r for r in ended if r["status"] == "已完成"]
    outcomes = {label: 0 for label in OUTCOMES}
    for row in completed:
        if row["outcome"] in outcomes:
            outcomes[row["outcome"]] += 1
    evaluated = sum(outcomes.values())
    courses = {
        "ended": len(ended), "completed": len(completed),
        "stopped": len(ended) - len(completed),
        "completion_rate": len(completed) / len(ended) * 100 if ended else None,
        "outcomes": outcomes, "evaluated": evaluated,
        "unassessed": len(completed) - evaluated,
    }
    pain_rows = conn.execute(
        "SELECT pain_before, pain_after FROM therapy_sessions"
        " WHERE session_date BETWEEN ? AND ?", params).fetchall()
    return {
        "summary": summary, "monthly": monthly, "people": people,
        "types": types, "without_items": without_items, "courses": courses,
        "session_pain": _pain_summary(pain_rows, "pain_before", "pain_after"),
        "course_pain": _pain_summary(completed, "initial_pain", "final_pain"),
    }


def analytics_csv_rows(data):
    """长表汇总；导出与页面共享数据，显式保留分母及缺失情况。"""
    summary = data["summary"]
    for key, label, unit, note in (
        ("visits", "问诊人次", "人次", "按问诊日期"),
        ("sessions", "理疗次数", "次", "按治疗日期；每条治疗记录计一次"),
        ("patients", "服务患者", "人", "期内问诊与实际治疗患者合并去重"),
        ("visit_patients", "问诊患者", "人", "期内问诊患者去重"),
        ("therapy_patients", "理疗患者", "人", "期内实际治疗患者去重"),
        ("first_visits", "初诊人次", "人次", "按问诊记录的初诊标记"),
        ("new_courses", "新增疗程", "个", "按开立日期"),
        ("visit_fee", "问诊收费", "元", "按问诊日期"),
        ("session_fee", "单次理疗收费", "元", "按治疗日期"),
        ("course_fee", "疗程预收", "元", "按开立日期；仅计一次"),
        ("revenue", "登记收费合计", "元", "问诊收费＋单次理疗收费＋疗程预收"),
    ):
        value = f"{summary[key]:.2f}" if unit == "元" else summary[key]
        yield ["期间汇总", label, value, unit, "", note]
    for row in data["monthly"]:
        for key, label, unit in (
            ("visits", "问诊人次", "人次"), ("sessions", "理疗次数", "次"),
            ("patients", "服务患者", "人"), ("visit_fee", "问诊收费", "元"),
            ("session_fee", "单次理疗收费", "元"), ("course_fee", "疗程预收", "元"),
            ("revenue", "登记收费合计", "元"),
        ):
            value = f"{row[key]:.2f}" if unit == "元" else row[key]
            note = "当月去重；跨月人数不可相加" if key == "patients" else "仅统计筛选日期内记录"
            yield ["月度 " + row["month"], label, value, unit, "", note]
    for row in data["types"]:
        yield ["理疗项目", row["label"], row["hits"], "次", summary["sessions"],
               "同次治疗同项目合并计一次；一次治疗可含多个项目"]
    yield ["理疗项目", "未记录项目", data["without_items"], "次", summary["sessions"],
           "没有有效项目名称的治疗记录"]
    courses = data["courses"]
    for key, label in (("ended", "期内结束疗程"), ("completed", "已完成"), ("stopped", "已中止")):
        yield ["疗程完成情况", label, courses[key], "个", courses["ended"], "按结束日期；已完成＋已中止"]
    yield ["疗程完成情况", "完成比例", round(courses["completion_rate"], 1)
           if courses["completion_rate"] is not None else "", "%", courses["ended"],
           "已完成 / 期内结束疗程；无结束疗程时留空"]
    for label, count in [*courses["outcomes"].items(), ("未评价", courses["unassessed"])]:
        yield ["完成疗程评价", label, count, "个", courses["completed"], "期内结束且状态为已完成的疗程"]
    for key, label in (("session_pain", "单次治疗疼痛"), ("course_pain", "完成疗程疼痛")):
        pain = data[key]
        for field, title in (("paired", "配对完整"), ("missing", "未形成有效配对")):
            yield [label, title, pain[field], "条", pain["total"], "同一记录前后均为有效 0–10 分才纳入"]
        for field, title in (("before", "平均治疗前评分"), ("after", "平均治疗后评分"), ("change", "平均变化（后－前）")):
            yield [label, title, round(pain[field], 2) if pain[field] is not None else "",
                   "分", pain["paired"], "只用有效配对；无配对时留空；负值表示疼痛下降"]
