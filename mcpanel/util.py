"""通用工具函数。"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------- 运行形态

def is_frozen() -> bool:
    """是否以 PyInstaller 打包后的 exe 在跑。"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """只读资源（静态文件）所在目录。

    打包后资源被解压到 sys._MEIPASS；直接跑源码时就是 mcpanel/ 包目录。
    """
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "mcpanel"
    return Path(__file__).resolve().parent


def app_root() -> Path:
    """可写的数据根目录（panel.json、servers/ 都放这里）。

    打包后必须是 **exe 所在目录**，否则数据会被写进临时解压目录，退出即丢。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def writable(path: Path) -> bool:
    """目录是否可写（避免把 exe 放进 Program Files 后一脸懵逼）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".mcpanel_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def icon_path() -> Path | None:
    """应用图标 app.ico —— 源码运行在项目根目录，打包后随 exe 一起带上。"""
    candidates = [
        Path(__file__).resolve().parent.parent / "app.ico",   # 源码运行
        app_root() / "app.ico",                              # 用户放在 exe 旁边
        bundle_dir().parent / "app.ico",                     # 打包进去的（_MEIPASS）
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


# ---------------------------------------------------------------- 编码处理

# Minecraft 服务端在 Windows + Java 8 下默认用 GBK 输出，新版则是 UTF-8。
# 这里逐个尝试，避免中文日志变乱码。
_ENCODINGS = ("utf-8", "gbk", "gb18030", "latin-1")


def decode_bytes(data: bytes) -> str:
    """尽最大努力把字节流解码成字符串。"""
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def encode_text(text: str) -> bytes:
    """把命令写进服务端 stdin 时用，优先 UTF-8。"""
    for enc in ("utf-8", "gbk"):
        try:
            return text.encode(enc)
        except UnicodeEncodeError:
            continue
    return text.encode("utf-8", "replace")


# ---------------------------------------------------------------- JSON 读写


def read_json(path: Path, default: Any = None) -> Any:
    """读取 JSON，失败时返回默认值（不抛异常，面板要够健壮）。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj: Any) -> None:
    """原子写入 JSON：先写临时文件再替换，避免面板崩溃写坏配置。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(obj, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- 格式化


def human_bytes(num: float | int | None) -> str:
    if not num:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    value = float(num)
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    return f"{value:.0f} {units[idx]}" if idx == 0 else f"{value:.1f} {units[idx]}"


def human_duration(seconds: float | int | None) -> str:
    if not seconds or seconds < 0:
        return "-"
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}天 {h}小时"
    if h:
        return f"{h}小时 {m}分"
    if m:
        return f"{m}分 {s}秒"
    return f"{s}秒"


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- 网络


def port_available(host: str, port: int) -> bool:
    """检测端口是否可绑定。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host or "0.0.0.0", int(port)))
            return True
        except OSError:
            return False


def local_ip() -> str:
    """拿到本机内网 IP，方便给朋友联机时用。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


# ---------------------------------------------------------------- 其他


def safe_join(root: Path, rel: str | None) -> Path:
    """把用户传来的相对路径限制在 root 之下，阻止 ../ 目录穿越。"""
    root = Path(root).resolve()
    rel = (rel or "").replace("\\", "/").strip()
    target = (root / rel).resolve() if rel else root
    if target != root and root not in target.parents:
        raise ValueError("非法路径")
    return target


def unique_name(base: str, exists: Iterable[str]) -> str:
    """名称冲突时自动加序号。"""
    exists = set(exists)
    if base not in exists:
        return base
    i = 2
    while f"{base}-{i}" in exists:
        i += 1
    return f"{base}-{i}"


def clean_name(name: str) -> str:
    """实例名只允许字母数字下划线短横线，避免奇怪的目录名。"""
    keep = [ch for ch in (name or "").strip() if ch.isalnum() or ch in "-_"]
    return "".join(keep)[:32]
