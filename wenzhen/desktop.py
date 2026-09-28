"""桌面版：在独立的应用窗口中运行问诊系统，不需要打开浏览器。

    python -m wenzhen              启动桌面版
    python -m wenzhen --debug      开启开发者工具（排查问题用）

程序在本机随机端口启动网页服务（只允许本机访问），再用系统自带的
WebView（Windows 为 Edge WebView2，macOS 为 WKWebView）显示界面。
"""

import argparse
import base64
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
import shutil
from datetime import datetime

from . import __version__, backup, create_app
from .db import get_settings, set_user_password

APP_ID = "WenzhenClinic"
APP_TITLE = "王艳霞中医门诊"
INSTANCE_PORT = 47285  # 单实例检测用，只监听 127.0.0.1
_HELLO = b"wenzhen-show\n"
_ACK = b"ok\n"

WEBVIEW2_MISSING = (
    "本软件需要 Microsoft Edge WebView2 运行库，但这台电脑上没有检测到。\n\n"
    "请重新运行本软件的安装程序（会自动安装），或到微软官网搜索"
    "“WebView2 Runtime”下载安装后再打开。"
)

# 窗口、对话框和 macOS 菜单中的系统文字
LOCALIZATION = {
    "global.quitConfirmation": "确定要退出吗？",
    "global.ok": "确定",
    "global.quit": "退出",
    "global.cancel": "取消",
    "global.saveFile": "保存文件",
    "cocoa.menu.about": "关于",
    "cocoa.menu.services": "服务",
    "cocoa.menu.view": "显示",
    "cocoa.menu.edit": "编辑",
    "cocoa.menu.hide": "隐藏",
    "cocoa.menu.hideOthers": "隐藏其他",
    "cocoa.menu.showAll": "全部显示",
    "cocoa.menu.quit": "退出",
    "cocoa.menu.fullscreen": "进入全屏幕",
    "cocoa.menu.cut": "剪切",
    "cocoa.menu.copy": "拷贝",
    "cocoa.menu.paste": "粘贴",
    "cocoa.menu.selectAll": "全选",
    "windows.fileFilter.allFiles": "所有文件",
    "windows.fileFilter.otherFiles": "其他文件类型",
    "linux.openFile": "打开文件",
    "linux.openFiles": "打开文件",
    "linux.openFolder": "打开文件夹",
}

BACKUP_FILE_TYPES = ("数据备份 (*.zip;*.sqlite3;*.db)", "所有文件 (*.*)")

log = logging.getLogger("wenzhen.desktop")


# ---------------------------------------------------------------- 环境

def user_data_dir():
    """数据目录：Windows 为 %APPDATA%\\WenzhenClinic，macOS 为 ~/Library/Application Support/WenzhenClinic。"""
    override = os.environ.get("WENZHEN_DATA_DIR")
    if override:
        return os.path.abspath(override)
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, APP_ID)


def default_save_dir():
    home = os.path.expanduser("~")
    for name in ("Documents", "文档", "Desktop"):
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    return home


def setup_logging(data_dir, debug=False):
    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "wenzhen.log")
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    return path


def open_in_file_manager(path):
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - 打开本机文件夹
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def show_error(message):
    """在网页窗口出现之前出错时，用系统对话框提示。"""
    log.error(message)
    _message_box(message, error=True)


def show_info(message):
    _message_box(message, error=False)


def _message_box(message, error):
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10 if error else 0x40)
        elif sys.platform == "darwin":
            script = "display alert {} message {}{}".format(
                json.dumps(APP_TITLE, ensure_ascii=False), json.dumps(message, ensure_ascii=False),
                " as critical" if error else "",
            )
            subprocess.run(["osascript", "-e", script], check=False)
        else:
            print(message, file=sys.stderr if error else sys.stdout)
    except Exception:  # 提示本身失败时，日志里已有记录
        log.exception("无法显示提示对话框")


def webview2_available():
    """Windows：缺少 WebView2 时 pywebview 会退回到 IE 内核，界面无法正常使用。"""
    from webview.platforms import winforms
    return winforms.renderer == "edgechromium"


def window_icon(app):
    # Windows 窗口图标由 .NET 的 System.Drawing.Icon 读取，只接受 .ico；传入 PNG 会使程序崩溃
    name = "icon.ico" if sys.platform == "win32" else "icon.png"
    return os.path.join(app.static_folder, name)


def _first(result):
    """文件对话框在不同系统上返回字符串或元组，取第一个路径。"""
    if not result:
        return None
    return result if isinstance(result, str) else result[0]


# ---------------------------------------------------------------- 本机服务

