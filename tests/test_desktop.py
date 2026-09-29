import base64
import json
import os
import socket
import sqlite3
import threading
import time
import urllib.request
import zipfile

import pytest

from conftest import make_patient
from wenzhen import create_app, desktop


class FakeWindow:
    """代替 pywebview 窗口：记录调用，文件对话框返回预设结果。"""

    def __init__(self):
        self.dialog_result = None
        self.dialog_calls = []
        self.scripts = []
        self.js_result = True
        self.destroyed = False

    def create_file_dialog(self, *args, **kwargs):
        self.dialog_calls.append(kwargs)
        return self.dialog_result

    def evaluate_js(self, script):
        self.scripts.append(script)
        return self.js_result

    def destroy(self):
        self.destroyed = True


@pytest.fixture
def api(app, tmp_path):
    api = desktop.DesktopApi(app, str(tmp_path))
    api._attach(FakeWindow(), "http://127.0.0.1:1")
    return api


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("WENZHEN_DATA_DIR", str(tmp_path / "data"))
    assert desktop.user_data_dir() == str(tmp_path / "data")
    monkeypatch.delenv("WENZHEN_DATA_DIR")
    assert desktop.user_data_dir().endswith(desktop.APP_ID)


def test_parse_args_ignores_unknown_arguments():
    args = desktop.parse_args(["-psn_0_12345", "--debug"])
    assert args.debug and not args.smoke_test


def test_save_file_writes_chosen_path(api, tmp_path):
    target = tmp_path / "导出.csv"
    api._window.dialog_result = (str(target),)
    content = "﻿病历号,姓名".encode()
    data_url = "data:text/csv;base64," + base64.b64encode(content).decode()
    assert api.save_file("../../患者档案.csv", data_url) == str(target)
    assert target.read_bytes() == content
    # 只用文件名作默认名，忽略网页传来的路径
    assert api._window.dialog_calls[0]["save_filename"] == "患者档案.csv"


def test_save_file_cancelled(api):
    api._window.dialog_result = None
    assert api.save_file("a.csv", "data:text/csv;base64,") is None


def test_backup_and_restore(api, client, db, tmp_path):
    make_patient(client, name="甲")
    package = tmp_path / "备份.zip"
    api._window.dialog_result = str(package)  # 有的系统返回字符串
    assert api.backup_database() == str(package)
    assert api._window.dialog_calls[0]["save_filename"].endswith(".zip")
    with zipfile.ZipFile(package) as zf:
        assert {"manifest.json", "wenzhen.sqlite3", "data.json", "csv/patients.csv"} <= set(zf.namelist())

    make_patient(client, name="乙")
    api._window.dialog_result = (str(package),)
    info = api.choose_backup()
    assert info["ok"] and info["path"] == str(package)
    assert (info["patients"], info["visits"]) == (1, 0) and info["created_at"]

    result = api.restore_database(str(package))
    assert result["ok"] and os.path.exists(result["safety"])
    assert [r[0] for r in db.execute("SELECT name FROM patients")] == ["甲"]
    # 恢复前的数据另存了一份，里面有两位患者
    with zipfile.ZipFile(result["safety"]) as zf:
        assert json.loads(zf.read("manifest.json"))["counts"]["patients"] == 2


def test_save_listed_backup_elsewhere(api, client, tmp_path):
    make_patient(client)
    with api._app.app_context():
        from wenzhen.backup import manual_backup
        name = os.path.basename(manual_backup())
    target = tmp_path / "U盘" / "副本.zip"
    target.parent.mkdir()
    api._window.dialog_result = (str(target),)
    assert api.save_backup_copy(name) == str(target)
    assert target.read_bytes() == (tmp_path / "backups" / name).read_bytes()
    assert api._window.dialog_calls[-1]["save_filename"].endswith(".zip")
    assert api.save_backup_copy("../secret.zip") is None


def test_choose_mirror_folder(api, client, tmp_path):
    make_patient(client)
    usb = tmp_path / "usb"
    usb.mkdir()
    api._window.dialog_result = (str(usb),)
    result = api.choose_mirror_folder()
    assert result["ok"], result
    assert list((usb / "问诊记录备份").glob("*.zip"))

    api._window.dialog_result = None
    assert api.choose_mirror_folder() is None


def test_backup_on_exit_updates_todays_backup(api, client, tmp_path):
    make_patient(client)
    api._backup_on_exit()
    today = sorted((tmp_path / "backups").glob("auto-*.zip"))
    assert len(today) == 1


