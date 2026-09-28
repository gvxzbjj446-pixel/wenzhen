import mimetypes

from wenzhen import create_app


def test_static_files_have_correct_types_despite_system_registry(tmp_path):
    # 模拟把 .js、.css 登记成 text/plain 的 Windows 电脑：浏览器会拒绝执行脚本
    mimetypes.add_type("text/plain", ".js")
    mimetypes.add_type("text/plain", ".css")
    app = create_app({"TESTING": True, "SECRET_KEY": "t", "AUTO_BACKUP": False},
                     instance_path=str(tmp_path))
    client = app.test_client()
    assert client.get("/static/app.js").mimetype == "text/javascript"
    assert client.get("/static/style.css").mimetype == "text/css"
    assert client.get("/static/icon.png").mimetype == "image/png"
    assert client.get("/static/app.js").headers["X-Content-Type-Options"] == "nosniff"
