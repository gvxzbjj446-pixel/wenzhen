"""启动问诊记录系统。

    python run.py            仅本机访问：http://127.0.0.1:5000
    python run.py --lan      允许同一局域网内的手机、平板、其他电脑访问
"""

import argparse
import os
import socket
import threading
import webbrowser

from wenzhen import create_app


def lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="王艳霞中医门诊 · 问诊记录系统")
    parser.add_argument("--port", type=int, default=int(os.environ.get("WENZHEN_PORT", "5000")))
    parser.add_argument("--lan", action="store_true", help="允许局域网内其他设备访问")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    app = create_app()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}"
    print(f"问诊记录系统已启动：{url}")
    if args.lan:
        print(f"局域网访问地址：http://{lan_ip()}:{args.port}")
    print(f"数据文件：{app.config['DATABASE']}")
    print("关闭此窗口即停止服务。")
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, (url,)).start()

    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=args.port)
    else:
        serve(app, host=host, port=args.port, threads=8)


if __name__ == "__main__":
    main()