class LocalServer:
    """在后台线程运行网页服务，只监听本机随机端口。"""

    def __init__(self, app):
        from waitress import create_server
        self._server = create_server(app, host="127.0.0.1", port=0, threads=4)
        self.url = f"http://127.0.0.1:{self._server.effective_port}"
        self._stopping = False
        self._thread = threading.Thread(target=self._serve, name="wenzhen-server", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stopping = True
        self._server.close()

    def _serve(self):
        try:
            self._server.run()
        except OSError:
            if not self._stopping:  # 退出时关闭监听端口会中断事件循环，属正常情况
                raise


class SingleInstance:
    """防止重复打开：再次启动时唤醒已打开的窗口，然后自行退出。"""

    def __init__(self, port=INSTANCE_PORT):
        self.port = port
        self.on_show = None
        self._sock = None

    def acquire(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", self.port))
            sock.listen(4)
        except OSError:
            sock.close()
            return False
        self._sock = sock
        threading.Thread(target=self._serve, name="wenzhen-instance", daemon=True).start()
        return True

    def notify_existing(self):
        """通知已打开的程序显示窗口；对方是本程序并已应答时返回 True。"""
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as conn:
                conn.sendall(_HELLO)
                return conn.recv(16) == _ACK
        except OSError:
            return False

    def release(self):
        if self._sock is not None:
            try:
                # 唤醒阻塞在 accept() 的线程，否则 Linux 上端口仍处于监听状态
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except (OSError, AttributeError):
                return
            with conn:
                try:
                    conn.settimeout(2)
                    if conn.recv(64) == _HELLO:
                        conn.sendall(_ACK)
                        if self.on_show:
                            self.on_show()
                except OSError:
                    pass


# ---------------------------------------------------------------- 网页可调用的本机功能

class DesktopApi:
    """网页通过 window.pywebview.api.* 调用这些方法。

    pywebview 不会把以下划线开头的属性和方法暴露给网页。
    """

    def __init__(self, app, data_dir):
        self._app = app
        self._data_dir = data_dir
        self._window = None
        self._base_url = ""
        self._dirty = False
        self._allow_close = False

    # ---- 网页调用 ----

    def set_dirty(self, dirty):
        """表单有未保存的修改时为 True，关闭窗口前会提醒。"""
        self._dirty = bool(dirty)

    def save_file(self, filename, data_url):
        """把网页生成的文件（导出的表格等）另存到用户选择的位置，返回保存路径。"""
        payload = data_url.split(",", 1)[1] if "," in data_url else data_url
        path = self._ask_save_path(os.path.basename(filename or "") or "导出文件")
        if not path:
            return None
        with open(path, "wb") as f:
            f.write(base64.b64decode(payload))
        return path

    def backup_database(self):
        """导出完整数据包（.zip）到用户选择的位置（如 U 盘），返回保存路径。"""
        path = self._ask_save_path(self._package_name(datetime.now()))
        if not path:
            return None
        with self._app.app_context():
            backup.export_package(path)
        log.info("已导出完整数据包到 %s", path)
        return path

    def save_backup_copy(self, name):
        """把备份列表中的某个备份另存到用户选择的位置，返回保存路径。"""
        with self._app.app_context():
            source = backup.backup_path(name)
            info = next((b for b in backup.list_backups() if b["name"] == name), None)
        if source is None:
            return None
        suffix = os.path.splitext(name)[1]
        path = self._ask_save_path(self._package_name(info["created"] if info else None, suffix))
        if not path:
            return None
        shutil.copyfile(source, path)
        with self._app.app_context():
            backup.mark_offsite()
        return path

    def choose_backup(self):
        """选择要恢复的备份文件并检查，返回其中的记录数和备份时间。"""
        from webview import FileDialog
        path = _first(self._window.create_file_dialog(
            FileDialog.OPEN, directory=default_save_dir(), file_types=BACKUP_FILE_TYPES,
        ))
        if not path:
            return None
        try:
            with self._app.app_context():
                info = backup.inspect_backup(path)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        created = backup._parse_time(info["created_at"])
        return {
            "ok": True, "path": path, "patients": info["patients"], "visits": info["visits"],
            "created_at": created.strftime("%Y-%m-%d %H:%M") if created else "",
        }

    def restore_database(self, path):
        """用备份文件替换当前数据；替换前的数据会自动另存到备份文件夹。"""
        try:
            with self._app.app_context():
                safety = backup.restore_backup(path)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "safety": safety}

    def choose_mirror_folder(self):
        """选择外部备份文件夹（U 盘、网盘同步文件夹等），并立即复制一份备份过去。"""
        from webview import FileDialog
        folder = _first(self._window.create_file_dialog(FileDialog.FOLDER, directory=default_save_dir()))
        if not folder:
            return None
        with self._app.app_context():
            try:
                copied = backup.set_mirror_root(folder)
            except ValueError as exc:
                return {"ok": False, "message": str(exc)}
            if copied:
                return {"ok": True, "message": f"已设置，现有备份已同步到 {copied}"}
            return {"ok": False, "message": f"已设置，但复制失败：{backup.status()['mirror_error']}"}

    def open_backup_folder(self):
        with self._app.app_context():
            folder = backup.backup_dir()
        os.makedirs(folder, exist_ok=True)
        open_in_file_manager(folder)
        return True

    def open_data_folder(self):
        open_in_file_manager(self._data_dir)
        return True

    def confirm_quit(self):
        """用户在“尚未保存，确定退出？”中选了退出。"""
        self._force_close()

    # ---- 程序内部 ----

    def _package_name(self, created=None, suffix=".zip"):
        with self._app.app_context():
            clinic = get_settings()["clinic_name"]
        return f"{clinic}-数据备份-{(created or datetime.now()):%Y%m%d-%H%M}{suffix}"

    def _backup_on_exit(self):
        """关闭程序时用最新数据更新当天的自动备份，当天的工作也有备份。"""
        try:
            with self._app.app_context():
                path = backup.daily_backup(self._app.config["BACKUP_KEEP_DAYS"], refresh=True)
            log.info("退出时备份：%s", path or "数据无变化，无需更新")
        except Exception:
            log.exception("退出时备份失败")

    def _attach(self, window, base_url):
        self._window = window
        self._base_url = base_url

    def _ask_save_path(self, filename):
        from webview import FileDialog
        return _first(self._window.create_file_dialog(
            FileDialog.SAVE, directory=default_save_dir(), save_filename=filename,
        ))

    def _run_js(self, script):
        return self._window.evaluate_js(script)

    def _toast(self, message):
        self._run_js(f"window.wenzhenToast && wenzhenToast({json.dumps(message, ensure_ascii=False)})")

    def _on_closing(self):
        """关闭窗口时：有未保存的表单就先询问（在网页中弹出），否则直接关闭。"""
        if self._dirty and not self._allow_close:
            threading.Thread(target=self._ask_before_quit, daemon=True).start()
            return False
        return True

    def _ask_before_quit(self):
        shown = False
        try:
            shown = self._run_js("window.wenzhenConfirmQuit ? (wenzhenConfirmQuit(), true) : false")
        except Exception:
            log.exception("无法显示退出确认")
        if not shown:
            self._force_close()

    def _force_close(self):
        self._allow_close = True
        self._window.destroy()

    def _request_close(self):
        if self._dirty:
            self._ask_before_quit()
        else:
            self._force_close()

    def _bring_to_front(self):
        window = self._window
        if window is None:
            return
        window.restore()
        window.show()
        window.on_top = True
        window.on_top = False

    # ---- 菜单 ----

    def _menu_backup(self):
        path = self.backup_database()
        if path:
            self._toast(f"已导出完整数据包：{path}")

    def _menu_restore(self):
        self._run_js("window.wenzhenRestore && wenzhenRestore()")

    def _menu_reload(self):
        self._run_js("location.reload()")

    def _menu_go(self, path):
        return lambda: self._window.load_url(self._base_url + path)


def build_menu(api):
    from webview.menu import Menu, MenuAction, MenuSeparator
    return [
        Menu("文件", [
            MenuAction("新患者建档", api._menu_go("/patients/new")),
            MenuSeparator(),
            MenuAction("导出完整数据包…", api._menu_backup),
            MenuAction("从备份恢复…", api._menu_restore),
            MenuAction("数据备份与恢复", api._menu_go("/backups/")),
            MenuAction("打开数据文件夹", api.open_data_folder),
            MenuSeparator(),
            MenuAction("退出", api._request_close),
        ]),
        Menu("查看", [
            MenuAction("工作台", api._menu_go("/")),
            MenuAction("患者", api._menu_go("/patients/")),
            MenuAction("复诊提醒", api._menu_go("/followups")),
            MenuSeparator(),
            MenuAction("刷新", api._menu_reload),
        ]),
        Menu("帮助", [
            MenuAction("关于本软件", api._menu_go("/settings#about")),
        ]),
    ]


# ---------------------------------------------------------------- 启动

def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="wenzhen", description=f"{APP_TITLE} · 问诊记录系统")
    parser.add_argument("--data-dir", help="数据目录（默认在用户目录下）")
    parser.add_argument("--debug", action="store_true", help="开启开发者工具")
    parser.add_argument("--reset-password", nargs=2, metavar=("用户名", "新密码"),
                        help="忘记密码时重置（账户不存在则新建），不打开窗口")
    parser.add_argument("--smoke-test", action="store_true",
                        help="自动检查：打开窗口、确认页面正常后自动退出（用于打包后的检查）")
    # macOS 可能附带 -psn_xxx 等参数，忽略未知参数
    args, _ = parser.parse_known_args(argv)
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.smoke_test and not args.data_dir:
        args.data_dir = tempfile.mkdtemp(prefix="wenzhen-smoke-")
    data_dir = os.path.abspath(args.data_dir or user_data_dir())
    os.makedirs(data_dir, exist_ok=True)
    log_path = setup_logging(data_dir, args.debug or args.smoke_test)
    log.info("启动 %s %s，数据目录：%s", APP_TITLE, __version__, data_dir)
    if args.reset_password:
        return reset_password(data_dir, *args.reset_password)

    instance = None
    if not args.smoke_test:
        instance = SingleInstance()
        if not instance.acquire():
            if instance.notify_existing():
                log.info("程序已在运行，已唤醒原窗口")
                return 0
            log.warning("单实例端口被其他程序占用，照常启动")
            instance = None
    try:
        return run(data_dir, args, instance)
    except Exception:
        log.exception("程序出错")
        if not args.smoke_test:
            show_error(f"程序启动失败。\n\n详细信息已记录在：\n{log_path}")
        return 1
    finally:
        if instance:
            instance.release()


