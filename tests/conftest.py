import pytest

from wenzhen import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "CSRF_ENABLED": False,
        "AUTO_BACKUP": False,
    })
    app.instance_path = str(tmp_path)
    return app


@pytest.fixture
def anon(app):
    return app.test_client()


@pytest.fixture
def client(app):
    """已完成首次设置并登录的客户端。"""
    client = app.test_client()
    resp = client.post("/setup", data={
        "clinic_name": "王艳霞中医门诊", "doctor_name": "王艳霞",
        "username": "wang", "display_name": "王医生",
        "password": "secret1", "password2": "secret1",
    })
    assert resp.status_code == 302
    return client


@pytest.fixture
def db(app):
    from wenzhen.db import connect
    conn = connect(app.config["DATABASE"])
    yield conn
    conn.close()


def _follow(client, resp):
    """打开跳转后的页面（消耗提示消息），返回新记录的 id。"""
    location = resp.headers["Location"]
    client.get(location)
    return int(location.rstrip("/").split("/")[-1])


def make_patient(client, **fields):
    data = {"name": "张三", "gender": "男", "phone": "13800000000"}
    data.update(fields)
    resp = client.post("/patients/new", data=data)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return _follow(client, resp)


def make_visit(client, patient_id, herbs=(), **fields):
    data = {
        "visit_date": "2026-09-01", "visit_type": "初诊",
        "chief_complaint": "胃脘胀痛3月", "tcm_disease": "胃脘痛",
        "syndrome": "肝胃不和证", "tongue_body": "淡红", "pulse": "弦",
        "formula_name": "柴胡疏肝散加减", "dose_count": "7",
        "usage": "水煎服，日一剂", "fee": "120",
        "herb_name": [h[0] for h in herbs],
        "herb_dose": [h[1] for h in herbs],
        "herb_unit": [h[2] if len(h) > 2 else "g" for h in herbs],
        "herb_note": [h[3] if len(h) > 3 else "" for h in herbs],
    }
    data.update(fields)
    resp = client.post(f"/patients/{patient_id}/visits/new", data=data)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return _follow(client, resp)
