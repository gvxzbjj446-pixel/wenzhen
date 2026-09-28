import csv
import io
import json
import os
import sqlite3
import time
import zipfile
from datetime import date, timedelta

import pytest

from conftest import make_patient, make_visit
from wenzhen import backup
from wenzhen.db import get_db, get_setting, set_setting


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield app


def read_zip(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def csv_rows(data):
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


# ---------------------------------------------------------------- 备份包内容

def test_full_package_contents(app, client, tmp_path):
    pid = make_patient(client, name="李秀兰", allergies="青霉素")
    make_visit(client, pid, herbs=[("柴胡", "9"), ("砂仁", "6", "g", "后下")])
    target = tmp_path / "package.zip"
    with app.app_context():
        manifest = backup.create_package(str(target), kind="export", full=True)
    files = read_zip(target)

    assert set(files) == {
        "manifest.json", "wenzhen.sqlite3", "README.txt", "data.json",
        "csv/patients.csv", "csv/visits.csv", "csv/prescription_items.csv",
        "csv/formulas.csv", "csv/formula_items.csv",
        "csv/therapy_courses.csv", "csv/therapy_course_items.csv", "csv/therapy_sessions.csv",
        "csv/therapy_session_items.csv", "csv/therapy_types.csv",
    }
    stored = json.loads(files["manifest.json"])
    assert stored == manifest
    assert stored["format"] == "wenzhen-backup" and stored["kind"] == "export"
    assert stored["counts"]["patients"] == 1 and stored["counts"]["prescription_items"] == 2
    # 每个文件都有校验值且与内容一致
    import hashlib
    for name, meta in stored["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == meta["sha256"]

    readme = files["README.txt"].decode("utf-8-sig")
    assert "患者 1 人" in readme and "从备份恢复" in readme and "\r\n" in readme

    patients = csv_rows(files["csv/patients.csv"])
    assert patients[0][:3] == ["患者ID", "病历号", "姓名"]
    assert patients[1][1:3] == [f"{pid:06d}", "李秀兰"]
    items = csv_rows(files["csv/prescription_items.csv"])
    assert items[0] == ["就诊ID", "序号", "药名", "剂量", "单位", "脚注"]
    assert [row[1:] for row in items[1:]] == [["1", "柴胡", "9.0", "g", ""], ["2", "砂仁", "6.0", "g", "后下"]]
    visits = csv_rows(files["csv/visits.csv"])
    assert "主诉" in visits[0] and "胃脘胀痛3月" in visits[1]

    data = json.loads(files["data.json"])
    assert data["format"] == "wenzhen-data" and data["clinic"]["clinic_name"] == "王艳霞中医门诊"
    patient = data["patients"][0]
    assert patient["name"] == "李秀兰" and patient["allergies"] == "青霉素"
    visit = patient["visits"][0]
    assert visit["syndrome"] == "肝胃不和证"
    assert visit["prescription"]["formula_name"] == "柴胡疏肝散加减"
    assert visit["prescription"]["items"][1] == {"herb": "砂仁", "dose": 6.0, "unit": "g", "note": "后下"}
    assert data["field_labels"]["visit"]["tongue_body"] == "舌质"
    assert any(f["name"] == "桂枝汤" and f["items"] for f in data["formulas"])
    # 登录账户只在数据库中，不写入 JSON / CSV
    assert b"password_hash" not in files["data.json"]


def test_auto_package_is_database_only(ctx, tmp_path):
    target = tmp_path / "auto.zip"
    backup.create_package(str(target), kind="auto", full=False)
    assert set(read_zip(target)) == {"manifest.json", "wenzhen.sqlite3", "README.txt"}


# ---------------------------------------------------------------- 校验与恢复

def test_inspect_zip_and_legacy_sqlite(app, client, tmp_path):
    make_patient(client)
    with app.app_context():
        package = tmp_path / "p.zip"
        backup.create_package(str(package))
        info = backup.inspect_backup(str(package))
        assert (info["patients"], info["visits"]) == (1, 0)
        assert info["created_at"] and info["app_version"]

        legacy = tmp_path / "old.sqlite3"
        backup.snapshot_database(str(legacy))
        info = backup.inspect_backup(str(legacy))
        assert info["patients"] == 1 and info["created_at"] == ""


def _rewrite_zip(source, target, replace=None, drop=()):
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w") as dst:
        for name in src.namelist():
            if name in drop:
                continue
            data = src.read(name)
            dst.writestr(name, (replace or {}).get(name, data))


def test_damaged_or_foreign_packages_are_rejected(ctx, tmp_path):
    good = tmp_path / "good.zip"
    backup.create_package(str(good))

    # 数据库被改动：校验值不符
    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE t (x)")
    conn.close()
    tampered = tmp_path / "tampered.zip"
    _rewrite_zip(good, tampered, replace={"wenzhen.sqlite3": other.read_bytes()})
    with pytest.raises(ValueError, match="校验失败"):
        backup.inspect_backup(str(tampered))

    # 来自更新版本的软件
    manifest = json.loads(read_zip(good)["manifest.json"])
    manifest["schema_version"] = 99
    newer = tmp_path / "newer.zip"
    _rewrite_zip(good, newer, replace={"manifest.json": json.dumps(manifest).encode()})
    with pytest.raises(ValueError, match="更新版本"):
        backup.inspect_backup(str(newer))

    # 缺少数据库
    missing = tmp_path / "missing.zip"
    _rewrite_zip(good, missing, drop=("wenzhen.sqlite3",))
    with pytest.raises(ValueError, match="不完整"):
        backup.inspect_backup(str(missing))

    # 别的 zip 文件
    foreign = tmp_path / "foreign.zip"
    with zipfile.ZipFile(foreign, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(ValueError):
        backup.inspect_backup(str(foreign))

    # 文件损坏：在整个文件的多个位置各改一个字节，都应给出“损坏”之类的提示，而不是程序出错
    raw = good.read_bytes()
    broken = tmp_path / "broken.zip"
    for i in range(25):
        damaged = bytearray(raw)
        damaged[len(raw) * i // 25] ^= 0xFF
        broken.write_bytes(bytes(damaged))
        try:
            info = backup.inspect_backup(str(broken))
        except ValueError:
            continue
        # 改动落在不影响内容的位置（如时间戳）时，备份仍可正常读取
        assert info["patients"] == 0
    # 截断的文件
    broken.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(ValueError):
        backup.inspect_backup(str(broken))
    # 列表读取损坏的备份时不出错
    backups_dir = tmp_path / "list"
    backups_dir.mkdir()
    damaged = bytearray(raw)
    damaged[len(raw) // 3] ^= 0xFF
    (backups_dir / "manual-20260928-101010.zip").write_bytes(bytes(damaged))
    assert len(backup.list_backups(str(backups_dir))) == 1


def test_restore_keeps_machine_settings(app, client, db, tmp_path):
    make_patient(client, name="甲")
    with app.app_context():
        package = tmp_path / "p.zip"
        backup.create_package(str(package))
        set_setting("backup_mirror_dir", str(tmp_path))
        set_setting("clinic_name", "改过的名字")
        get_db().commit()
    make_patient(client, name="乙")

    with app.app_context():
        safety = backup.restore_backup(str(package))
        assert os.path.basename(safety).startswith("before-restore-")
        assert get_setting("backup_mirror_dir") == str(tmp_path)   # 本机设置保留
        assert get_setting("clinic_name") == "王艳霞中医门诊"       # 其余随备份恢复
    assert [r[0] for r in db.execute("SELECT name FROM patients")] == ["甲"]
    assert json.loads(read_zip(safety)["manifest.json"])["counts"]["patients"] == 2


# ---------------------------------------------------------------- 自动备份与保留

def test_daily_backup_once_a_day_and_refresh_on_change(app, ctx, tmp_path):
    first = backup.daily_backup()
    assert first and os.path.basename(first) == f"auto-{date.today():%Y%m%d}.zip"
    assert backup.daily_backup() is None                 # 当天已有
    assert backup.daily_backup(refresh=True) is None     # 数据没有变化
    past = time.time() - 60
    os.utime(first, (past, past))                        # 模拟之后又修改了数据
    assert backup.daily_backup(refresh=True) == first


def test_retention_keeps_recent_days_and_one_per_month(tmp_path):
    today = date(2026, 9, 28)
    folder = tmp_path / "b"
    folder.mkdir()
    names = []
    day = date(2026, 5, 1)
    while day <= today:
        names.append(f"auto-{day:%Y%m%d}.zip")
        day += timedelta(days=1)
    names += ["wenzhen-20260410.sqlite3", "wenzhen-20260420.sqlite3",   # 1.1.0 版的每日备份
              "manual-20260502-101010.zip", "before-restore-20260503-080000.zip", "notes.txt"]
    for name in names:
        (folder / name).write_bytes(b"x")

    backup.prune_backups(str(folder), today, keep_days=30)
    kept = sorted(os.listdir(folder))
    autos = [n for n in kept if backup.classify(n) and backup.classify(n)[0] == "auto"]
    recent = [n for n in autos if backup.classify(n)[1] > today - timedelta(days=30)]
    assert len(recent) == 30
    older = sorted(set(autos) - set(recent))
    assert older == ["auto-20260531.zip", "auto-20260630.zip", "auto-20260731.zip",
                     "auto-20260829.zip", "wenzhen-20260420.sqlite3"]
    for name in ("manual-20260502-101010.zip", "before-restore-20260503-080000.zip", "notes.txt"):
        assert name in kept


def test_classify_and_backup_path(ctx):
    assert backup.classify("auto-20260928.zip") == ("auto", date(2026, 9, 28))
    assert backup.classify("manual-20260928-101500.zip")[0] == "manual"
    assert backup.classify("wenzhen-before-restore-20260928-101500.sqlite3")[0] == "restore"
    assert backup.classify("auto-20261340.zip") is None
    assert backup.classify("evil.zip") is None
    assert backup.backup_path("../auto-20260928.zip") is None
    assert backup.backup_path("auto-20260928.zip") is None   # 不存在


# ---------------------------------------------------------------- 外部备份文件夹与提醒

def test_mirror_copies_and_reports_failures(app, client, tmp_path):
    make_patient(client)
    usb = tmp_path / "usb"
    usb.mkdir()
    with app.app_context():
        with pytest.raises(ValueError):
            backup.set_mirror_root(str(tmp_path / "missing"))
        assert backup.set_mirror_root(str(usb)) == str(usb / backup.MIRROR_SUBDIR)
        assert backup.status()["remind_offsite"] is False

        usb_path = str(usb)
        os.rename(usb_path, usb_path + "-unplugged")              # 拔掉 U 盘
        assert backup.manual_backup() is not None                # 本机备份照常
        status = backup.status()
        assert "找不到文件夹" in status["mirror_error"]

        os.rename(usb_path + "-unplugged", usb_path)
        backup.manual_backup()
        assert backup.status()["mirror_error"] == ""
        # 拔掉期间漏掉的那一份也补上了（同一秒内的两次备份不会互相覆盖）
        assert len(list((usb / backup.MIRROR_SUBDIR).glob("manual-*.zip"))) == 2

        backup.set_mirror_root("")
        assert backup.status()["mirror_root"] == ""


@pytest.mark.parametrize("days_since_offsite", [None, 30])
def test_offsite_reminder_is_only_on_backup_and_settings_pages(app, client, days_since_offsite):
    with app.app_context():
        assert backup.status()["remind_offsite"] is False   # 还没有数据
    make_patient(client)
    with app.app_context():
        if days_since_offsite is not None:
            set_setting("last_offsite_backup", (date.today() - timedelta(days=days_since_offsite)).isoformat())
        assert backup.status()["remind_offsite"] is True
    home = client.get("/").get_data(as_text=True)
    assert "备份到这台电脑以外" not in home
    assert "去备份 →" not in home
    for path in ("/settings", "/backups/"):
        response = client.get(path)
        assert response.status_code == 200
        assert "备份到这台电脑以外" in response.get_data(as_text=True)
    with app.app_context():
        backup.mark_offsite()
        assert backup.status()["remind_offsite"] is False
    assert "备份到这台电脑以外" not in client.get("/").get_data(as_text=True)


# ---------------------------------------------------------------- 页面

def test_backup_page_backup_now_and_download(client, tmp_path):
    make_patient(client)
    assert "还没有备份" in client.get("/backups/").get_data(as_text=True)
    resp = client.post("/backups/now", follow_redirects=True)
    page = resp.get_data(as_text=True)
    assert "已备份：manual-" in page and "手动备份" in page and "患者 1 人" in page
    name = next(p.name for p in (tmp_path / "backups").iterdir())
    download = client.get(f"/backups/files/{name}")
    assert download.status_code == 200 and download.data.startswith(b"PK")
    assert "filename*=UTF-8''" in download.headers["Content-Disposition"]
    assert client.get("/backups/files/..%2Fsecret").status_code == 404
    assert client.get("/backups/files/nothing.zip").status_code == 404


def test_export_download_is_full_package(client):
    make_patient(client)
    resp = client.get("/backups/export")
    assert resp.status_code == 200 and resp.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        assert "data.json" in zf.namelist()
    assert "%E6%95%B0%E6%8D%AE%E5%A4%87%E4%BB%BD" in resp.headers["Content-Disposition"]  # “数据备份”


def test_restore_from_list_logs_out(client, db, tmp_path):
    make_patient(client, name="甲")
    client.post("/backups/now")
    make_patient(client, name="乙")
    name = next(p.name for p in (tmp_path / "backups").iterdir())
    resp = client.post(f"/backups/files/{name}/restore", follow_redirects=True)
    assert "数据已从备份恢复" in resp.get_data(as_text=True)
    assert [r[0] for r in db.execute("SELECT name FROM patients")] == ["甲"]
    assert client.get("/").status_code == 302   # 需要重新登录


def test_restore_from_upload(app, client, db, tmp_path):
    make_patient(client, name="甲")
    package = tmp_path / "upload.zip"
    with app.app_context():
        backup.create_package(str(package))
    make_patient(client, name="乙")
    with open(package, "rb") as f:
        resp = client.post("/backups/upload", data={"backup": (f, "备份.zip")},
                           content_type="multipart/form-data")
    assert resp.headers["Location"].endswith("/login")
    assert [r[0] for r in db.execute("SELECT name FROM patients")] == ["甲"]


def test_upload_rejects_junk(client, db):
    make_patient(client, name="甲")
    resp = client.post("/backups/upload", data={"backup": (io.BytesIO(b"junk"), "x.zip")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "无法恢复" in resp.get_data(as_text=True)
    assert db.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 1


def test_mirror_form(client, tmp_path):
    make_patient(client)
    usb = tmp_path / "usb"
    usb.mkdir()
    page = client.post("/backups/mirror", data={"folder": str(usb)}, follow_redirects=True).get_data(as_text=True)
    assert "已设置外部备份文件夹" in page and backup.MIRROR_SUBDIR in page
    page = client.post("/backups/mirror", data={"folder": str(tmp_path / "nope")},
                       follow_redirects=True).get_data(as_text=True)
    assert "找不到文件夹" in page
    page = client.post("/backups/mirror", data={"action": "clear"}, follow_redirects=True).get_data(as_text=True)
    assert "已取消同步" in page


def test_legacy_backups_listed_and_restorable(app, client, db, tmp_path):
    make_patient(client, name="甲")
    folder = tmp_path / "backups"
    folder.mkdir(exist_ok=True)
    with app.app_context():
        backup.snapshot_database(str(folder / "wenzhen-20260901.sqlite3"))   # 1.1.0 版的每日备份
    make_patient(client, name="乙")
    page = client.get("/backups/").get_data(as_text=True)
    assert "每日自动（旧版）" in page
    resp = client.post("/backups/files/wenzhen-20260901.sqlite3/restore")
    assert resp.headers["Location"].endswith("/login")
    assert [r[0] for r in db.execute("SELECT name FROM patients")] == ["甲"]