def reset_password(data_dir, username, password):
    app = create_app({"DESKTOP": True}, instance_path=data_dir)
    try:
        with app.app_context():
            message = set_user_password(username, password)
    except ValueError as exc:
        show_error(str(exc))
        return 1
    log.info(message)
    show_info(message)
    return 0


def run(data_dir, args, instance=None):
    import webview

    if sys.platform == "win32" and not webview2_available():
        if not args.smoke_test:
            show_error(WEBVIEW2_MISSING)
        return 2

    app = create_app({"DESKTOP": True}, instance_path=data_dir)
    server = LocalServer(app).start()
    log.info("本机服务：%s", server.url)
    if args.smoke_test:
        _check_http(server.url)

    with app.app_context():
        title = get_settings()["clinic_name"]
    api = DesktopApi(app, data_dir)
    window = webview.create_window(
        title, server.url + "/", js_api=api,
        width=1360, height=900, min_size=(1024, 680), maximized=not args.smoke_test,
        text_select=True, zoomable=True, background_color="#F5F2EB",
        menu=build_menu(api),
    )
    api._attach(window, server.url)
    window.events.closing += api._on_closing
    if instance:
        instance.on_show = api._bring_to_front

    result = {"ok": not args.smoke_test}
    webview.start(
        _smoke_check if args.smoke_test else None,
        (window, api, result) if args.smoke_test else None,
        localization=LOCALIZATION, debug=args.debug, private_mode=True,
        icon=window_icon(app),
    )
    api._backup_on_exit()
    server.stop()
    log.info("程序退出")
    return 0 if result["ok"] else 3


