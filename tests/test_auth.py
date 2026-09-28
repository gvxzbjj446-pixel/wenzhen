from wenzhen import create_app


def test_first_run_redirects_to_setup(anon):
    resp = anon.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/setup")


def test_setup_creates_account_and_logs_in(client, db):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "工作台" in resp.get_data(as_text=True)
    assert db.execute("SELECT username FROM users").fetchone()["username"] == "wang"


def test_setup_is_closed_once_account_exists(client, anon):
    assert anon.get("/setup").headers["Location"].endswith("/login")


def test_setup_validates_password(anon):
    resp = anon.post("/setup", data={
        "clinic_name": "诊所", "username": "wang", "password": "123", "password2": "123",
    })
    assert resp.status_code == 200
    assert "密码至少 6 位" in resp.get_data(as_text=True)


def test_login_required_and_next_redirect(client, anon):
    resp = anon.get("/patients/?q=张")
    assert resp.status_code == 302
    assert "/login?next=" in resp.headers["Location"]

    bad = anon.post("/login", data={"username": "wang", "password": "wrong"})
    assert "用户名或密码错误" in bad.get_data(as_text=True)

    ok = anon.post("/login", data={"username": "wang", "password": "secret1", "next": "/patients/"})
    assert ok.status_code == 302
    assert ok.headers["Location"] == "/patients/"


def test_login_rejects_external_next(client, anon):
    resp = anon.post("/login", data={
        "username": "wang", "password": "secret1", "next": "//evil.example.com/",
    })
    assert resp.headers["Location"] == "/"


def test_logout(client):
    client.post("/logout")
    assert client.get("/").status_code == 302


def test_csrf_rejects_post_without_token(tmp_path):
    app = create_app({
        "TESTING": True, "SECRET_KEY": "x", "AUTO_BACKUP": False,
        "DATABASE": str(tmp_path / "csrf.sqlite3"),
    })
    client = app.test_client()
    resp = client.post("/setup", data={"username": "a", "password": "secret1", "password2": "secret1"})
    assert resp.status_code == 400

    client.get("/setup")
    with client.session_transaction() as sess:
        token = sess["_csrf"]
    resp = client.post("/setup", data={
        "_csrf": token, "clinic_name": "诊所", "username": "a",
        "password": "secret1", "password2": "secret1",
    })
    assert resp.status_code == 302


def test_change_password_and_manage_users(client, anon):
    resp = client.post("/account", data={
        "action": "password", "old_password": "wrong",
        "new_password": "newpass1", "new_password2": "newpass1",
    })
    assert "原密码不正确" in resp.get_data(as_text=True)

    resp = client.post("/account", data={
        "action": "password", "old_password": "secret1",
        "new_password": "newpass1", "new_password2": "newpass1",
    })
    assert resp.status_code == 302
    assert anon.post("/login", data={"username": "wang", "password": "newpass1"}).status_code == 302

    resp = client.post("/account", data={
        "action": "add_user", "username": "zhuli", "display_name": "助理",
        "password": "zhuli123", "password2": "zhuli123",
    })
    assert resp.status_code == 302
    page = client.get("/account").get_data(as_text=True)
    assert "zhuli" in page

    dup = client.post("/account", data={
        "action": "add_user", "username": "zhuli", "password": "zhuli123", "password2": "zhuli123",
    })
    assert "该用户名已存在" in dup.get_data(as_text=True)


def test_cannot_delete_self(client, db):
    me = db.execute("SELECT id FROM users WHERE username = 'wang'").fetchone()["id"]
    client.post(f"/account/users/{me}/delete")
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_set_password_command(app, client, anon):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["set-password", "wang", "--password", "another1"])
    assert "已重置" in result.output
    assert anon.post("/login", data={"username": "wang", "password": "another1"}).status_code == 302

    result = runner.invoke(args=["set-password", "newbie", "--password", "newbie12"])
    assert "已创建" in result.output

    result = runner.invoke(args=["set-password", "x", "--password", "123"])
    assert result.exit_code != 0


def test_backup_command(app, tmp_path):
    result = app.test_cli_runner().invoke(args=["backup"])
    assert result.exit_code == 0
    assert any(p.name.startswith("wenzhen-manual-") for p in (tmp_path / "backups").iterdir())
