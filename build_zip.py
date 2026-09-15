"""把 mc-panel 打包成可分发的源码 zip（纯标准库，跨平台）。

用法：
    python build_zip.py                  # 打包并自动校验
    python build_zip.py --list           # 只看会打进去哪些文件，不打包
    python build_zip.py --no-verify      # 跳过校验（快，但别这么发版）
    python build_zip.py --out D:\\x.zip  # 指定输出路径

产物：上级目录下的 mc-panel-v<版本>-src.zip，版本号取自 mcpanel/__init__.py。

设计要点：
- **默认自动收录**整个项目，靠 EXCLUDE 排除不该进包的东西 ——
  以后新增源码/文档不用改这个脚本，不会漏打。
- 打完会**解压到临时目录真跑一次 `--check`**，确认发出去的源码是能跑的，
  而不是"文件都在但 import 就炸"。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from mcpanel import __version__ as VERSION      # noqa: E402  版本号单一来源

TOP = "mc-panel"                                 # 解压后的顶层目录名
DEFAULT_OUT = ROOT.parent / f"mc-panel-v{VERSION}-src.zip"

# ---------------------------------------------------------------- 排除规则

# 这些目录整棵不要（名字匹配任意一层）
EXCLUDE_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode", ".preview", "node_modules",
    ".buildenv",      # 打包用的隔离虚拟环境
    "build", "dist",  # PyInstaller 中间产物与 exe
    "servers",        # 用户的实例数据（世界存档动辄几个 G）
    ".webview", ".appwindow",   # 桌面窗口/浏览器 app 模式的运行时数据
    ".venv", "venv", "env",
}

# 这些后缀不要
EXCLUDE_SUFFIX = {
    ".pyc", ".pyo", ".pyd", ".part", ".log", ".spec",
    ".zip", ".7z", ".rar", ".exe", ".msi", ".dll", ".pdb",   # 源码包不放二进制/嵌套包
    ".tmp", ".bak", ".swp",
}

# 这些具体文件名不要
EXCLUDE_NAMES = {
    "panel.json",         # 面板运行配置（含端口/密码，属于用户环境）
    "panel-gui.log",
    "verify-stdout.log",
    "Thumbs.db", "desktop.ini", ".DS_Store",
}

# 校验用：这些文件缺一个都算打包失败
REQUIRED = [
    "panel.py", "README.md", ".gitignore",
    "mcpanel/__init__.py", "mcpanel/config.py", "mcpanel/desktop.py",
    "mcpanel/downloader.py", "mcpanel/instance.py", "mcpanel/java.py",
    "mcpanel/properties.py", "mcpanel/procstat.py", "mcpanel/util.py",
    "mcpanel/web.py",
    "mcpanel/static/index.html", "mcpanel/static/app.js", "mcpanel/static/style.css",
    # 打包工具链：让拿到源码包的人也能自己打出 exe / 重新打包源码
    "build_exe.py", "build_exe.bat", "build_zip.py", "build_zip.bat",
    "make_icon.py", "app.ico",
]


def wanted(rel: Path) -> bool:
    """rel 是相对项目根的路径。"""
    if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
        return False
    if rel.name in EXCLUDE_NAMES:
        return False
    if rel.suffix.lower() in EXCLUDE_SUFFIX:
        return False
    if rel.name.startswith("~") or rel.name.endswith("~"):
        return False
    return True


def collect() -> list[tuple[Path, str]]:
    """自动收录项目里所有该进包的文件。"""
    items: list[tuple[Path, str]] = []
    for src in sorted(ROOT.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(ROOT)
        if wanted(rel):
            items.append((src, f"{TOP}/{rel.as_posix()}"))
    return items


# ---------------------------------------------------------------- 打包

def make_zip(items: list[tuple[Path, str]], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再原子替换：既不产生"删除"动作（高安全策略环境会拦删除），
    # 也不会出现"打包到一半留下半个坏 zip"
    tmp = out.with_suffix(".zip.part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            # 解压后自动带 UTF-8 文件名（Windows 资源管理器依赖这个标记）
            zf.comment = f"MC Panel {VERSION} - Minecraft 面板开服器".encode("utf-8")
            for src, arc in items:
                zi = zipfile.ZipInfo.from_file(src, arc)
                zi.flag_bits |= 0x800
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(src, "rb") as fp:
                    zf.writestr(zi, fp.read())
        os.replace(tmp, out)
    except OSError as exc:
        print(f"✗ 写 zip 失败：{exc}")
        return 1
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return 0


def verify(zip_path: Path) -> bool:
    """校验压缩包：必备文件、CRC、以及"解压出来真的能跑"。"""
    print("\n校验压缩包")
    ok = True
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())

        missing = [r for r in REQUIRED if f"{TOP}/{r}" not in names]
        print(f"  {'✓' if not missing else '✗'} 必备文件          "
              f"{len(REQUIRED) - len(missing)}/{len(REQUIRED)}"
              + ("" if not missing else f"  缺：{', '.join(missing)}"))
        ok &= not missing

        bad = zf.testzip()
        print(f"  {'✓' if bad is None else '✗'} CRC 完整性        "
              f"{'全部通过' if bad is None else '损坏：' + str(bad)}")
        ok &= bad is None

        leaks = [n for n in names
                 if any(part in EXCLUDE_DIRS for part in n.split("/")[1:-1])
                 or n.split("/")[-1] in EXCLUDE_NAMES]
        print(f"  {'✓' if not leaks else '✗'} 未混入运行期产物   "
              f"{'干净' if not leaks else ', '.join(leaks[:5])}")
        ok &= not leaks

        exes = [n for n in names if n.lower().endswith((".exe", ".dll"))]
        print(f"  {'✓' if not exes else '✗'} 未混入二进制       "
              f"{'干净' if not exes else ', '.join(exes[:5])}")
        ok &= not exes

        total = sum(i.file_size for i in zf.infolist())
        packed = zip_path.stat().st_size
        print(f"  ✓ 体积             原始 {total / 1024:.1f} KB -> 压缩后 "
              f"{packed / 1024:.1f} KB（{len(names)} 个文件）")

        # 真跑一次：解压到临时目录执行 --check
        tmpdir = Path(tempfile.mkdtemp(prefix="mcpanel-zipcheck-"))
        try:
            zf.extractall(tmpdir)
            pkg = tmpdir / TOP
            proc = subprocess.run(
                [sys.executable, "panel.py", "--check"], cwd=str(pkg),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180)
            ran = proc.returncode == 0
            print(f"  {'✓' if ran else '✗'} 解压后能运行      "
                  f"panel.py --check 退出码 {proc.returncode}")
            if ran:
                for line in (proc.stdout or "").splitlines():
                    if any(k in line for k in ("运行方式", "数据目录", "目录可写",
                                               "界面模式", "Java        ", "面板鉴权")):
                        print("      " + line.rstrip())
            else:
                tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
                for line in tail[-12:]:
                    print("      " + line)
            ok &= ran
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"  ✗ 解压后运行失败    {exc}")
            ok = False
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 mc-panel 源码 zip")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出路径")
    ap.add_argument("--list", action="store_true", help="只列出会打进去的文件")
    ap.add_argument("--no-verify", action="store_true", help="跳过校验")
    args = ap.parse_args()

    items = collect()
    if not items:
        print("没有可打包的文件")
        return 1

    print(f"MC Panel 源码打包  v{VERSION}")
    print("=" * 66)
    if args.list:
        for _, arc in items:
            print("  " + arc)
        print(f"\n共 {len(items)} 个文件（--list 模式，未打包）")
        return 0

    total = sum(src.stat().st_size for src, _ in items)
    print(f"收录 {len(items)} 个文件，原始体积 {total / 1024:.1f} KB")

    if make_zip(items, args.out) != 0:
        return 1
    print(f"\n✓ 已生成 {args.out}")
    print(f"  {args.out.stat().st_size / 1024:.1f} KB")

    if args.no_verify:
        print("\n（已跳过校验）")
        return 0

    if not verify(args.out):
        print("\n✗ 校验未通过，这个包不要发出去")
        return 1

    print(f"\n{'=' * 66}")
    print(f"完成：{args.out}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
