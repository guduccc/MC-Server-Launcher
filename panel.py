#!/usr/bin/env python3
"""MC Panel —— Minecraft 服务端面板开服器（零依赖）。

用法：
    python panel.py                       # 启动面板，自动打开浏览器
    python panel.py --port 9000           # 指定端口
    python panel.py --host 0.0.0.0 --password 你的密码
    python panel.py --check               # 只做环境自检，不启动面板
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mcpanel import __version__                      # noqa: E402
from mcpanel.config import PanelConfig               # noqa: E402
from mcpanel.instance import InstanceManager         # noqa: E402
from mcpanel.util import (app_root, has_console, human_bytes,  # noqa: E402
                          is_frozen, port_available, writable)
from mcpanel.web import PanelServer                  # noqa: E402

# 打包成 exe 后，数据目录必须是 exe 所在目录
ROOT = app_root()


def setup_console() -> None:
    """把控制台切到 UTF-8；没有控制台时把输出重定向到日志文件。

    两件事都很关键：
    - 中文 Windows 控制台默认 GBK，打印 ✓ ✗ → 会抛 UnicodeEncodeError 把程序打死；
    - 打包成 --noconsole 的窗口版 exe 后，sys.stdout / sys.stderr 是 **None**，
      此时任何 print 都会崩。必须改写成文件或空流。
    """
    if sys.stdout is None or sys.stderr is None:
        stream = None
        try:
            stream = open(app_root() / "panel-gui.log", "a",
                          encoding="utf-8", buffering=1)
        except OSError:
            import io
            stream = io.StringIO()
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
        return

    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001  没有控制台（输出重定向）时会失败
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


setup_console()

BANNER = r"""
 __  __  ___   ___                _
