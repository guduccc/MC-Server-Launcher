"""内置 HTTP 面板服务（基于 http.server，零依赖）。

路由风格：/api/instances/<名字>/<动作>，<名字> 允许中文，故统一用正则匹配。
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import downloader, java as javamod, properties
from .instance import JVM_PRESETS, InstanceManager
from .util import bundle_dir, human_bytes, local_ip, read_json, write_json

# 前端静态资源：源码运行取自 mcpanel/static，打包后取自 _MEIPASS
STATIC_DIR = bundle_dir() / "static"
COOKIE_NAME = "mcpanel_token"
MAX_BODY = 512 * 1024 * 1024          # 单次上传上限 512MB


class Router:
    """极简路由器：(方法, 正则) -> 处理函数。"""

    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, str]] = []

    def add(self, method: str, pattern: str, handler: str) -> None:
        self.routes.append((method.upper(), re.compile("^" + pattern + "$"), handler))

    def match(self, method: str, path: str):
        for m, rx, handler in self.routes:
            if m != method.upper():
                continue
            found = rx.match(path)
            if found:
                return handler, found.groupdict()
        return None, None


router = Router()

router.add("GET", r"/", "index")
router.add("GET", r"/favicon\.ico", "favicon")
router.add("GET", r"/static/(?P<file>[\w./\-]+)", "static")

router.add("POST", r"/api/login", "login")
router.add("POST", r"/api/logout", "logout")
router.add("GET", r"/api/session", "session")
router.add("POST", r"/api/focus-qt", "focus_qt")

router.add("GET", r"/api/overview", "overview")
router.add("GET", r"/api/java", "java_list")
router.add("POST", r"/api/java/refresh", "java_refresh")
router.add("GET", r"/api/cores", "cores")
router.add("GET", r"/api/versions", "versions")
router.add("GET", r"/api/builds", "builds")
router.add("POST", r"/api/settings", "save_settings")
router.add("GET", r"/api/appearance", "appearance")

router.add("POST", r"/api/instances", "instance_create")
router.add("GET", r"/api/instances/(?P<name>[^/]+)", "instance_detail")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/config", "instance_config")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/delete", "instance_delete")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/start", "instance_start")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/stop", "instance_stop")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/kill", "instance_kill")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/restart", "instance_restart")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/command", "instance_command")
router.add("GET", r"/api/instances/(?P<name>[^/]+)/console", "instance_console")
router.add("GET", r"/api/instances/(?P<name>[^/]+)/properties", "instance_props_get")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/properties", "instance_props_set")
router.add("GET", r"/api/instances/(?P<name>[^/]+)/files", "instance_files")
router.add("GET", r"/api/instances/(?P<name>[^/]+)/file", "instance_file_get")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/file", "instance_file_set")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/delete-file", "instance_file_delete")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/mkdir", "instance_mkdir")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/upload", "instance_upload")
router.add("GET", r"/api/instances/(?P<name>[^/]+)/download", "instance_download")
router.add("POST", r"/api/instances/(?P<name>[^/]+)/install", "instance_install")


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "MCPanel"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------- 基础响应
    def log_message(self, fmt, *args):  # 屏蔽默认访问日志
        return

    def handle_one_request(self):
        """吞掉客户端主动断开连接产生的噪声。

        内嵌浏览器（WebView2）会开一批"探测用"连接然后立刻关掉，
        标准库会为每个连接打一整段 traceback，把日志刷得没法看。
        这类断开是正常现象，静默处理即可。
        """
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError,
                BrokenPipeError, TimeoutError, OSError):
            self.close_connection = True

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, data, code: int = 200, extra: dict | None = None) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", extra)

    def _error(self, message: str, code: int = 400) -> None:
        self._json({"ok": False, "error": str(message)}, code)

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("请求体过大")
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ValueError(f"请求体不是合法 JSON：{exc}") from exc

    # ------------------------------------------------------- 登录态
    def _token(self) -> str:
        pwd = self.server.config.get("password") or ""
        return hashlib.sha256(("mcpanel:" + pwd).encode("utf-8")).hexdigest()

    def _logged_in(self) -> bool:
        if not self.server.config.auth_enabled:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        morsel = cookie.get(COOKIE_NAME)
        return bool(morsel and morsel.value == self._token())

    # ------------------------------------------------------- 分发
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        handler, params = router.match(method if method != "HEAD" else "GET", path)
        if not handler:
            self._error("接口不存在", 404)
            return

        if handler in ("login", "static", "index", "favicon", "session"):
            pass
        elif not self._logged_in():
            self._json({"ok": False, "error": "未登录", "need_login": True}, 401)
            return

        try:
            params["query"] = {k: v[-1] for k, v in query.items()}
            getattr(self, "h_" + handler)(**params)
        except (KeyboardInterrupt, SystemExit):
            raise
        except KeyError as exc:
            self._error(f"找不到对象：{exc}", 404)
        except PermissionError as exc:
            self._error(f"没有权限：{exc}", 403)
        except BaseException as exc:  # noqa: BLE001
            # 用 BaseException 兜底：无论如何都要回一个 JSON，
            # 否则浏览器侧只会看到“连接被重置”，排查起来很痛苦。
            self._error(str(exc) or exc.__class__.__name__, 400)

    # ======================================================= 静态资源
    def h_index(self, **kw):
        return self._serve_file(STATIC_DIR / "index.html")

    def h_favicon(self, **kw):
        self._send(204, b"", "image/x-icon")

    def h_static(self, file: str, **kw):
        target = (STATIC_DIR / file).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self._error("非法路径", 403)
            return
        return self._serve_file(target)

    def _serve_file(self, path: Path):
        if not path.is_file():
            self._error("资源不存在", 404)
            return
        ctype, _ = mimetypes.guess_type(str(path))
        mapping = {".js": "application/javascript; charset=utf-8",
                   ".css": "text/css; charset=utf-8",
                   ".html": "text/html; charset=utf-8",
                   ".svg": "image/svg+xml"}
        ctype = mapping.get(path.suffix, ctype or "application/octet-stream")
        if path.suffix in (".html", ".css", ".js"):
            ctype += "" if "charset" in ctype else "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # ======================================================= 会话
    def h_login(self, **kw):
        data = self._body_json()
        if not self.server.config.auth_enabled:
            self._json({"ok": True, "auth_enabled": False})
            return
        if str(data.get("password", "")) != str(self.server.config.get("password")):
            self._json({"ok": False, "error": "密码错误"}, 401)
            return
        self._json({"ok": True}, extra={
            "Set-Cookie": f"{COOKIE_NAME}={self._token()}; Path=/; HttpOnly; SameSite=Lax"
        })

    def h_logout(self, **kw):
        self._json({"ok": True}, extra={"Set-Cookie": f"{COOKIE_NAME}=; Path=/; Max-Age=0"})

    def h_session(self, **kw):
        cfg = self.server.config
        self._json({"ok": True, "auth_enabled": cfg.auth_enabled,
                    "logged_in": self._logged_in()})

    # ======================================================= 总览
    def h_appearance(self, **kw):
        self._json({"ok": True, "panel": self.server.config.public(),
                    "local_ip": local_ip(), "cores": downloader.CORE_INFO,
                    "presets": JVM_PRESETS})

    def h_overview(self, **kw):
        mgr: InstanceManager = self.server.manager
        instances = [inst.snapshot() for inst in mgr.all()]
        self._json({
            "ok": True,
            "panel": self._panel_info(),
            "local_ip": local_ip(),
            "servers_dir": str(mgr.servers_dir),
            "instances": instances,
            "running": mgr.running_count(),
            "javas": mgr.javas(),
            "cores": downloader.CORE_INFO,
            "presets": JVM_PRESETS,
            "server_time": time.time(),
        })

    def h_java_list(self, **kw):
        self._json({"ok": True, "javas": self.server.manager.javas()})

    def h_java_refresh(self, **kw):
        self._json({"ok": True, "javas": self.server.manager.javas(refresh=True)})

    def _panel_info(self) -> dict:
        """面板信息 + Qt 界面是否在跑（网页版据此显示「切换到 Qt 界面」按钮）。"""
        info = dict(self.server.config.public())
        info["qt_running"] = getattr(self.server, "qt_activator", None) is not None
        return info

    def h_focus_qt(self, **kw):
        """把正在运行的 Qt 界面窗口切到前台。"""
        activator = getattr(self.server, "qt_activator", None)
        if activator is None:
            self._json({"ok": False,
                        "error": "当前没有正在运行的 Qt 界面。"
                                 "用 `python panel.py --qt` 启动就能用 Qt 界面。"})
            return
        activator()
        self._json({"ok": True})

    def h_cores(self, **kw):
        self._json({"ok": True, "cores": downloader.CORE_INFO, "presets": JVM_PRESETS})

    def h_versions(self, core: str = "", **kw):
        query = kw.get("query") or {}
        core = core or query.get("core", "paper")
        try:
            versions = downloader.list_versions(core)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"获取版本列表失败：{exc}",
                        "versions": []})
            return
        mc = query.get("mc", "")
        payload = {"ok": True, "core": core, "versions": versions,
                   "total": len(versions)}
        if mc:
            payload["required_java"] = javamod.required_java(mc)
        self._json(payload)

    def h_builds(self, **kw):
        query = kw.get("query") or {}
        core = query.get("core", "paper")
        mc = query.get("mc", "")
        if not mc:
            self._error("缺少 mc 参数")
            return
        builds = downloader.list_builds(core, mc)
        self._json({"ok": True, "core": core, "mc": mc, "builds": builds})

    def h_save_settings(self, **kw):
        data = self._body_json()
        cfg = self.server.config
        patch = {}
        for key in ("servers_dir", "password", "java_path", "console_buffer",
                    "open_browser", "host", "port"):
            if key in data:
                patch[key] = data[key]
        if "console_buffer" in patch:
            try:
                patch["console_buffer"] = max(200, min(50000, int(patch["console_buffer"])))
            except (TypeError, ValueError):
                patch.pop("console_buffer")
        if "port" in patch:
            try:
                patch["port"] = int(patch["port"])
            except (TypeError, ValueError):
                patch.pop("port")
        cfg.update(**patch)
        self._json({"ok": True, "panel": cfg.public()})

    # ======================================================= 实例 CRUD
    def h_instance_create(self, **kw):
        data = self._body_json()
        mgr: InstanceManager = self.server.manager
        name = data.get("name") or f"server-{int(time.time()) % 10000}"
        inst = mgr.create(
            name,
            core=str(data.get("core") or "paper"),
            mc_version=str(data.get("mc_version") or "1.20.1"),
            port=data.get("port"),
            motd=data.get("motd"),
            min_memory=data.get("min_memory"),
            max_memory=data.get("max_memory"),
            java_path=data.get("java_path"),
            jvm_args=data.get("jvm_args"),
        )
        if data.get("accept_eula"):
            inst.update_config({"accept_eula": True})
        auto_install = bool(data.get("auto_install", True)) and inst.cfg.get("core") != "custom"
        if auto_install:
            try:
                mgr.start_install(inst.name, inst.cfg["core"], inst.cfg["mc_version"],
                                  data.get("build"))
            except RuntimeError as exc:
                inst._emit(f"[面板] 自动安装未启动：{exc}", "warn")
        self._json({"ok": True, "instance": inst.snapshot(), "detail": inst.detail()})

    def h_instance_detail(self, name: str, **kw):
        self._json({"ok": True, "detail": self.server.manager.get(name).detail()})

    def h_instance_config(self, name: str, **kw):
        inst = self.server.manager.get(name)
        detail = inst.update_config(self._body_json())
        inst._emit("[面板] 实例设置已更新", "cmd")
        self._json({"ok": True, "detail": detail})

    def h_instance_delete(self, name: str, **kw):
        data = self._body_json()
        self._json(self.server.manager.delete(name, bool(data.get("remove_files"))))

    def h_instance_start(self, name: str, **kw):
        self._json(self.server.manager.get(name).start())

    def h_instance_stop(self, name: str, **kw):
        self._json(self.server.manager.get(name).stop())

    def h_instance_kill(self, name: str, **kw):
        self._json(self.server.manager.get(name).kill())

    def h_instance_restart(self, name: str, **kw):
        self._json(self.server.manager.get(name).restart())

    def h_instance_command(self, name: str, **kw):
        data = self._body_json()
        self._json(self.server.manager.get(name).send(str(data.get("command") or "")))

    def h_instance_console(self, name: str, **kw):
        inst = self.server.manager.get(name)
        query = kw.get("query") or {}
        try:
            since = int(query.get("since", 0))
        except (TypeError, ValueError):
            since = 0
        data = inst.get_logs(since)
        data["ok"] = True
        data["snapshot"] = inst.snapshot()
        self._json(data)

    # ======================================================= server.properties
    def h_instance_props_get(self, name: str, **kw):
        inst = self.server.manager.get(name)
        form = inst.properties()
        form["ok"] = True
        form["path"] = str(inst.root / "server.properties")
        self._json(form)

    def h_instance_props_set(self, name: str, **kw):
        inst = self.server.manager.get(name)
        data = self._body_json()
        updates = data.get("updates") or {}
        clean = {str(k): ("" if v is None else
                          ("true" if v is True else "false" if v is False else str(v)))
                 for k, v in updates.items()}
        inst.update_properties(clean)
        inst._emit(f"[面板] 已更新 server.properties（{len(clean)} 项）", "cmd")
        self._json({"ok": True, "saved": list(clean)})

    # ======================================================= 文件管理
    def h_instance_files(self, name: str, **kw):
        inst = self.server.manager.get(name)
        query = kw.get("query") or {}
        data = inst.list_dir(query.get("path", ""))
        data["ok"] = True
        self._json(data)

    def h_instance_file_get(self, name: str, **kw):
        inst = self.server.manager.get(name)
        query = kw.get("query") or {}
        data = inst.read_file(query.get("path", ""))
        data["ok"] = True
        self._json(data)

    def h_instance_file_set(self, name: str, **kw):
        inst = self.server.manager.get(name)
        data = self._body_json()
        inst.write_file(data.get("path") or "", data.get("content") or "")
        self._json({"ok": True})

    def h_instance_file_delete(self, name: str, **kw):
        inst = self.server.manager.get(name)
        data = self._body_json()
        inst.delete_entry(data.get("path") or "")
        self._json({"ok": True})

    def h_instance_mkdir(self, name: str, **kw):
        inst = self.server.manager.get(name)
        data = self._body_json()
        inst.make_dir(data.get("path") or "")
        self._json({"ok": True})

    def h_instance_upload(self, name: str, **kw):
        inst = self.server.manager.get(name)
        query = kw.get("query") or {}
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._error("没有收到文件内容")
            return
        if length > MAX_BODY:
            self._error(f"文件过大，上限 {human_bytes(MAX_BODY)}")
            return
        data = self.rfile.read(length)
        filename = query.get("filename") or f"upload-{int(time.time())}.bin"
        self._json(inst.save_upload(query.get("path", ""), filename, data))

    def h_instance_download(self, name: str, **kw):
        inst = self.server.manager.get(name)
        query = kw.get("query") or {}
        from .util import safe_join
        target = safe_join(inst.root, query.get("path", ""))
        if not target.is_file():
            self._error("文件不存在", 404)
            return
        ctype, _ = mimetypes.guess_type(str(target))
        self._send(200, target.read_bytes(),
                   ctype or "application/octet-stream",
                   {"Content-Disposition":
                    f'attachment; filename="{target.name.encode("ascii", "ignore").decode() or "file"}"'})

    # ======================================================= 安装
    def h_instance_install(self, name: str, **kw):
        inst = self.server.manager.get(name)
        data = self._body_json()
        core = str(data.get("core") or inst.cfg.get("core") or "paper")
        mc_version = str(data.get("mc_version") or inst.cfg.get("mc_version") or "1.20.1")
        if mc_version != inst.cfg.get("mc_version") or core != inst.cfg.get("core"):
            inst.update_config({"mc_version": mc_version, "core": core})
        task = self.server.manager.start_install(name, core, mc_version, data.get("build"))
        self._json({"ok": True, "task": task})


class PanelServer:
    """面板服务器。"""

    def __init__(self, config, manager: InstanceManager | None = None, quiet: bool = False):
        self.config = config
        self.manager = manager or InstanceManager(config)
        self.quiet = quiet
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        # Qt 界面在跑时会把自己的窗口激活函数挂到这里，
        # 网页版点「切换到 Qt 界面」就通过它把窗口叫到前台。
        self.qt_activator = None

    def build(self) -> ThreadingHTTPServer:
        class Bound(PanelHandler):
            """把面板配置挂到 handler 类上，供 self.server 之外的地方取用。"""

        Bound.manager = self.manager  # type: ignore[attr-defined]
        Bound.config = self.config    # type: ignore[attr-defined]

        host = self.config.get("host") or "127.0.0.1"
        port = int(self.config.get("port") or 8080)
        httpd = ThreadingHTTPServer((host, port), Bound)
        httpd.daemon_threads = True
        # BaseHTTPRequestHandler 通过 self.server 访问，这里把两个属性挂上去
        httpd.manager = self.manager          # type: ignore[attr-defined]
        httpd.config = self.config            # type: ignore[attr-defined]
        # 注意：请求处理器里的 self.server 是这个 httpd 对象，不是 PanelServer 包装，
        # 所以 Qt 钩子必须挂到 httpd 上才读得到。
        httpd.qt_activator = getattr(self, "qt_activator", None)   # type: ignore[attr-defined]
        self.httpd = httpd
        return httpd

    def serve_forever(self) -> None:
        self.build().serve_forever()

    def start_background(self, open_browser: bool = False) -> None:
        httpd = self.build()
        self.thread = threading.Thread(target=httpd.serve_forever,
                                       name="panel-http", daemon=True)
        self.thread.start()
        host = self.config.get("host") or "127.0.0.1"
        port = int(self.config.get("port") or 8080)
        url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        url = f"http://{url_host}:{port}/"
        if not self.quiet:
            print(f"[面板] 已启动：{url}")
            if host not in ("127.0.0.1", "localhost"):
                print(f"[面板] 局域网访问：http://{local_ip()}:{port}/")
        if open_browser and self.config.get("open_browser", True):
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    def set_qt_activator(self, func) -> None:
        """登记 Qt 界面的窗口激活函数（网页版据此显示并触发「切换到 Qt 界面」）。"""
        self.qt_activator = func
        httpd = getattr(self, "httpd", None)
        if httpd is not None:
            httpd.qt_activator = func
    
    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
