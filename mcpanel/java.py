"""本机 Java 环境探测。

Minecraft 服务端对 Java 版本很敏感：
    1.16.5 及以前  -> Java 8
    1.17 ~ 1.19    -> Java 17
    1.20.5 及以后  -> Java 21
本模块负责找出机器上所有可用的 java.exe，并给出推荐版本。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .util import decode_bytes

JAVA_VERSION_RE = re.compile(r'version "([^"]+)"')
# Java 8 及更早的版本号形如 "1.8.0_202"，主版本要取第二段
JAVA8_LAYOUT_RE = re.compile(r'version "1\.(\d+)[._]')


def parse_java_version(raw: str) -> int | None:
    """把 java -version 的输出解析成主版本号（8 / 17 / 21 ...）。"""
    if not raw:
        return None
    m = JAVA8_LAYOUT_RE.search(raw)
    if m:
        return int(m.group(1))
    m = re.search(r'version "(\d+)', raw)
    if m:
        return int(m.group(1))
    return None


def probe(java_exe: str | Path) -> dict | None:
    """运行 java -version，返回 {path, version, raw, arch}。"""
    exe = str(java_exe)
    if not os.path.isfile(exe):
        return None
    try:
        proc = subprocess.run(
            [exe, "-version"],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = decode_bytes(proc.stderr or b"") + decode_bytes(proc.stdout or b"")
    major = parse_java_version(raw)
    if major is None:
        return None
    arch = "x64" if "64-Bit" in raw else ("x86" if "32-Bit" in raw else "")
    return {
        "path": exe,
        "version": major,
        "raw": raw.strip().splitlines()[0] if raw.strip() else "",
        "arch": arch,
    }


def _candidate_paths() -> list[str]:
    """穷举常见安装位置。"""
    found: list[str] = []

    def add(p: str | None):
        if p and p not in found:
            found.append(p)

    exe_name = "java.exe" if os.name == "nt" else "java"

    # 1) JAVA_HOME
    jh = os.environ.get("JAVA_HOME")
    if jh:
        add(os.path.join(jh, "bin", exe_name))

    # 2) PATH
    on_path = shutil.which("java")
    if on_path:
        add(on_path)

    # 3) 常见目录扫描（Windows）
    if os.name == "nt":
        roots = [
            r"C:\Program Files\Java",
            r"C:\Program Files (x86)\Java",
            r"C:\Program Files\Eclipse Adoptium",
            r"C:\Program Files\Microsoft",
            r"C:\Program Files\Zulu",
            r"C:\Program Files\BellSoft",
            r"C:\Program Files\Amazon Corretto",
            r"C:\Program Files\Semeru",
            r"C:\ProgramData\Oracle\Java\javapath",
        ]
        for root in roots:
            if not os.path.isdir(root):
                continue
            # 目录本身可能就是 bin 的父级
            direct = os.path.join(root, exe_name)
            if os.path.isfile(direct):
                add(direct)
            try:
                for entry in sorted(os.listdir(root), reverse=True):
                    add(os.path.join(root, entry, "bin", exe_name))
            except OSError:
                continue
    else:
        for root in ("/usr/lib/jvm", "/Library/Java/JavaVirtualMachines",
                     os.path.expanduser("~/.sdkman/candidates/java")):
            if not os.path.isdir(root):
                continue
            try:
                for entry in sorted(os.listdir(root)):
                    add(os.path.join(root, entry, "bin", exe_name))
                    add(os.path.join(root, entry, "Contents", "Home", "bin", exe_name))
            except OSError:
                continue

    return found


def scan_javas(extra: list[str] | None = None) -> list[dict]:
    """扫描全部 Java，按版本升序返回（同路径去重）。"""
    results: list[dict] = []
    seen: set[str] = set()
    for path in list(extra or []) + _candidate_paths():
        try:
            key = os.path.normcase(os.path.realpath(path))
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        info = probe(path)
        if info:
            results.append(info)
    results.sort(key=lambda x: (x["version"], x["path"]))
    return results


def recommend_java(mc_version: str, javas: list[dict]) -> dict | None:
    """按 MC 版本挑一个最合适的 Java。"""
    need = required_java(mc_version)
    if not javas:
        return None
    exact = [j for j in javas if j["version"] == need]
    if exact:
        return exact[-1]
    higher = [j for j in javas if j["version"] > need]
    if higher:
        return higher[0]
    return javas[-1]


def required_java(mc_version: str) -> int:
    """根据 MC 版本返回推荐 Java 主版本。"""
    try:
        parts = [int(x) for x in str(mc_version).split(".")[:3]]
    except (ValueError, TypeError):
        return 17
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts
    if major == 1:
        if minor <= 16:
            return 8
        if minor <= 19:
            return 17
        if minor == 20 and patch <= 4:
            return 17
        return 21
    # 快照 / 新版本号体系
    return 21


def version_tier(mc_version: str) -> str:
    """给 UI 用：返回 legacy / mid / modern。"""
    need = required_java(mc_version)
    return {8: "legacy", 17: "mid", 21: "modern"}.get(need, "modern")