|  \/  |/ __| | _ \__ _ _ _ __ _| |
| |\/| | (__  |  _/ _` | ' \/ _` | |
|_|  |_|\___| |_| \__,_|_||_\__,_|_|
     Minecraft 面板开服器  v{ver}{frozen}
""".format(ver=__version__,
           frozen="  [独立 exe]" if is_frozen() else "")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="MC Panel —— Minecraft 面板开服器",
        epilog="默认以桌面窗口方式启动（原生窗口，无需浏览器）。"
               "加 --browser 可改为在浏览器里打开。")
    p.add_argument("--host", help="面板监听地址，默认 127.0.0.1（仅本机）")
    p.add_argument("--port", type=int, help="面板监听端口，默认 8080")
    p.add_argument("--password", help="面板访问密码（走网络时强烈建议设置）")
    p.add_argument("--servers-dir", help="服务端实例存放目录")
    p.add_argument("--qt", action="store_true",
                   help="用 Qt 原生界面（需要 PyQt5 + PyQt-SiliconUI）")
    p.add_argument("--web", action="store_true",
                   help="用网页界面（默认在有 Qt 环境时用 Qt）")
    p.add_argument("--browser", action="store_true",
                   help="用系统浏览器打开网页界面")
    p.add_argument("--app", action="store_true",
                   help="用浏览器窗口模式（无地址栏的独立窗口，不需要 pywebview）")
    p.add_argument("--desktop", action="store_true",
                   help="强制原生桌面窗口（需要 pywebview，缺了会自动降级）")
    p.add_argument("--no-browser", action="store_true",
                   help="启动后不自动打开界面（只跑 HTTP 服务）")
    p.add_argument("--stop-all-on-exit", action="store_true",
                   help="退出面板时一并停止所有正在运行的服务端")
    p.add_argument("--check", action="store_true", help="仅执行环境自检并退出")
    p.add_argument("--software-render", action="store_true",
                   help="强制软件渲染（窗口一片空白时试试，虚拟机/远程桌面常用）")
    p.add_argument("--ui-selftest", type=float, default=0.0, metavar="秒",
                   help="打开界面后 N 秒自动关闭（打包验证用）")
    p.add_argument("--version", action="version", version=f"MC Panel {__version__}")
    return p.parse_args()


def environment_check(config: PanelConfig, manager: InstanceManager) -> bool:
    """启动前自检：Python 依赖、Java、目录、端口。"""
    print("=" * 58)
    print("环境自检")
    print("=" * 58)
    ok = True

    print(f"  Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"  运行方式    : {'独立 exe（PyInstaller）' if is_frozen() else 'Python 源码'}")
    print(f"  数据目录    : {config.root}")
    print(f"  实例目录    : {config.servers_dir}")

    if not writable(config.root):
        ok = False
        print("  目录可写    : ✗ 当前目录没有写入权限")
        if is_frozen():
            print("                请把 exe 放到桌面、D 盘等普通目录，")
            print("                不要放在 C:\\Program Files 或系统盘根目录。")
        else:
            print("                请换一个有权限的目录运行。")
    else:
        print("  目录可写    : ✓")

    javas = manager.javas(refresh=True)
    if not javas:
        ok = False
        print("  Java        : ✗ 没有检测到 Java")
        print("                Minecraft 服务端必须要 Java 才能运行。")
        print("                · 1.12.2 及以下 → 装 Java 8")
        print("                · 1.17 ~ 1.20.4 → 装 Java 17")
        print("                · 1.20.5 及以后 → 装 Java 21")
        print("                下载后也可以稍后在「实例设置」里手动填 java.exe 路径。")
    else:
        print(f"  Java        : 共发现 {len(javas)} 个")
        for j in javas:
            print(f"                - Java {j['version']:<3} {j['arch']:<4} {j['path']}")

    host = config.get("host") or "127.0.0.1"
    port = int(config.get("port") or 8080)
    if port_available(host, port):
        print(f"  面板端口    : {host}:{port} ✓ 可用")
    else:
        ok = False
        print(f"  面板端口    : {host}:{port} ✗ 已被占用，请用 --port 换一个")

    auth = "已开启" if config.auth_enabled else "未设置（仅本机使用是安全的）"
    print(f"  面板鉴权    : {auth}")
    if not config.auth_enabled and host not in ("127.0.0.1", "localhost"):
        ok = False
        print("                ✗ 监听在公网地址却没有设置密码，请加 --password")

    from mcpanel.qtui import qt_available, qt_missing_reason
    from mcpanel.desktop import (find_app_browser, pywebview_available,
                                 webview2_available, webview2_version)
    if qt_available():
        print("  Qt 界面     : ✓ 可用（PyQt5 + PyQt-SiliconUI，原生窗口）")
    else:
        print("  Qt 界面     : - 不可用")
        for line in qt_missing_reason().splitlines():
            print(f"                {line}")
    browser = find_app_browser()
    if pywebview_available() and webview2_available():
        print(f"  网页界面    : 桌面窗口（pywebview + WebView2 {webview2_version()}）")
    elif pywebview_available():
        # 这个组合最容易骗人：程序能起、窗口能开，但渲染内核是 IE，
        # 界面没样式、按钮全点不动。必须在自检里直接点出来。
        print("  网页界面    : ✗ 装了 pywebview 但没装 WebView2 运行时")
        print("                原生窗口会退化到 IE(MSHTML) 内核：")
        print("                深色主题丢失、控件错位、所有按钮点了没反应。")
        print("                装 WebView2 运行时即可修复（一次装好永久有效）：")
        print("                https://developer.microsoft.com/microsoft-edge/webview2/")
        print(f"                或改用浏览器窗口模式：--app"
              f"{'（' + Path(browser).name + '）' if browser else '（需要先装 Edge / Chrome）'}")
    else:
        print(f"  网页界面    : 浏览器窗口"
              f"{'（' + Path(browser).name + '）' if browser else '（系统默认浏览器）'}")
    print("=" * 58)
    return ok


def free_port(host: str, port: int, tries: int = 20) -> int:
    for candidate in range(port, port + tries):
        if port_available(host, candidate):
            return candidate
        # 如果已有面板在跑，直接把地址打出来
        import socket
        s = socket.socket()
        try:
            s.settimeout(0.4)
            s.connect((host, candidate))
            print(f"[面板] 端口 {candidate} 上已经有一个服务在跑了：http://{host}:{candidate}/")
        except OSError:
            pass
        finally:
            s.close()
    return -1


def hard_exit(code: int = 0) -> None:
    """强制收尾。

    Qt / pythonnet / WebView2 都会留下非守护线程，正常 return 的话解释器
    会一直等它们，进程永远退不掉（任务管理器里挂着）。所以收尾一律走这里。
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(code)


def shutdown_ui(server, stop_servers, code: int) -> None:
    """界面关掉之后的收尾，然后强制退出。**不会返回**。

    `server.stop()` 干的是 `httpd.shutdown()` —— 它要在调用线程里等 httd 的
    `serve_forever` 收工。实测在 pywebview / pythonnet 收尾之后再调它，
    会有相当一部分环境下永远等不回来：窗口已经关了、日志也打完「窗口已关闭」，
    进程却一直挂在任务管理器里，并一直占着 exe（下次打包就报"正被占用"）。

    所以这里不留任何"可能永远等下去"的动作：停 HTTP 服务丢进守护线程就不管了，
    接着立刻 os._exit。反正进程马上要没了，那个端口交给系统回收即可，
    没必要礼貌地把 TCP 连接握完。
    """
    stop_servers()          # 停服务端实例：内部是有超时上限的，让它正常跑完
    threading.Thread(target=server.stop, name="http-stop", daemon=True).start()
    hard_exit(code)


def main() -> int:
    # PyInstaller 打包后必须调用，避免子进程重复拉起整个程序
    try:
        import multiprocessing
        multiprocessing.freeze_support()
    except Exception:  # noqa: BLE001
        pass

    args = parse_args()
    print(BANNER)

    config = PanelConfig(ROOT)
    if args.host:
        config.update(host=args.host)
    if args.port:
        config.update(port=args.port)
    if args.password is not None:
        config.update(password=args.password)
    if args.servers_dir:
        config.update(servers_dir=args.servers_dir)
    if args.no_browser:
        config.update(open_browser=False)

    manager = InstanceManager(config)

    if args.check:
        return 0 if environment_check(config, manager) else 1

    if not environment_check(config, manager):
        print("\n自检发现上面标 ✓ 以外的问题，仍会尝试启动面板，"
              "但下载好 Java 之前服务端是跑不起来的。\n")

    host = config.get("host") or "127.0.0.1"
    desired = int(config.get("port") or 8080)
    if not port_available(host, desired):
        new_port = free_port(host, desired + 1)
        if new_port < 0:
            print(f"[面板] 从 {desired + 1} 开始连续 20 个端口都被占用了，请用 --port 指定。")
            return 1
        print(f"[面板] 端口 {desired} 被占用，已改用 {new_port}")
        config.update(port=new_port)

    instances = manager.all()
    print(f"[面板] 已收录 {len(instances)} 个实例"
          + ("：" + "、".join(i.name for i in instances) if instances else "（还没有，去面板里点新建）"))
    total = 0
    for inst in instances:
        for f in inst.root.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
    if total:
        print(f"[面板] 实例目录共占用 {human_bytes(total)}")

    server = PanelServer(config, manager)

    def stop_all(reason: str = "") -> None:
        if reason:
            print(f"\n[面板] {reason}")
        running = [i for i in manager.all() if i.state != "stopped"]
        if not running:
            return
        if args.stop_all_on_exit:
            for inst in running:
                print(f"[面板] 停止实例 {inst.name}")
                try:
                    inst.stop(timeout=20)
                except Exception as exc:  # noqa: BLE001
                    print(f"[面板] 实例 {inst.name} 停止失败：{exc}")
        else:
            print(f"[面板] 注意：{len(running)} 个服务端仍在后台运行"
                  f"（{'、'.join(i.name for i in running)}）")
            print("[面板] 下次启动面板后仍可继续接管它们；"
                  "如需退出时一并关闭，加 --stop-all-on-exit")

    # ---------------------------------------------------------- 选界面
    # 默认：装了 PyQt5 + PyQt-SiliconUI 就用 Qt 原生界面，否则用网页界面。
    # --browser/--app/--desktop/--no-browser 都明确指向网页前端，优先级更高。
    want_qt = bool(args.qt)
    if args.qt and args.web:
        print("[面板] --qt 和 --web 不能同时用。")
        return 1
    if not args.qt and not args.web and not (
            args.browser or args.app or args.desktop or args.no_browser):
        from mcpanel.qtui import qt_available
        want_qt = qt_available()

    # ---------------------------------------------------------- Qt 界面
    if want_qt:
        from mcpanel.qtui import qt_available, qt_missing_reason, run_qt
        from mcpanel.desktop import wait_port
        if not qt_available():
            reason = qt_missing_reason()
            print("[面板] Qt 界面不可用：")
            for line in reason.splitlines():
                print(f"        {line}")
            if args.qt and not has_console():
                # 双击 exe 时没有控制台，上面的说明用户一个字都看不到，
                # 只会觉得"点了没反应"，所以这里必须弹窗。
                from mcpanel.desktop import message_box
                message_box(
                    "这台机器上没装 Qt 界面需要的依赖，暂时只能用网页界面。\n\n"
                    + reason + "\n\n"
                    "现在会用网页界面打开；源码方式运行的话，双击 run_qt.bat 就是 Qt 界面。",
                    error=True)
            if args.qt:
                print("[面板] 已自动改用网页界面。\n")
        else:
            # HTTP 服务照常在后台跑：Qt 界面上的「切换到网页界面」按钮要用它
            server.start_background(open_browser=False)
            url_port = int(config.get("port") or 8080)
            if not wait_port("127.0.0.1", url_port):
                print(f"[面板] 提示：HTTP 服务没在 {url_port} 端口起来，"
                      "「切换到网页界面」可能打不开。")
            print(f"[面板] Qt 界面启动中…网页版在 http://127.0.0.1:{url_port}/")
            code = run_qt(server, config, manager,
                          stop_all_on_exit=args.stop_all_on_exit,
                          selftest=args.ui_selftest)
            print("[面板] Qt 界面已关闭")
            shutdown_ui(server, stop_all, code)

    # ---------------------------------------------------------- 桌面窗口模式
    use_desktop = not (args.browser or args.no_browser)
    if use_desktop:
        from mcpanel.desktop import pywebview_available, run_desktop
        prefer = "webview" if args.desktop else ("app" if args.app else None)
        if prefer == "webview" and not pywebview_available():
            print("[面板] 找不到 pywebview，改用浏览器窗口模式（--app）")
            prefer = "app"
        code = run_desktop(server, config, manager,
                           force_stop=args.stop_all_on_exit, prefer=prefer,
                           selftest=args.ui_selftest,
                           software_render=args.software_render)
        print("[面板] 窗口已关闭")
        shutdown_ui(server, stop_all, code)

    # ------------------------------------------------- 浏览器 / 仅服务模式
    def shutdown(signum=None, frame=None):
        stop_all("正在关闭…")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (AttributeError, ValueError):
        pass

    server.start_background(open_browser=not args.no_browser)
    if args.no_browser:
        print("[面板] 已启动 HTTP 服务（未打开界面），按 Ctrl+C 退出\n")
    else:
        print("[面板] 已在浏览器中打开面板，按 Ctrl+C 退出\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