def _check_http(base_url):
    expected = {"/login": "text/html", "/static/style.css": "text/css",
                "/static/app.js": "text/javascript"}
    for path, mime in expected.items():
        with urllib.request.urlopen(base_url + path, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if resp.status != 200 or not content_type.startswith(mime):
                raise RuntimeError(f"{path} 返回 {resp.status} {content_type}")
    log.info("自检：网页服务正常")


def _smoke_check(window, api, result):
    """打包后的自动检查：窗口打开、页面和本机功能接口都正常就退出。"""
    try:
        if not window.events.loaded.wait(90):
            raise RuntimeError("页面加载超时")
        title = window.evaluate_js("document.title")
        has_form = window.evaluate_js("!!document.querySelector('.auth-card form')")
        bridge = window.evaluate_js(
            "!!(window.pywebview && window.pywebview.api && window.pywebview.api.save_file)"
        )
        # 界面脚本已执行（被浏览器拦截时，点选项、开方等都会失灵）
        script = window.evaluate_js("typeof window.wenzhenConfirm === 'function'")
        log.info("自检：标题=%r 登录表单=%s 本机接口=%s 界面脚本=%s", title, has_form, bridge, script)
        result["ok"] = bool(title and "中医" in title and has_form and bridge and script)
    except Exception:
        log.exception("自检失败")
    finally:
        api._force_close()