def test_choose_backup_rejects_other_files(api, tmp_path):
    junk = tmp_path / "junk.sqlite3"
    junk.write_bytes(b"not a database" * 200)
    api._window.dialog_result = (str(junk),)
    assert api.choose_backup()["ok"] is False

    other = tmp_path / "other.sqlite3"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE t (x)")
    conn.close()
    api._window.dialog_result = (str(other),)
    result = api.choose_backup()
    assert result["ok"] is False and "不是本系统" in result["message"]

    api._window.dialog_result = None
    assert api.choose_backup() is None


def test_close_asks_only_when_form_unsaved(api):
    assert api._on_closing() is True

    api.set_dirty(True)
    assert api._on_closing() is False
    deadline = time.time() + 2
    while not api._window.scripts and time.time() < deadline:
        time.sleep(0.01)
    assert "wenzhenConfirmQuit" in api._window.scripts[0]
    assert not api._window.destroyed

    api.confirm_quit()
    assert api._window.destroyed
    assert api._on_closing() is True


def test_close_proceeds_when_page_cannot_ask(api):
    api.set_dirty(True)
    api._window.js_result = False  # 例如打印页没有加载确认框
    api._ask_before_quit()
    assert api._window.destroyed


def test_single_instance_wakes_existing_window():
    port = free_port()
    first = desktop.SingleInstance(port)
    assert first.acquire()
    shown = threading.Event()
    first.on_show = shown.set
    try:
        second = desktop.SingleInstance(port)
        assert not second.acquire()
        assert second.notify_existing()
        assert shown.wait(2)
    finally:
        first.release()
    third = desktop.SingleInstance(port)
    assert third.acquire()
    third.release()


def test_single_instance_ignores_other_programs():
    port = free_port()
    with socket.socket() as other:
        other.bind(("127.0.0.1", port))
        other.listen(1)
        assert desktop.SingleInstance(port).notify_existing() is False


def test_local_server_serves_app(app):
    server = desktop.LocalServer(app).start()
    try:
        assert server.url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(server.url + "/static/style.css", timeout=5) as resp:
            assert resp.status == 200
    finally:
        server.stop()


def test_desktop_mode_backup_page(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "t", "CSRF_ENABLED": False,
                      "AUTO_BACKUP": False, "DESKTOP": True}, instance_path=str(tmp_path))
    assert app.config["DATABASE"] == str(tmp_path / "wenzhen.sqlite3")
    client = app.test_client()
    client.post("/setup", data={"clinic_name": "诊所", "username": "a",
                                "password": "secret1", "password2": "secret1"})
    page = client.get("/backups/").get_data(as_text=True)
    for action in ("backup", "restore", "choose-mirror", "open-backups"):
        assert f'data-desktop-action="{action}"' in page
    assert "下载完整数据包" not in page and "上传并恢复" not in page
    assert str(tmp_path) in client.get("/settings").get_data(as_text=True)


def test_web_mode_backup_page(client):
    page = client.get("/backups/").get_data(as_text=True)
    assert "下载完整数据包" in page and "上传并恢复" in page
    assert "data-desktop-action" not in page


def test_reset_password_from_command_line(tmp_path, capsys, monkeypatch):
    # Windows 的原生消息框会等待人工关闭；断言结果并隔离界面通知。
    monkeypatch.setattr(desktop, "show_info", print)
    monkeypatch.setattr(desktop, "show_error", print)
    data = str(tmp_path / "data")
    assert desktop.main(["--data-dir", data, "--reset-password", "wang", "newpass1"]) == 0
    assert "已创建账户 wang" in capsys.readouterr().out
    assert desktop.main(["--data-dir", data, "--reset-password", "wang", "another2"]) == 0
    assert "已重置" in capsys.readouterr().out
    assert desktop.main(["--data-dir", data, "--reset-password", "wang", "123"]) == 1

    app = create_app({"TESTING": True, "SECRET_KEY": "t", "CSRF_ENABLED": False,
                      "AUTO_BACKUP": False}, instance_path=data)
    resp = app.test_client().post("/login", data={"username": "wang", "password": "another2"})
    assert resp.status_code == 302


def test_window_icon_matches_platform(app, monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    assert desktop.window_icon(app).endswith("icon.ico")
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    assert desktop.window_icon(app).endswith("icon.png")
    for name in ("icon.ico", "icon.png"):
        assert os.path.isfile(os.path.join(app.static_folder, name))
