"""把 mc-panel 打包成 Windows 单文件 exe（PyInstaller）。

用法：
    python build_exe.py                 # 打包两个版本（默认）
    python build_exe.py --mode desktop  # 只打桌面窗口版
    python build_exe.py --mode console  # 只打控制台版
    python build_exe.py --check         # 只检查打包环境

产物：
    dist/mc-panel.exe          桌面窗口版（原生窗口，无控制台，双击即用）
    dist/mc-panel-console.exe  控制台版（终端 + 浏览器界面，零额外依赖）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from mcpanel import __version__ as VERSION      # noqa: E402  版本号单一来源

# 两个发行版本
#   desktop —— 桌面窗口版：原生窗口（WebView2），无控制台，双击就是一个应用
#   console —— 控制台版：保留终端 + 浏览器界面，零额外依赖
VARIANTS = {
    "desktop": {
        "name": "mc-panel",
        "console": False,
        "title": "桌面窗口版",
        "desc": "原生窗口（WebView2），双击直接是应用",
        # pywebview / pythonnet 自带 PyInstaller 钩子，不用额外 collect；
        # app.ico 放到包根，运行时给窗口当图标（util.icon_path 会去找）
        "extra": ["--add-data", f"{ROOT / 'app.ico'}{os.pathsep}."],
    },
    "console": {
        "name": "mc-panel-console",
        "console": True,
        "title": "控制台版",
        "desc": "终端 + 浏览器界面，零额外依赖",
        "extra": ["--exclude-module", "webview", "--exclude-module", "clr",
                  "--exclude-module", "pythonnet", "--exclude-module", "clr_loader"],
    },
}

# 这些模块肯定用不到，排掉可以显著减小体积。
# 注意：**不要**排除 distutils —— PyInstaller 自带 hook-distutils 会把它别名到
# setuptools._distutils，一旦排除就会报
# "Target module distutils already imported as ExcludedModule(...)"。
EXCLUDES = [
    "tkinter", "unittest", "pydoc", "doctest", "lib2to3",
    "setuptools", "pip", "wheel", "test", "curses", "sqlite3",
    "numpy", "PIL", "matplotlib", "scipy", "pandas",
]

VERSION_FILE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%(v)s), prodvers=(%(v)s), mask=0x3f, flags=0x0,
    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'MC Panel'),
        StringStruct('FileDescription', 'MC Panel - Minecraft 面板开服器(%(title)s)'),
        StringStruct('FileVersion', '%(ver)s'),
        StringStruct('InternalName', '%(name)s'),
        StringStruct('OriginalFilename', '%(name)s.exe'),
        StringStruct('ProductName', 'MC Panel'),
        StringStruct('ProductVersion', '%(ver)s'),
        StringStruct('LegalCopyright', 'MIT License'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


# ================================================================ 环境检查

def check_env() -> bool:
    print("=" * 62)
    print("打包环境检查")
    print("=" * 62)
    ok = True
    print(f"  Python      : {sys.version.split()[0]}")

    try:
        import PyInstaller  # noqa: F401
        from PyInstaller import __version__ as pi_ver
        print(f"  PyInstaller : {pi_ver}")
    except ImportError:
        ok = False
        print("  PyInstaller : ✗ 未安装")
        print(f"                请先执行：{Path(sys.executable).parent / 'pip'} install pyinstaller")

    if os.name != "nt":
        ok = False
        print("  平台        : ✗ 非 Windows，单文件 exe 需要在 Windows 上打包")
    else:
        print("  平台        : Windows ✓")

    # 桌面窗口版依赖 pywebview + WebView2 运行时
    try:
        import webview  # noqa: F401
        try:
            # pywebview 6.x 不再从顶层导出 __version__，走包元数据取
            from importlib.metadata import version as _pkg_version
            wv_ver = _pkg_version("pywebview")
        except Exception:  # noqa: BLE001
            wv_ver = "已安装"
        print(f"  pywebview   : {wv_ver}（桌面窗口版需要）")
        try:
            import clr  # noqa: F401
            print("  pythonnet   : ✓")
        except ImportError:
            print("  pythonnet   : ✗ 缺失，桌面窗口版会打包失败")
            ok = False
        if os.name == "nt":
            import winreg
            key = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
                   r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, key.replace("WOW6432Node\\", "")
                                        if hive == winreg.HKEY_CURRENT_USER else key) as k:
                        print(f"  WebView2    : {winreg.QueryValueEx(k, 'pv')[0]}")
                        break
                except OSError:
                    continue
            else:
                print("  WebView2    : ! 未检测到运行时，桌面窗口版在别的机器上需要装")
                print("                https://developer.microsoft.com/microsoft-edge/webview2/")
    except ImportError:
        print("  pywebview   : - 未安装，将只打控制台版")
        print(f"                想要桌面窗口版：{Path(sys.executable).parent / 'pip'} install pywebview")

    icon = ROOT / "app.ico"
    print(f"  图标        : {'app.ico（%.1f KB）' % (icon.stat().st_size / 1024) if icon.exists() else '缺失，将自动生成'}")

    static = ROOT / "mcpanel" / "static"
    files = sorted(p.name for p in static.iterdir()) if static.is_dir() else []
    print(f"  静态资源    : {len(files)} 个 {files}")
    if len(files) < 3:
        ok = False
        print("                ✗ 静态资源不完整，面板起来会白屏")

    print("=" * 62)
    return ok


def ensure_icon() -> Path:
    icon = ROOT / "app.ico"
    if not icon.exists():
        print("      生成图标 app.ico …")
        subprocess.run([sys.executable, str(ROOT / "make_icon.py")],
                       cwd=str(ROOT), check=True)
    return icon


def version_file_for(key: str) -> Path:
    info = VARIANTS[key]
    path = ROOT / "build" / f"version_info_{key}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    numbers = ", ".join((VERSION.split(".") + ["0", "0", "0", "0"])[:4])
    path.write_text(
        VERSION_FILE % {"v": numbers, "ver": VERSION + ".0",
                        "name": info["name"], "title": info["title"]},
        encoding="utf-8")
    return path


def _best_effort_cleanup(dist: Path) -> None:
    """清掉上一次校验留下的运行期产物。

    全部"删不掉就算了"：PyInstaller 会覆盖同名 exe，
    而某些环境（安全软件、沙箱）会拦截删除，绝不能让清理把打包搞失败。
    """
    for name in (".webview", ".appwindow", "servers", "build"):
        path = dist / name
        if path.is_dir():
            try:
                shutil.rmtree(path)
            except OSError:
                pass
    for name in ("panel-gui.log", "panel.json"):
        path = dist / name
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def _safe_unlink(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


# ================================================================ 打包

def pyinstaller_args(key: str, outdir: Path, work: Path, icon: Path,
                     full: bool = False) -> list[str]:
    info = VARIANTS[key]
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--console" if info["console"] else "--windowed",
        "--name", info["name"],
        "--icon", str(icon),
        "--version-file", str(version_file_for(key)),
        # 注意：--add-data 的相对源路径是按 specpath 解析的，必须给绝对路径；
        # 目标位置要对齐 util.bundle_dir() 的约定。
        "--add-data", f"{ROOT / 'mcpanel' / 'static'}{os.pathsep}mcpanel/static",
        "--distpath", str(outdir),
        "--workpath", str(work),
        "--specpath", str(work),
        "--noupx",
    ]
    if full:
        # --clean 会清掉 PyInstaller 的分析缓存：慢，而且在高安全策略的环境下
        # 大量删除可能被拦截。默认走增量构建，改过依赖/发现构建结果不对时再加 --full。
        args.append("--clean")
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    args += info["extra"]
    args.append(str(ROOT / "panel.py"))
    return args


def build_variant(key: str, dist: Path, work: Path, icon: Path,
                  full: bool = False) -> int:
    """打包单个版本。

    PyInstaller 是直接往 --distpath 里写 exe 的，如果那个位置已经有同名文件，
    它会先删掉再写。某些环境（安全软件、开发沙箱）会拦截删除动作，
    轻则报错重则 fail-closed 直接把构建搞挂。
    所以这里统一输出到一个全新的临时目录，成功后再原子替换到 dist —— 全程不删东西。
    """
    info = VARIANTS[key]
    print(f"\n===== 打包 {info['title']}：{info['name']}.exe =====")
    outdir = work / f"out-{key}-{int(time.time())}"
    outdir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(pyinstaller_args(key, outdir, work, icon, full), cwd=str(ROOT))
    if result.returncode != 0:
        print(f"      ✗ PyInstaller 失败，退出码 {result.returncode}")
        return result.returncode

    produced = outdir / f"{info['name']}.exe"
    if not produced.exists():
        print("      ✗ 没有生成 exe")
        return 1

    dist.mkdir(parents=True, exist_ok=True)
    target = dist / f"{info['name']}.exe"
    try:
        os.replace(produced, target)      # 覆盖已存在的产物，不产生删除操作
    except PermissionError:
        print(f"      ✗ {target.name} 正被占用，无法覆盖。")
        print(f"        请先关闭正在运行的 {info['title']}（{target.name}）再重新打包。")
        return 1
    except OSError as exc:
        print(f"      ✗ 产物搬运失败：{exc}")
        return 1
    print(f"      ✓ 已生成 {target.name}，体积 {target.stat().st_size / 1048576:.1f} MB")
    return 0


# ================================================================ 验证

def free_port(start: int = 18099) -> int:
    for port in range(start, start + 80):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到空闲端口")


def _fetch(opener, base: str, path: str, timeout: float = 15.0):
    with opener.open(base + path, timeout=timeout) as resp:
        return resp.status, resp.read()


def _wait_api(opener, base: str, proc: subprocess.Popen, seconds: float = 45.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return None
        try:
            status, body = _fetch(opener, base, "/api/overview")
            return json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, ConnectionError, OSError, ValueError):
            time.sleep(0.7)
    return None


def _report(results: list[tuple[str, bool, str]]) -> bool:
    ok = True
    for name, passed, detail in results:
        ok &= passed
        print(f"      {'✓' if passed else '✗'} {name:<22} {detail}")
    return ok


def _common_api_checks(opener, base: str, info: dict) -> list[tuple[str, bool, str]]:
    results = []
    status, body = _fetch(opener, base, "/")
    results.append(("GET /", status == 200 and len(body) > 500, f"{status}, {len(body)} B"))
    for asset in ("/static/app.js", "/static/style.css"):
        status, body = _fetch(opener, base, asset)
        results.append((f"GET {asset}", status == 200 and len(body) > 5000,
                        f"{status}, {len(body)} B"))
    results.append(("API /api/overview",
                    bool(info.get("ok")) and info["panel"]["version"] == VERSION,
                    f"ok={info.get('ok')}, v={info['panel']['version']}"))
    cores = [c["id"] for c in info.get("cores", [])]
    results.append(("核心列表", len(cores) == 7, ",".join(cores)))
    javas = info.get("javas", [])
    results.append(("Java 探测", len(javas) > 0,
                    f"{len(javas)} 个：" + ",".join(f"Java {j['version']}" for j in javas)))
    return results


def verify_console(exe: Path, dist: Path) -> int:
    """控制台版：起服务、打接口。"""
    port = free_port()
    print(f"      启动 exe 校验接口（端口 {port}）…")
    proc = subprocess.Popen(
        [str(exe), "--port", str(port), "--no-browser"], cwd=str(dist),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"
    try:
        info = _wait_api(opener, base, proc)
        if not info:
            print("      ✗ 接口没起来")
            return 1
        ok = _report(_common_api_checks(opener, base, info))
        print("      ✓ 控制台版功能完整" if ok else "      ✗ 校验未通过")
        return 0 if ok else 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def verify_desktop(exe: Path, dist: Path) -> int:
    """桌面窗口版：真开一次窗口，读日志确认 WebView2 真的把页面加载出来了。

    --windowed 打包后没有标准输出，panel.py 会把输出重定向到 exe 旁边的
    panel-gui.log，所以这里靠读那个日志拿自检标记。
    """
    log = dist / "panel-gui.log"          # 无控制台时程序自己的日志
    console_log = dist / "verify-stdout.log"   # 有管道时输出落这里
    pre_size = log.stat().st_size if log.is_file() else 0
    port = free_port(19099)
    print(f"      启动 exe 并真实打开窗口自检（端口 {port}）…")
    # 两种输出路径都要收：--windowed 打包后 sys.stdout 可能是 None（程序会转向
    # panel-gui.log），也可能因为继承了管道而照常输出，所以 stdout 直接落到文件。
    try:
        console_log.unlink()
    except OSError:
        pass
    console_fp = open(console_log, "wb")
    proc = subprocess.Popen(
        [str(exe), "--port", str(port), "--ui-selftest", "10"],
        cwd=str(dist), stdout=console_fp, stderr=subprocess.STDOUT)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"
    results: list[tuple[str, bool, str]] = []
    try:
        info = _wait_api(opener, base, proc, seconds=45)
        if not info:
            print("      ✗ 面板服务没起来（窗口可能没成功创建）")
            if log.is_file():
                tail = log.read_bytes()[pre_size:].decode("utf-8", "replace")
                print("      日志尾部：")
                for line in tail.splitlines()[-15:]:
                    print("        " + line)
            return 1
        results += _common_api_checks(opener, base, info)

        # 等窗口自己关掉
        deadline = time.time() + 40
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.5)
        exited = proc.poll() is not None
        exit_code = proc.returncode
        if not exited:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            exit_code = proc.returncode

        text = ""
        if log.is_file():
            try:
                text = log.read_bytes()[pre_size:].decode("utf-8", "replace")
            except OSError:
                text = ""
        try:
            console_fp.close()
        except OSError:
            pass
        try:
            captured = console_log.read_bytes().decode("utf-8", "replace")
        except OSError:
            captured = ""
        text += "\n" + captured
        results.append(("窗口自检日志", bool(text.strip()),
                        f"程序日志 {len(text) - len(captured)}B + 标准输出 {len(captured)}B"))
        results.append(("原生窗口模式", "UI_MODE=webview" in text,
                        "WebView2 原生窗口" if "UI_MODE=webview" in text
                        else "未走原生窗口！"))
        # 在窗口里执行 JS 读 DOM/布局/样式，是"页面真的渲染出来了"最硬的证据。
        # 注意：依赖抓屏截图是不可靠的 —— WebView2 走 DirectComposition，
        # 在部分环境（虚拟机/远程桌面）截图只能拿到窗口背景色，但页面其实是好的。
        js_ok = "UI_JS_OK" in text
        js_line = next((l.strip() for l in text.splitlines() if "UI_JS_OK" in l), "")
        results.append(("页面渲染 + JS 执行", js_ok,
                        js_line or next((l.strip() for l in text.splitlines()
                                         if "UI_JS_FAIL" in l), "窗口里没能执行 JS")))
        import re as _re
        vw = _re.search(r"viewport=(\d+)x(\d+)", text)
        real_viewport = bool(vw and int(vw.group(1)) > 900 and int(vw.group(2)) > 550)
        results.append(("窗口视口尺寸", real_viewport,
                        f"{vw.group(1)}x{vw.group(2)}" if vw else "拿不到视口尺寸"))
        bg = _re.search(r"body_bg=rgb\(([\d, ]+)\)", text)
        styled = bool(bg and bg.group(1).replace(" ", "") == "13,17,23")
        results.append(("CSS 样式已生效", styled,
                        f"body 背景 {bg.group(1)}" if bg else "拿不到样式"))
        side = _re.search(r"sidebar=(\d+)", text)
        laid_out = bool(side and int(side.group(1)) > 200)
        results.append(("布局已计算", laid_out,
                        f"侧边栏 {side.group(1)}px" if side else "拿不到布局尺寸"))
        results.append(("窗口能正常关闭", "UI_SELFTEST_OK" in text or "UI_WINDOW_CLOSED" in text,
                        "窗口已关闭"))
        results.append(("进程正常退出", exited and exit_code == 0,
                        f"exit={exit_code}" if exited else "窗口关闭后进程没退出"))
        results.append(("未发生降级", "UI_WEBVIEW_FAILED" not in text,
                        "没回退到浏览器" if "UI_WEBVIEW_FAILED" not in text
                        else "回退到浏览器了，pywebview 打包有问题"))
        big = exe.stat().st_size / 1048576
        results.append(("体积", big < 80, f"{big:.1f} MB"))
        ok = _report(results)
        print("      ✓ 桌面窗口版功能完整（原生窗口已实测打开）" if ok
              else "      ✗ 校验未通过")
        return 0 if ok else 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        # 校验过程产生的运行期残留清掉（删不掉也不影响结果）
        _best_effort_cleanup(dist)


# ================================================================ 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="打包 mc-panel 为单文件 exe")
    ap.add_argument("--mode", choices=["both", "desktop", "console"], default="both",
                    help="打包哪个版本，默认两个都打")
    ap.add_argument("--check", action="store_true", help="只检查打包环境")
    ap.add_argument("--verify-only", action="store_true",
                    help="不重新打包，只校验 dist 里已有的 exe")
    ap.add_argument("--full", action="store_true",
                    help="完整重建（清 PyInstaller 分析缓存，慢但最干净）")
    ap.add_argument("--no-clean", action="store_true",
                    help="跳过 dist 里的运行期残留清理")
    args = ap.parse_args()

    dist = ROOT / "dist"

    if args.verify_only:
        existing = [k for k in VARIANTS
                    if (dist / f"{VARIANTS[k]['name']}.exe").exists()]
        if not existing:
            print(f"dist 里没有 exe 可校验：{dist}")
            return 1
        for key in existing:
            exe = dist / f"{VARIANTS[key]['name']}.exe"
            print(f"\n--- {VARIANTS[key]['title']}：{exe.name} ---")
            code = (verify_desktop if key == "desktop" else verify_console)(exe, dist)
            if code != 0:
                return code
        return 0

    env_ok = check_env()
    if args.check:
        print("打包环境就绪 ✓" if env_ok else "打包环境有问题，见上方 ✗")
        return 0 if env_ok else 1
    if not env_ok:
        print("\n环境检查未通过，已中止打包。")
        return 1

    try:
        import webview  # noqa: F401
        has_webview = True
    except ImportError:
        has_webview = False

    keys = ["desktop", "console"] if args.mode == "both" else [args.mode]
    if "desktop" in keys and not has_webview:
        print("\n[提示] 没装 pywebview，跳过桌面窗口版。")
        print(f"      想要它请先执行：{Path(sys.executable).parent / 'pip'} install pywebview")
        keys = [k for k in keys if k != "desktop"]

    if not keys:
        print("没有可打包的版本。")
        return 1

    work = ROOT / "build"
    dist.mkdir(parents=True, exist_ok=True)
    if not args.no_clean:
        # 只清运行期残留；同名 exe 交给 PyInstaller 自己覆盖
        _best_effort_cleanup(dist)

    print("[1/3] 准备图标")
    icon = ensure_icon()

    built: list[str] = []
    for key in keys:
        print(f"[2/3] 打包 {VARIANTS[key]['title']} …")
        code = build_variant(key, dist, work, icon, args.full)
        if code != 0:
            return code
        built.append(key)

    print("[3/3] 校验产物")
    for key in built:
        exe = dist / f"{VARIANTS[key]['name']}.exe"
        print(f"\n--- {VARIANTS[key]['title']}：{exe.name} ---")
        code = (verify_desktop if key == "desktop" else verify_console)(exe, dist)
        if code != 0:
            return code

    print("\n" + "=" * 62)
    for key in built:
        exe = dist / f"{VARIANTS[key]['name']}.exe"
        print(f"  {VARIANTS[key]['title']:<12} {exe.name:<24} "
              f"{exe.stat().st_size / 1048576:>5.1f} MB  — {VARIANTS[key]['desc']}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
