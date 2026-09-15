"""服务端实例：进程生命周期、日志缓冲、玩家统计、文件管理。

一个实例 = servers/<名字>/ 目录 + instance.json 配置。
面板支持同时运行多个实例。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from . import downloader, java as javamod, procstat, properties
from .util import (clean_name, decode_bytes, encode_text, human_bytes,
                   now, port_available, read_json, safe_join, unique_name,
                   write_json)

# 日志里的事件识别
RE_JOIN = re.compile(r"\]:\s*([A-Za-z0-9_]{1,16})(?:\[[^\]]*\])?\s+joined the game")
RE_LEAVE = re.compile(r"\]:\s*([A-Za-z0-9_]{1,16})(?:\[[^\]]*\])?\s+left the game")
RE_LIST = re.compile(r"There are (\d+) of a max(?: of)? (\d+) players online:?\s*(.*)",
                     re.IGNORECASE)
RE_DONE = re.compile(r'Done \(([\d.]+)s\)!', re.IGNORECASE)
RE_EULA = re.compile(r"you need to agree to the eula", re.IGNORECASE)
RE_STOPPING = re.compile(r"Stopping (the )?server", re.IGNORECASE)
RE_JAVA_BAD = re.compile(r"(Unsupported class file major version|"
                         r"requires Java|has been compiled by a more recent version)",
                         re.IGNORECASE)

DEFAULT_JVM_LEGACY = (
    "-XX:+UseG1GC -XX:-UseAdaptiveSizePolicy -XX:-OmitStackTraceInFastThrow "
    "-Dfml.ignoreInvalidMinecraftCertificates=true -Dfml.ignorePatchDiscrepancies=true "
    "-Dfile.encoding=UTF-8"
)
# Aikar's flags（现代版本推荐）
DEFAULT_JVM_MODERN = (
    "-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 "
    "-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch "
    "-XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M "
    "-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 "
    "-XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 "
    "-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 "
    "-XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 -Dfile.encoding=UTF-8"
)

JVM_PRESETS = [
    {"id": "legacy", "name": "1.12.2 及以下（Java 8）", "args": DEFAULT_JVM_LEGACY},
    {"id": "aikar", "name": "Aikar's Flags（1.13+ 推荐）", "args": DEFAULT_JVM_MODERN},
    {"id": "none", "name": "不加额外参数", "args": ""},
]

DEFAULT_INSTANCE: dict = {
    "core": "paper",
    "mc_version": "1.20.1",
    "launch": {"type": "jar", "target": "server.jar"},
    "java_path": "",
    "min_memory": 1024,
    "max_memory": 2048,
    "jvm_args": DEFAULT_JVM_LEGACY,
    "server_args": "nogui",
    "accept_eula": False,
    "auto_restart": False,
    "stop_timeout": 90,
    "installed": False,
    "note": "",
    "created_at": 0.0,
    "last_started": 0.0,
    "total_uptime": 0.0,
}


class Instance:
    """一个服务端实例。"""

    def __init__(self, root: Path, manager: "InstanceManager"):
        self.root = Path(root)
        self.name = self.root.name
        self.manager = manager
        self.cfg_path = self.root / "instance.json"
        self.cfg: dict = dict(DEFAULT_INSTANCE)
        self.cfg.update(read_json(self.cfg_path, {}) or {})

        self.proc: subprocess.Popen | None = None
        self.state = "stopped"          # stopped / starting / running / stopping
        self.started_at: float = 0.0
        self.exit_code: int | None = None
        self.ready = False

        self.logs: deque = deque(maxlen=int(manager.config.get("console_buffer", 3000)))
        self._seq = 0
        self._lock = threading.RLock()
        self._reader: threading.Thread | None = None
        self._stop_requested = False
        self._stdin_open = False
        self._log_file = None

        self.players: set[str] = set()
        self.max_players = 20
        self._pending_restart = False
        self._task: dict | None = None

    # ============================================================ 序列化
    def save(self) -> None:
        write_json(self.cfg_path, self.cfg)

    def _log_path(self) -> Path:
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        return logs / "panel-latest.log"

    def snapshot(self) -> dict:
        """给前端的实例概要。"""
        uptime = (now() - self.started_at) if self.state == "running" and self.started_at else 0
        stats = procstat.process_stats(self.proc.pid if self.proc and self.state != "stopped" else None)
        return {
            "name": self.name,
            "core": self.cfg.get("core"),
            "mc_version": self.cfg.get("mc_version"),
            "state": self.state,
            "ready": self.ready,
            "uptime": round(uptime, 1),
            "pid": self.proc.pid if self.proc and self.state != "stopped" else None,
            "mem_used": stats["rss"],
            "cpu": stats["cpu"],
            "max_memory": self.cfg.get("max_memory"),
            "min_memory": self.cfg.get("min_memory"),
            "players": sorted(self.players),
            "player_count": len(self.players),
            "max_players": self.max_players,
            "installed": bool(self.cfg.get("installed")),
            "accept_eula": bool(self.cfg.get("accept_eula")),
            "auto_restart": bool(self.cfg.get("auto_restart")),
            "java_path": self.cfg.get("java_path"),
            "port": properties.read_properties(self.root / "server.properties")
                    .get("server-port", ""),
            "task": self._task,
            "seq": self._seq,
        }

    def detail(self) -> dict:
        """配置字段平铺 + 运行态字段覆盖，方便前端直接读。"""
        data = {k: v for k, v in self.cfg.items() if not str(k).startswith("_")}
        data.update(self.snapshot())
        data["config"] = {k: v for k, v in self.cfg.items() if not str(k).startswith("_")}
        data["root"] = str(self.root)
        data["presets"] = JVM_PRESETS
        return data

    # ============================================================ 控制台
    def _emit(self, text: str, level: str = "info", progress: bool = False) -> None:
        with self._lock:
            if progress and self.logs and self.logs[-1][4]:
                # 同一行百分比刷新：原地覆盖，不新增行
                seq, _, _, _, _ = self.logs[-1]
                self.logs[-1] = (seq, time.time(), text, level, True)
            else:
                self._seq += 1
                self.logs.append((self._seq, time.time(), text, level, progress))
            fp = self._log_file
        if fp:
            try:
                fp.write(text + "\n")
                fp.flush()
            except (OSError, ValueError):
                pass

    def get_logs(self, since: int = 0) -> dict:
        with self._lock:
            entries = [e for e in self.logs if e[0] >= max(0, since)]
            next_seq = self._seq
        return {
            "lines": [{"seq": e[0], "t": e[1], "text": e[2],
                       "level": e[3], "progress": e[4]} for e in entries],
            "next": next_seq,
            "state": self.state,
            "ready": self.ready,
        }

    @staticmethod
    def classify(line: str) -> str:
        low = line.lower()
        if "error" in low or "severe" in low or "exception" in low or "caused by" in low \
                or "\tat " in line:
            return "error"
        if "warn" in low:
            return "warn"
        if line.startswith("> ") or line.startswith("[面板]"):
            return "cmd"
        if "done (" in low or "joined" in low or "left" in low:
            return "ok"
        return "info"

    def _handle_line(self, line: str) -> None:
        """写日志 + 提取事件。"""
        level = self.classify(line)
        self._emit(line, level)
        # 进度行：Preparing spawn area 之类
        text = line.strip()

        m = RE_JOIN.search(text)
        if m:
            self.players.add(m.group(1))
        m = RE_LEAVE.search(text)
        if m:
            self.players.discard(m.group(1))
        m = RE_LIST.search(text)
        if m:
            self.max_players = int(m.group(2))
            raw = (m.group(3) or "").strip()
            self.players = {n.strip() for n in raw.split(",") if n.strip()}
        if RE_DONE.search(text) and self.state == "starting":
            self.state = "running"
            self.ready = True
        if RE_STOPPING.search(text):
            self.state = "stopping"
        if RE_EULA.search(text):
            self.cfg["_eula_needed"] = True
            self._emit("[面板] 服务端要求先同意 EULA，请在「实例设置」里打开“自动同意 EULA”。", "warn")
        if RE_JAVA_BAD.search(text):
            self._emit("[面板] 检测到 Java 版本不匹配，请在「实例设置」里换一个 Java。", "error")

    # ============================================================ 启动
    def resolve_java(self) -> str:
        path = self.cfg.get("java_path") or self.manager.config.get("java_path") or ""
        if path and os.path.isfile(path):
            return path
        javas = self.manager.javas()
        best = javamod.recommend_java(self.cfg.get("mc_version", ""), javas)
        if best:
            self.cfg["java_path"] = best["path"]
            self.save()
            return best["path"]
        which = shutil.which("java")
        if which:
            return which
        raise RuntimeError("没有找到可用的 Java，请先安装 JDK/JRE，或在「实例设置」中手动指定路径")

    def resolve_jar(self) -> Path | None:
        launch = self.cfg.get("launch") or {}
        target = launch.get("target")
        if target:
            p = self.root / target
            if p.exists():
                return p
        found = self.auto_detect_jar()
        if found:
            self.cfg["launch"] = {"type": "jar", "target": found.name}
            self.save()
        return found

    def auto_detect_jar(self) -> Path | None:
        """在自己上传/整合包场景下找一个能用的服务端 jar。"""
        skip = ("installer", "buildtools", "sources", "javadoc")
        jars = [p for p in self.root.glob("*.jar")
                if not any(s in p.name.lower() for s in skip)]
        if not jars:
            return None
        # 优先 server.jar / paper / forge / spigot 这类命名
        for keyword in ("server", "paper", "purpur", "spigot", "forge", "fabric", "craftbukkit"):
            for j in jars:
                if keyword in j.name.lower():
                    return j
        return max(jars, key=lambda p: p.stat().st_mtime)

    def build_command(self) -> list[str]:
        java_exe = self.resolve_java()
        cfg = self.cfg
        cmd = [java_exe,
               f"-Xms{int(cfg.get('min_memory') or 1024)}M",
               f"-Xmx{int(cfg.get('max_memory') or 2048)}M"]
        jvm_args = (cfg.get("jvm_args") or "").strip()
        if jvm_args:
            cmd.extend(_split_args(jvm_args))

        launch = cfg.get("launch") or {"type": "jar", "target": "server.jar"}
        target = launch.get("target") or "server.jar"
        if launch.get("type") == "argsfile":
            cmd.append("@" + str(target).replace("/", os.sep))
        else:
            cmd.extend(["-jar", target])

        server_args = (cfg.get("server_args") or "").strip()
        if server_args:
            cmd.extend(_split_args(server_args))
        return cmd

    def start(self, _restart: bool = False) -> dict:
        with self._lock:
            if self.state in ("running", "starting"):
                raise RuntimeError("实例已经在运行了")
            jar = self.resolve_jar()
            if not jar:
                raise RuntimeError("没有找到服务端 jar，请先在「安装」页下载核心")
            cmd = self.build_command()
            self._stop_requested = False
            self.exit_code = None
            self.ready = False
            self.players.clear()
            self.state = "starting"
            self.cfg["_eula_needed"] = False

        if self.cfg.get("accept_eula"):
            self.write_eula(True)
        self._rotate_log()
        self._emit("=" * 62, "info")
        self._emit(f"[面板] 启动实例 {self.name}（{self.cfg.get('core')} "
                   f"{self.cfg.get('mc_version')}）", "cmd")
        self._emit("[面板] " + " ".join(cmd), "cmd")
        if self._stop_requested is False and _restart:
            self._emit("[面板] 自动重启中…", "warn")

        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(self.root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.state = "stopped"
            self._emit(f"[面板] 启动失败：{exc}", "error")
            self._close_log()
            raise RuntimeError(f"启动失败：{exc}") from exc

        self.started_at = now()
        self.cfg["last_started"] = self.started_at
        self.save()
        self._stdin_open = True
        self._reader = threading.Thread(target=self._read_loop, args=(self.proc,),
                                        name=f"reader-{self.name}", daemon=True)
        self._reader.start()
        procstat.forget(self.proc.pid)
        return {"ok": True, "pid": self.proc.pid}

    def _read_loop(self, proc: subprocess.Popen) -> None:
        """读取服务端输出。

        这里必须保证：无论发生什么异常都不能让读取线程退出。
        否则管道缓冲区写满后，Java 进程会卡在 println 上永不退出。
        """
        stream = proc.stdout
        assert stream is not None
        buf = b""
        try:
            while True:
                try:
                    chunk = stream.read1(8192) if hasattr(stream, "read1") else stream.read(8192)
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                buf += chunk
                while True:
                    idx_n = buf.find(b"\n")
                    idx_r = buf.find(b"\r")
                    candidates = [i for i in (idx_n, idx_r) if i >= 0]
                    if not candidates:
                        break
                    idx = min(candidates)
                    sep = buf[idx:idx + 1]
                    line = buf[:idx]
                    buf = buf[idx + 1:]
                    if sep == b"\r" and buf[:1] == b"\n":
                        buf = buf[1:]
                    text = decode_bytes(line).rstrip()
                    if text:
                        self._safe_handle(text)
            if buf.strip():
                self._safe_handle(decode_bytes(buf).rstrip())
        except Exception as exc:  # noqa: BLE001  兜底，绝不让读取线程静默死掉
            try:
                self._emit(f"[面板] 日志读取异常：{exc}", "error")
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._on_exit(proc)

    def _safe_handle(self, text: str) -> None:
        try:
            self._handle_line(text)
        except Exception as exc:  # noqa: BLE001
            self._emit(f"[面板] 日志解析异常（已忽略）：{exc}", "warn")
            self._emit(text, "info")

    def _on_exit(self, proc: subprocess.Popen) -> None:
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            code = -1
        self._stdin_open = False
        procstat.forget(proc.pid)
        duration = now() - self.started_at if self.started_at else 0
        self.exit_code = code
        self.ready = False
        self.players.clear()
        self.state = "stopped"
        self.proc = None
        self.cfg["total_uptime"] = float(self.cfg.get("total_uptime") or 0) + duration
        self.save()

        if self._stop_requested:
            self._emit(f"[面板] 服务端已停止（运行 {duration:.0f} 秒）", "cmd")
        elif code == 0:
            self._emit("[面板] 服务端正常退出", "cmd")
        else:
            self._emit(f"[面板] 服务端异常退出，退出码 {code}", "error")

        self._close_log()

        if self.cfg.get("auto_restart") and not self._stop_requested:
            self._emit("[面板] 已开启自动重启，5 秒后重新启动…", "warn")
            threading.Thread(target=self._delayed_restart, daemon=True).start()

    def _delayed_restart(self) -> None:
        time.sleep(5)
        if self.state == "stopped" and not self._stop_requested:
            try:
                self.start(_restart=True)
            except RuntimeError as exc:
                self._emit(f"[面板] 自动重启失败：{exc}", "error")

    # ============================================================ 停止
    SHUTDOWN_MARKERS = (
        "stopping the server", "stopping server", "saving worlds",
        "saving players", "all dimensions are saved",
    )
    SAVED_MARKERS = (
        "all dimensions are saved", "closing thread pool", "closing server",
        "all chunks are saved",
    )

    def _recent_logs(self, count: int = 30) -> list[str]:
        with self._lock:
            return [e[2].lower() for e in list(self.logs)[-count:]]

    def _shutdown_started(self) -> bool:
        recent = self._recent_logs()
        return any(m in line for line in recent for m in self.SHUTDOWN_MARKERS)

    def _saved_recently(self) -> bool:
        recent = self._recent_logs()
        return any(m in line for line in recent for m in self.SAVED_MARKERS)

    def _close_stdin(self, proc: subprocess.Popen) -> None:
        """关闭 stdin 让服务端收到 EOF。

        实测 Paper / 原版服务端在控制台线程上阻塞等待 stdin，
        不关掉这个管道进程会一直挂着不退出。
        """
        self._stdin_open = False
        stream, proc.stdin = proc.stdin, None
        if stream:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def stop(self, timeout: int | None = None) -> dict:
        with self._lock:
            proc = self.proc
            if not proc or self.state == "stopped":
                raise RuntimeError("实例当前没有运行")
            if self.state == "stopping":
                raise RuntimeError("正在停止中，请稍候")
            self._stop_requested = True
            self.state = "stopping"

        limit = int(timeout if timeout is not None
                    else (self.cfg.get("stop_timeout") or 90))
        self._emit(f"[面板] 发送 stop 指令，等待服务端保存数据（最多 {limit} 秒）…", "cmd")
        try:
            self.send("stop", echo=False)
        except RuntimeError:
            pass

        deadline = time.time() + limit
        grace_deadline: float | None = None
        stdin_closed = False
        while proc.poll() is None:
            if time.time() > deadline:
                break
            if not stdin_closed and self._shutdown_started():
                self._close_stdin(proc)
                stdin_closed = True
            if grace_deadline is None and self._saved_recently():
                grace_deadline = time.time() + 12
            if grace_deadline and time.time() > grace_deadline:
                self._emit("[面板] 存档已完成但进程没有自行退出，结束残留进程", "warn")
                break
            time.sleep(0.3)

        if proc.poll() is None:
            if not stdin_closed:
                self._close_stdin(proc)
                for _ in range(20):                    # 再给 4 秒
                    if proc.poll() is not None:
                        break
                    time.sleep(0.2)
        if proc.poll() is None:
            if time.time() > deadline:
                self._emit(f"[面板] {limit} 秒内未退出，强制结束进程", "warn")
            self.kill(force=True)
        return {"ok": True, "exit_code": proc.returncode}

    def kill(self, force: bool = True) -> dict:
        with self._lock:
            proc = self.proc
            self._stop_requested = True
        if proc and proc.poll() is None:
            self._emit("[面板] 强制结束服务端进程（未保存的数据可能丢失）" if force
                       else "[面板] 结束服务端进程", "error" if force else "warn")
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
            finally:
                self._close_stdin(proc)
        self.state = "stopped"
        return {"ok": True}

    def kill(self) -> dict:
        with self._lock:
            proc = self.proc
            self._stop_requested = True
        if proc and proc.poll() is None:
            self._emit("[面板] 强制结束进程（未保存的数据可能丢失）", "error")
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        self.state = "stopped"
        return {"ok": True}

    def restart(self) -> dict:
        if self.state in ("running", "starting", "stopping"):
            self.stop()
        time.sleep(1)
        return self.start(_restart=True)

    def send(self, command: str, echo: bool = True) -> dict:
        proc = self.proc
        if not proc or proc.poll() is not None or not proc.stdin or not self._stdin_open:
            raise RuntimeError("实例没有运行，无法发送指令")
        command = (command or "").strip()
        if not command:
            raise RuntimeError("指令为空")
        if echo:
            self._emit(f"> {command}", "cmd")
        try:
            proc.stdin.write(encode_text(command + "\n"))
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"发送失败：{exc}") from exc
        return {"ok": True, "command": command}

    # ============================================================ 文件 / 配置
    def write_eula(self, accept: bool) -> None:
        # eula.txt 由 Java 的 Properties 读取，注释保持纯 ASCII 更稳妥
        text = "# Accepted via MC Panel\n" + ("eula=true\n" if accept else "eula=false\n")
        with open(self.root / "eula.txt", "w", encoding="utf-8", newline="\n") as fp:
            fp.write(text)

    def properties(self) -> dict:
        path = self.root / "server.properties"
        if not path.exists():
            properties.write_properties(path, properties.STARTER_PROPERTIES)
        return properties.build_form(properties.read_properties(path),
                                     self.cfg.get("mc_version", ""))

    def update_properties(self, updates: dict) -> dict:
        path = self.root / "server.properties"
        properties.merge_properties(path, updates)
        return self.properties()

    def list_dir(self, rel: str | None = None) -> dict:
        target = safe_join(self.root, rel)
        if not target.exists():
            raise RuntimeError("目录不存在")
        if target.is_file():
            target = target.parent
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            try:
                st = item.stat()
                size = st.st_size if item.is_file() else None
                mtime = st.st_mtime
            except OSError:
                size, mtime = None, 0
            entries.append({
                "name": item.name,
                "path": item.relative_to(self.root).as_posix(),
                "dir": item.is_dir(),
                "size": size,
                "size_text": human_bytes(size) if size is not None else "",
                "mtime": mtime,
                "editable": item.is_file() and item.suffix.lower() in
                            (".properties", ".json", ".yml", ".yaml", ".txt", ".conf",
                             ".cfg", ".toml", ".md", ".log", ".sh", ".bat", ".csv"),
            })
        parent = None
        if target != self.root:
            parent = target.parent.relative_to(self.root).as_posix() or ""
        return {"path": target.relative_to(self.root).as_posix() if target != self.root else "",
                "parent": parent, "entries": entries}

    def read_file(self, rel: str, limit: int = 2 * 1024 * 1024) -> dict:
        target = safe_join(self.root, rel)
        if not target.is_file():
            raise RuntimeError("文件不存在")
        size = target.stat().st_size
        if size > limit:
            raise RuntimeError(f"文件过大（{human_bytes(size)}），不支持在线编辑")
        if target.suffix.lower() == ".properties":
            # .properties 是 Latin-1 + \uXXXX，交给 properties 模块正确解码
            values, comments = properties.parse(target.read_bytes().decode("latin-1"))
            text = "\n".join(comments + [f"{k}={properties.unescape(v)}"
                                         for k, v in values.items()]) + "\n"
        else:
            with open(target, "rb") as fp:
                text = decode_bytes(fp.read())
        return {"path": rel, "content": text, "size": size, "encoding": "auto"}

    def write_file(self, rel: str, content: str) -> dict:
        target = safe_join(self.root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == ".properties":
            values, comments = properties.parse(content)
            body = "\n".join(comments + [f"{k}={properties.escape(v)}"
                                         for k, v in values.items()]) + "\n"
            target.write_bytes(body.encode("latin-1", "replace"))
        else:
            with open(target, "w", encoding="utf-8", newline="\n") as fp:
                fp.write(content)
        self._emit(f"[面板] 已保存文件 {rel}", "cmd")
        return {"ok": True}

    def delete_entry(self, rel: str) -> dict:
        target = safe_join(self.root, rel)
        if target == self.root:
            raise RuntimeError("不能删除实例根目录")
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            raise RuntimeError(f"删除失败：{exc}") from exc
        self._emit(f"[面板] 已删除 {rel}", "cmd")
        return {"ok": True}

    def make_dir(self, rel: str) -> dict:
        target = safe_join(self.root, rel)
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True}

    def save_upload(self, rel: str, filename: str, data: bytes) -> dict:
        folder = safe_join(self.root, rel) if rel else self.root
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / Path(filename).name
        with open(dest, "wb") as fp:
            fp.write(data)
        self._emit(f"[面板] 已上传 {dest.relative_to(self.root).as_posix()}"
                   f"（{human_bytes(len(data))}）", "cmd")
        return {"ok": True, "path": dest.relative_to(self.root).as_posix()}

    # ============================================================ 安装
    @property
    def task(self) -> dict | None:
        return self._task

    def set_task(self, task: dict | None) -> None:
        self._task = task

    def update_config(self, data: dict) -> dict:
        allowed = {"core", "mc_version", "java_path", "min_memory", "max_memory",
                   "jvm_args", "server_args", "accept_eula", "auto_restart",
                   "note", "launch", "installed", "stop_timeout"}
        for key, value in (data or {}).items():
            if key in allowed:
                self.cfg[key] = value
        if self.cfg.get("accept_eula"):
            self.write_eula(True)
        self.save()
        return self.detail()

    def _rotate_log(self) -> None:
        self._close_log()
        try:
            logs = self.root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            latest = logs / "panel-latest.log"
            if latest.exists() and latest.stat().st_size > 0:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                latest.replace(logs / f"panel-{stamp}.log")
            self._log_file = open(self._log_path(), "a", encoding="utf-8", newline="\n")
        except OSError:
            self._log_file = None

    def _close_log(self) -> None:
        fp, self._log_file = self._log_file, None
        if fp:
            try:
                fp.close()
            except OSError:
                pass


def _split_args(text: str) -> list[str]:
    """按空格切分启动参数，支持引号。"""
    import shlex
    try:
        return shlex.split(text, posix=False)
    except ValueError:
        return text.split()


class InstanceManager:
    """实例集合 + Java 缓存 + 安装任务调度。"""

    def __init__(self, config):
        self.config = config
        self.servers_dir = config.servers_dir
        self._instances: dict[str, Instance] = {}
        self._java_cache: list[dict] = []
        self._java_ts = 0.0
        self._lock = threading.RLock()
        self.reload()

    # ---------------------------------------------------------- 发现
    def reload(self) -> None:
        with self._lock:
            seen = set()
            for child in sorted(self.servers_dir.iterdir()):
                if not child.is_dir():
                    continue
                seen.add(child.name)
                if child.name not in self._instances:
                    self._instances[child.name] = Instance(child, self)
            for name in list(self._instances):
                if name not in seen:
                    del self._instances[name]

    def all(self) -> list[Instance]:
        return [self._instances[k] for k in sorted(self._instances)]

    def get(self, name: str) -> Instance:
        inst = self._instances.get(name)
        if not inst:
            raise KeyError(f"实例 {name} 不存在")
        return inst

    def names(self) -> list[str]:
        return sorted(self._instances)

    # ---------------------------------------------------------- Java
    def javas(self, refresh: bool = False) -> list[dict]:
        with self._lock:
            if refresh or not self._java_cache or now() - self._java_ts > 300:
                extra = [self.config.get("java_path")] if self.config.get("java_path") else []
                self._java_cache = javamod.scan_javas(extra)
                self._java_ts = now()
            return self._java_cache

    # ---------------------------------------------------------- 创建 / 删除
    def create(self, name: str, core: str = "paper", mc_version: str = "1.20.1",
               **overrides) -> Instance:
        name = clean_name(name)
        if not name:
            raise RuntimeError("实例名只能是字母、数字、- 和 _")
        name = unique_name(name, self.names())
        root = self.servers_dir / name
        root.mkdir(parents=True, exist_ok=True)

        cfg = dict(DEFAULT_INSTANCE)
        cfg.update({
            "core": core,
            "mc_version": mc_version,
            "created_at": now(),
            "jvm_args": DEFAULT_JVM_LEGACY if javamod.version_tier(mc_version) == "legacy"
                        else DEFAULT_JVM_MODERN,
                })
        cfg.update({k: v for k, v in overrides.items() if v not in (None, "")})
        write_json(root / "instance.json", cfg)

        props = dict(properties.STARTER_PROPERTIES)
        if overrides.get("port"):
            props["server-port"] = str(overrides["port"])
        if overrides.get("motd"):
            props["motd"] = str(overrides["motd"])
        properties.write_properties(root / "server.properties", props)

        for sub in ("plugins", "mods", "logs", "config"):
            (root / sub).mkdir(exist_ok=True)

        inst = Instance(root, self)
        self._instances[name] = inst
        inst._emit(f"[面板] 实例 {name} 创建成功，请到「安装 / 升级」下载服务端核心", "cmd")
        return inst

    def delete(self, name: str, remove_files: bool = False) -> dict:
        """从面板移除实例；remove_files=True 时连目录一起删。

        注意：删除目录可能失败（文件被占用、权限不足），
        这种情况下仍要把它从面板移除并如实返回原因，不能让接口断开。
        """
        inst = self.get(name)
        if inst.state != "stopped":
            try:
                inst.kill()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._instances.pop(name, None)

        result = {"ok": True, "removed_files": False, "name": name}
        if not remove_files:
            return result

        error = ""
        try:
            shutil.rmtree(inst.root, ignore_errors=False)
            result["removed_files"] = True
        except OSError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001  删除钩子/杀软拦截等
            error = str(exc) or exc.__class__.__name__

        if error:
            # 兜底：逐项删，尽量把能删的都删掉
            leftovers = 0
            for item in sorted(inst.root.rglob("*"), reverse=True):
                try:
                    if item.is_dir():
                        item.rmdir()
                    else:
                        item.unlink()
                except OSError:
                    leftovers += 1
            try:
                inst.root.rmdir()
                result["removed_files"] = True
            except OSError:
                result["ok"] = True
                result["warning"] = (
                    f"实例已从面板移除，但目录未能完全删除（残留 {leftovers} 项）：{error}。"
                    f"可手动删除 {inst.root}")
            return result
        return result

    # ---------------------------------------------------------- 安装任务
    def start_install(self, name: str, core: str, mc_version: str,
                      build: str | None = None) -> dict:
        inst = self.get(name)
        if inst.state != "stopped":
            raise RuntimeError("请先停止服务端再安装核心")
        if inst._task and inst._task.get("running"):
            raise RuntimeError("已有安装任务在进行")

        task = {"running": True, "phase": "准备", "percent": 0.0,
                "message": "初始化…", "error": "", "core": core,
                "version": mc_version, "started": now()}
        inst.set_task(task)

        def worker():
            try:
                java_path = inst.resolve_java()
            except RuntimeError:
                java_path = None

            def progress(phase: str, pct: float, msg: str):
                task.update({"phase": phase, "percent": round(pct, 1), "message": msg})

            def log(line: str):
                inst._emit(str(line), "info")

            try:
                launch = downloader.install(
                    inst.root, core, mc_version, build,
                    java_path=java_path, progress=progress, log=log)
                inst.cfg["launch"] = launch
                inst.cfg["core"] = core
                inst.cfg["mc_version"] = mc_version
                inst.cfg["installed"] = True
                inst.save()
                task.update({"running": False, "percent": 100.0, "phase": "完成",
                             "message": "安装完成"})
                inst._emit(f"[面板] 核心安装完成：{core} {mc_version}", "ok")
            except Exception as exc:  # noqa: BLE001
                task.update({"running": False, "error": str(exc), "phase": "失败"})
                inst._emit(f"[面板] 安装失败：{exc}", "error")

        threading.Thread(target=worker, name=f"install-{name}", daemon=True).start()
        return task

    # ---------------------------------------------------------- 批量
    def running_count(self) -> int:
        return sum(1 for i in self._instances.values()
                   if i.state in ("running", "starting", "stopping"))
