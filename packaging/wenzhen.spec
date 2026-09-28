# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置，在项目根目录运行：
#
#     pyinstaller --noconfirm --clean packaging/wenzhen.spec
#
# 输出：Windows 为 dist/Wenzhen/Wenzhen.exe，macOS 为 dist/王艳霞中医门诊.app

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
sys.path.insert(0, ROOT)
from wenzhen import __version__  # noqa: E402

APP_NAME = "王艳霞中医门诊"
ASSETS = os.path.join(ROOT, "packaging", "assets")

datas = [
    (os.path.join(ROOT, "wenzhen", "templates"), "wenzhen/templates"),
    (os.path.join(ROOT, "wenzhen", "static"), "wenzhen/static"),
    (os.path.join(ROOT, "wenzhen", "schema.sql"), "wenzhen"),
]

excludes = ["tkinter", "pytest"]
if sys.platform in ("win32", "darwin"):
    # Windows 用 WebView2、macOS 用 WKWebView，不需要 Qt/GTK
    excludes += ["PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi"]

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["waitress"],
    excludes=excludes,
)
pyz = PYZ(a.pure)

version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
        VarStruct, VSVersionInfo,
    )

    numbers = tuple(int(part) for part in __version__.split(".")) + (0,)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("080404B0", [
                StringStruct("CompanyName", APP_NAME),
                StringStruct("FileDescription", APP_NAME),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", "Wenzhen"),
                StringStruct("OriginalFilename", "Wenzhen.exe"),
                StringStruct("ProductName", f"{APP_NAME} 问诊记录系统"),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0804, 1200])]),
        ],
    )

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Wenzhen",
    console=False,
    icon=os.path.join(ASSETS, "icon.icns" if sys.platform == "darwin" else "icon.ico"),
    version=version_info,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Wenzhen", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=os.path.join(ASSETS, "icon.icns"),
        bundle_identifier="cn.wenzhen.clinic",
        version=__version__,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleDevelopmentRegion": "zh_CN",
            "LSApplicationCategoryType": "public.app-category.medical",
            "NSHighResolutionCapable": True,
        },
    )
