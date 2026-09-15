"""把 mc-panel 打包成可分发 zip（纯标准库，跨平台）。"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT.parent
VERSION = "1.0.0"
TOP = "mc-panel"
OUT = DIST / f"mc-panel-v{VERSION}-src.zip"

# 打进包里的内容
INCLUDE_FILES = [
    "panel.py", "run.bat", "run.sh", "README.md", ".gitignore",
    # 打包工具链：拿着源码包的人也能自己打出 exe
    "build_exe.py", "build_exe.bat", "build_zip.py", "make_icon.py", "app.ico",
]
INCLUDE_DIRS = ["mcpanel", "docs"]

# 这些不打包
EXCLUDE_PARTS = {"__pycache__", ".git", ".preview", ".buildenv", "build", "dist"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".part", ".log"}


def wanted(path: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in path.parts):
        return False
    if path.suffix in EXCLUDE_SUFFIX:
        return False
    if path.name in ("panel.json",):
        return False
    return True


def collect() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for name in INCLUDE_FILES:
        src = ROOT / name
        if src.is_file():
            items.append((src, f"{TOP}/{name}"))
        else:
            print(f"  ! 缺少文件：{name}")
    for name in INCLUDE_DIRS:
        base = ROOT / name
        for src in sorted(base.rglob("*")):
            if src.is_file() and wanted(src.relative_to(ROOT)):
                items.append((src, f"{TOP}/{src.relative_to(ROOT).as_posix()}"))
    return items


def main() -> int:
    if OUT.exists():
        OUT.unlink()
    items = collect()
    if not items:
        print("没有可打包的文件")
        return 1

    try:
        with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            # 解压后自动带 UTF-8 文件名（Windows 的资源管理器依赖这个标记）
            zf.comment = f"MC Panel {VERSION} - Minecraft 面板开服器".encode("utf-8")
            for src, arc in items:
                zi = zipfile.ZipInfo.from_file(src, arc)
                zi.flag_bits |= 0x800
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(src, "rb") as fp:
                    zf.writestr(zi, fp.read())
    except OSError as exc:
        print(f"写 zip 失败：{exc}")
        return 1

    size = OUT.stat().st_size
    print(f"已生成：{OUT}")
    print(f"体积：{size / 1024:.1f} KB，共 {len(items)} 个文件\n")
    total = 0
    with zipfile.ZipFile(OUT) as zf:
        bad = zf.testzip()
        if bad:
            print(f"  ! 压缩包损坏：{bad}")
            return 1
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            total += info.file_size
            print(f"  {info.filename:<48} {info.file_size:>7} B")
    print(f"\n原始体积：{total / 1024:.1f} KB -> 压缩后 {size / 1024:.1f} KB"
          f"（压缩率 {(1 - size / total) * 100:.0f}%）")
    print("校验：CRC 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
