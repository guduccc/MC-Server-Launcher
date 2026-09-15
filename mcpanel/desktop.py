"""桌面窗口模式：把面板装进原生窗口，不用打开浏览器。

实现分三档，逐级降级：
    1. pywebview + WebView2（首选）—— 真正的原生窗口，无地址栏、无标签页，
       窗口图标和标题都是我们自己的
    2. 浏览器 app 模式（`msedge --app=` / `chrome --app=`）—— 无地址栏的独立窗口，
       不需要任何 Python 依赖
    3. 系统默认浏览器打开标签页（兜底）

窗口关闭时如果还有服务端在跑，会弹原生对话框问要不要一并停掉。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

WINDOW_TITLE = "MC Panel · Minecraft 面板开服器"
WINDOW_SIZE = (1440, 900)
WINDOW_MIN = (1024, 680)


# ---------------------------------------------------------------- 原生提示

def message_box(text: str, title: str = WINDOW_TITLE, error: bool = False) -> None:
    """原生 MessageBox —— 打包成无控制台 exe 时，出错全靠它让人知道。"""
    if os.name != "nt":
        print(f"[{title}] {text}", file=sys.stderr)
        return
    try:
        flags = 0x00000010 if error else 0x00000040   # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), flags | 0x0)
    except Exception:  # noqa: BLE001
        print(f"[{title}] {text}", file=sys.stderr)


# ---------------------------------------------------------------- 能力探测

def pywebview_available() -> bool:
    try:
        import webview  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def find_app_browser() -> str | None:
    """找一个能用 --app 模式启动的 Chromium 内核浏览器。"""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    for name in ("msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def wait_port(host: str, port: int, timeout: float = 15.0) -> bool:
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.4)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.15)
    return False


# ---------------------------------------------------------------- 关闭确认

def _make_closing_handler(window, manager, force_stop: bool):
    def on_closing():
        running = [i for i in manager.all() if i.state != "stopped"]
        if not running:
            return True

        names = "、".join(i.name for i in running)
        if force_stop:
            question = (f"还有 {len(running)} 个服务端在运行：{names}\n\n"
                        f"确定要停止它们并退出面板吗？")
        else:
            question = (f"还有 {len(running)} 个服务端在运行：{names}\n\n"
                        f"关闭窗口后面板退出，服务端会继续在后台运行。\n"
                        f"要现在一并停止吗？")
        try:
            stop_now = bool(window.create_confirmation_dialog("退出 MC Panel", question))
        except Exception:  # noqa: BLE001  拿不到对话框就不拦
            stop_now = force_stop

        if stop_now:
            # 先在后台把 stop 指令全部发出去，服务端会自己存档退出；
            # 这里只做有限等待，避免关窗口时界面卡死几十秒。
            for inst in running:
                try:
                    inst.send("stop", echo=False)
                    inst._stop_requested = True
                except Exception:  # noqa: BLE001
                    pass
            deadline = time.time() + 12
            while time.time() < deadline:
                if all(i.state == "stopped" for i in running):
                    break
                time.sleep(0.4)
        return True

    return on_closing


# ---------------------------------------------------------------- 三档实现

def _marker(text: str) -> None:
    """打一个自检标记。

    --noconsole 打包后没有标准输出，setup_console() 会把它重定向到
    panel-gui.log，所以打包脚本能靠读这个日志判断 GUI 是否真的起来了。
    """
    try:
        print(text, flush=True)
    except Exception:  # noqa: BLE001
        pass


def run_pywebview(url: str, host: str, port: int, manager, config,
                  force_stop: bool, root: Path, selftest: float = 0.0,
                  software_render: bool = False) -> int:
    import webview

    if software_render:
        # 少数机器（虚拟机、远程桌面、老显卡驱动）上 WebView2 的硬件合成会让
        # 窗口一片空白，加这个参数强制走软件渲染。必须在建窗口之前设置。
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--disable-gpu"
        print("[面板] 已启用软件渲染（--disable-gpu）")

    storage = root / ".webview"
    try:
        storage.mkdir(parents=True, exist_ok=True)
    except OSError:
        storage = Path(os.environ.get("TEMP", ".")) / "mc-panel-webview"

    window = webview.create_window(
        WINDOW_TITLE, url,
        width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN,
        background_color="#0d1117",
        confirm_close=False,
        text_select=True,
    )
    window.events.closing += _make_closing_handler(window, manager, force_stop)

    def _on_loaded():
        """页面加载完成。

        JS 必须在 GUI 线程里执行 —— pywebview 的 evaluate_js 从别的线程调用
        会死锁，所以这里挂在 loaded 事件里，而不是自检线程里。
        """
        _marker("UI_PAGE_LOADED")
        if not selftest:
            return
        try:
            probe = window.evaluate_js("""(() => {
                const body = getComputedStyle(document.body);
                const con = document.querySelector('#console');
                const box = con ? con.getBoundingClientRect() : {width:0, height:0};
                const side = document.querySelector('.sidebar');
                const sbox = side ? side.getBoundingClientRect() : {width:0};
                return {
                    title: document.title,
                    vw: window.innerWidth, vh: window.innerHeight,
                    bg: body.backgroundColor,
                    tabs: document.querySelectorAll('.tab').length,
                    console: Math.round(box.width) + 'x' + Math.round(box.height),
                    sidebar: Math.round(sbox.width),
                };
            })()""")
            _marker(
                f"UI_JS_OK title={probe.get('title')!r} "
                f"viewport={probe.get('vw')}x{probe.get('vh')} "
                f"body_bg={probe.get('bg')} tabs={probe.get('tabs')} "
                f"console={probe.get('console')} sidebar={probe.get('sidebar')}")
        except Exception as exc:  # noqa: BLE001
            _marker(f"UI_JS_FAIL {exc}")

    window.events.loaded += _on_loaded

    if selftest:
        def _auto_close():
            time.sleep(max(2.0, selftest))
            try:
                window.destroy()
                _marker("UI_SELFTEST_OK")
            except Exception as exc:  # noqa: BLE001
                _marker(f"UI_SELFTEST_FAIL {exc}")
            # 兜底：万一 GUI 线程没能正常收尾，这里强制结束，绝不允许挂住
            time.sleep(6.0)
            _marker("UI_SELFTEST_FORCED")
            os._exit(0)

        threading.Thread(target=_auto_close, name="ui-selftest", daemon=True).start()
        print(f"[面板] 自检模式：窗口将在 {selftest:.0f} 秒后自动关闭")

    print(f"[面板] 桌面窗口已启动：{url}")
    print("[面板] 关闭窗口即退出；服务端进程不受影响（会在关闭时询问）")
    from .util import icon_path
    start_kwargs = {
        "private_mode": False,
        "storage_path": str(storage),
        "debug": False,
        "http_server": False,
    }
    icon = icon_path()
    if icon:
        start_kwargs["icon"] = str(icon)
    webview.start(**start_kwargs)
    _marker("UI_WINDOW_CLOSED")
    return 0


def run_app_mode(url: str, browser: str, root: Path, force_stop: bool) -> int:
    """用 Chromium 的 --app 模式开一个无地址栏的独立窗口。"""
    profile = root / ".appwindow"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        profile = Path(os.environ.get("TEMP", ".")) / "mc-panel-appwindow"
    cmd = [
        browser,
        f"--app={url}",
        f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    print(f"[面板] 已用 {Path(browser).name} 的窗口模式打开：{url}")
    proc = subprocess.Popen(cmd)
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    return 0


def run_desktop(server, config, manager, force_stop: bool = False,
                prefer=None, selftest: float = 0.0,
                software_render: bool = False) -> int:
    """桌面窗口主入口。prefer: None(自动) / 'webview' / 'app' / 'browser'。"""
    from .util import app_root, local_ip

    root = app_root()
    host = config.get("host") or "127.0.0.1"
    port = int(config.get("port") or 8080)
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{url_host}:{port}/"

    # 服务端必须跑在后台线程：pywebview 要求主线程归它
    server.start_background(open_browser=False)
    if not wait_port(url_host, port):
        message_box(f"面板服务没能在 {url_host}:{port} 上启动。\n"
                    f"端口可能被占用，请换一个：\n\nmc-panel.exe --port 8090",
                    error=True)
        return 1

    mode = prefer
    if mode is None:
        mode = "webview" if pywebview_available() else "app"

    if mode == "webview":
        try:
            _marker("UI_MODE=webview")
            return run_pywebview(url, url_host, port, manager, config,
                                 force_stop, root, selftest, software_render)
        except Exception as exc:  # noqa: BLE001  WebView2 缺失/初始化失败时降级
            print(f"[面板] 原生窗口初始化失败（{exc}），改用浏览器窗口模式")
            _marker(f"UI_WEBVIEW_FAILED {exc}")
            mode = "app"

    if mode == "app":
        browser = find_app_browser()
        if browser:
            _marker("UI_MODE=app")
            return run_app_mode(url, browser, root, force_stop)
        print("[面板] 没找到可用的 Chromium 内核浏览器，退回默认浏览器")
        mode = "browser"

    import webbrowser
    _marker("UI_MODE=browser")
    print(f"[面板] 已在默认浏览器打开：{url}")
    if host not in ("127.0.0.1", "localhost"):
        print(f"[面板] 局域网访问：http://{local_ip()}:{port}/")
    webbrowser.open(url)
    return 0
