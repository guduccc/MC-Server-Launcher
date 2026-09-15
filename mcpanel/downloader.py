"""服务端核心的下载与安装。

支持的核心：
    vanilla  Mojang 官方原版
    paper    PaperMC（服务端优化，1.8 ~ 最新）
    purpur   Purpur（Paper 加强版）
    fabric   Fabric 服务端（模组服）
    forge    Forge 服务端（模组服，支持 1.12.2 等经典版本）
    spigot   Spigot（需本机有 Git，通过 BuildTools 现场编译）
    custom   自行上传的 jar

全部走 urllib，不依赖 requests / aiohttp。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

USER_AGENT = "MCPanel/1.0 (+python-stdlib)"

Progress = Callable[[str, float, str], None]     # phase, percent, message
Logger = Callable[[str], None]


# ---------------------------------------------------------------- HTTP


def http_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def http_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def download(url: str, dest: Path, progress: Progress | None = None,
             phase: str = "download", retries: int = 3) -> Path:
    """带进度和重试的下载。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                with open(tmp, "wb") as fp:
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        fp.write(chunk)
                        got += len(chunk)
                        if progress:
                            pct = (got / total * 100.0) if total else 0.0
                            progress(phase, pct,
                                     f"{got / 1048576:.1f} / {total / 1048576:.1f} MB"
                                     if total else f"{got / 1048576:.1f} MB")
            tmp.replace(dest)
            if progress:
                progress(phase, 100.0, dest.name)
            return dest
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            if progress:
                progress(phase, 0.0, f"第 {attempt} 次下载失败，重试中…")
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    raise RuntimeError(f"下载失败：{url}\n{last_error}")


# ---------------------------------------------------------------- 版本列表


def _mojang_manifest() -> dict:
    urls = [
        "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
        "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
    ]
    last = None
    for url in urls:
        try:
            return http_json(url)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"无法获取 Mojang 版本清单：{last}")


def list_versions(core: str) -> list[dict]:
    """返回 [{version, type, note}]，最新在前。"""
    core = (core or "").lower()
    if core == "vanilla":
        data = _mojang_manifest()
        out = []
        for item in data.get("versions", []):
            out.append({"version": item["id"], "type": item.get("type", "release")})
        return out

    if core in ("paper", "purpur"):
        if core == "paper":
            versions = _paper_versions()
            return [{"version": v,
                     "type": "snapshot" if "-" in v else "release"} for v in versions]
        data = http_json("https://api.purpurmc.org/v2/purpur")
        versions = data.get("versions", [])
        if isinstance(versions, dict):
            flat: list[str] = []
            for group in versions.values():
                flat.extend(group or [])
            versions = flat
        else:
            versions = list(reversed(versions))
        return [{"version": v,
                 "type": "snapshot" if "-" in v else "release"} for v in versions]

    if core == "fabric":
        games = http_json("https://meta.fabricmc.net/v2/versions/game")
        return [{"version": g["version"],
                 "type": "release" if g.get("stable") else "snapshot"}
                for g in games]

    if core == "forge":
        meta = http_json("https://files.minecraftforge.net/net/minecraftforge/"
                         "forge/maven-metadata.json")
        try:
            promos = http_json("https://files.minecraftforge.net/net/minecraftforge/"
                               "forge/promotions_slim.json").get("promos", {})
        except Exception:  # noqa: BLE001
            promos = {}
        versions = list(meta.keys())
        versions.sort(key=_version_key, reverse=True)

        def note(mc: str) -> str:
            rec = promos.get(f"{mc}-recommended")
            lat = promos.get(f"{mc}-latest")
            parts = []
            if rec:
                parts.append(f"推荐 {rec}")
            elif lat:
                parts.append(f"最新 {lat}")
            return " · ".join(parts)

        return [{"version": v, "type": "release", "note": note(v)} for v in versions]

    if core == "spigot":
        # BuildTools 支持范围写死一份常见列表
        return [{"version": v, "type": "release", "note": "需编译，较慢"}
                for v in _spigot_versions()]

    return []


def _version_key(v: str):
    parts = []
    for chunk in str(v).split("."):
        parts.append(int(chunk) if chunk.isdigit() else -1)
    return parts


def _spigot_versions() -> list[str]:
    return ["1.21.4", "1.21.1", "1.20.6", "1.20.4", "1.20.2", "1.20.1", "1.19.4",
            "1.19.2", "1.18.2", "1.17.1", "1.16.5", "1.16.4", "1.15.2", "1.14.4",
            "1.13.2", "1.12.2", "1.11.2", "1.10.2", "1.9.4", "1.8.8"]


# ---------------------------------------------------------------- 安装逻辑


def install(target_dir: Path, core: str, mc_version: str,
            build: str | None = None,
            java_path: str | None = None,
            progress: Progress | None = None,
            log: Logger | None = None) -> dict:
    """把服务端核心安装到 target_dir。

    返回 launch 描述：{"type": "jar"|"argsfile", "target": "..."}
    """
    core = (core or "vanilla").lower()
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    def say(msg: str):
        if log:
            log(msg)

    def tick(phase: str, pct: float, msg: str):
        if progress:
            progress(phase, pct, msg)

    if core == "vanilla":
        return _install_vanilla(target_dir, mc_version, tick, say)
    if core == "paper":
        return _install_paper(target_dir, mc_version, build, tick, say)
    if core == "purpur":
        return _install_purpur(target_dir, mc_version, build, tick, say)
    if core == "fabric":
        return _install_fabric(target_dir, mc_version, tick, say)
    if core == "forge":
        return _install_forge(target_dir, mc_version, build, java_path, tick, say)
    if core == "spigot":
        return _install_spigot(target_dir, mc_version, java_path, tick, say)
    raise RuntimeError(f"暂不支持的核心：{core}")


def _install_vanilla(target_dir: Path, mc_version: str, tick, say) -> dict:
    say(f"查询 Mojang 版本清单，目标 {mc_version}")
    tick("准备", 1, "获取版本信息")
    manifest = _mojang_manifest()
    entry = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
    if not entry:
        raise RuntimeError(f"原版找不到版本 {mc_version}")
    detail = http_json(entry["url"])
    server = detail.get("downloads", {}).get("server")
    if not server:
        raise RuntimeError(f"{mc_version} 没有官方服务端下载（可能是快照）")
    dest = target_dir / "server.jar"
    say(f"开始下载 server.jar（{server['size'] / 1048576:.1f} MB）")
    download(server["url"], dest, tick, "下载服务端")
    say("原版服务端下载完成")
    return {"type": "jar", "target": "server.jar"}


def _paper_versions() -> list[str]:
    """Paper 版本列表，最新在前。

    PaperMC 已于 2025 年下线 api.papermc.io/v2（返回 410），
    现用 fill.papermc.io/v3；v3 的 versions 是按小版本分组的字典。
    """
    try:
        data = http_json("https://fill.papermc.io/v3/projects/paper")
        raw = data.get("versions", {})
        if isinstance(raw, dict):
            flat: list[str] = []
            for group in raw.values():
                flat.extend(group or [])
            return flat
        return list(raw)
    except Exception:  # noqa: BLE001  兜底走老接口
        data = http_json("https://api.papermc.io/v2/projects/paper")
        return list(reversed(data.get("versions", [])))


def _paper_builds(mc_version: str) -> list[dict]:
    """某版本的构建列表，最新在前。"""
    try:
        data = http_json(f"https://fill.papermc.io/v3/projects/paper/versions/"
                         f"{urllib.parse.quote(mc_version)}/builds")
        if isinstance(data, list):
            return data
    except Exception:  # noqa: BLE001
        pass
    builds = http_json(f"https://api.papermc.io/v2/projects/paper/versions/"
                       f"{urllib.parse.quote(mc_version)}").get("builds", [])
    out = []
    for b in reversed(builds):
        out.append({"id": b, "channel": "",
                    "downloads": {"application": {
                        "name": f"paper-{mc_version}-{b}.jar",
                        "url": (f"https://api.papermc.io/v2/projects/paper/versions/"
                                f"{urllib.parse.quote(mc_version)}/builds/{b}/downloads/"
                                f"paper-{mc_version}-{b}.jar")}}})
    return out


def _paper_build(mc_version: str, want: str | None) -> tuple[str, str, str]:
    """返回 (build, 下载地址, 文件名)。"""
    builds = _paper_builds(mc_version)
    if not builds:
        raise RuntimeError(f"Paper 没有 {mc_version} 的可用构建")
    entry = None
    if want:
        entry = next((b for b in builds if str(b.get("id")) == str(want)), None)
    if entry is None:
        entry = next((b for b in builds if b.get("channel") in ("STABLE", "", None)),
                     builds[0])
    downloads = entry.get("downloads", {})
    payload = (downloads.get("server:default")
               or downloads.get("application") or {})
    url = payload.get("url") if isinstance(payload, dict) else None
    name = payload.get("name") if isinstance(payload, dict) else None
    if not url:
        raise RuntimeError(f"Paper {mc_version} 构建 {entry.get('id')} 没有下载地址")
    name = name or f"paper-{mc_version}-{entry['id']}.jar"
    return str(entry.get("id")), url, name


def _install_paper(target_dir: Path, mc_version: str, build, tick, say) -> dict:
    say(f"查询 Paper {mc_version} 构建列表")
    tick("准备", 1, "获取构建信息")
    build_id, url, name = _paper_build(mc_version, build)
    say(f"最新构建 #{build_id}，下载 {name}")
    download(url, target_dir / name, tick, "下载服务端")
    say("Paper 服务端下载完成")
    return {"type": "jar", "target": name}


def _install_purpur(target_dir: Path, mc_version: str, build, tick, say) -> dict:
    tick("准备", 1, "获取构建信息")
    latest = build or "latest"
    if latest == "latest":
        info = http_json(f"https://api.purpurmc.org/v2/purpur/{mc_version}")
        latest = info.get("builds", {}).get("latest", "latest")
    url = f"https://api.purpurmc.org/v2/purpur/{mc_version}/{latest}/download"
    name = f"purpur-{mc_version}-{latest}.jar"
    say(f"下载 {name}")
    download(url, target_dir / name, tick, "下载服务端")
    say("Purpur 服务端下载完成")
    return {"type": "jar", "target": name}


def _install_fabric(target_dir: Path, mc_version: str, tick, say) -> dict:
    tick("准备", 1, "查询 Fabric Loader")
    loaders = http_json(f"https://meta.fabricmc.net/v2/versions/loader/"
                        f"{urllib.parse.quote(mc_version)}")
    if not loaders:
        raise RuntimeError(f"Fabric 不支持 {mc_version}")
    stable = next((l for l in loaders if l["loader"].get("stable")), loaders[0])
    loader = stable["loader"]["version"]
    installers = http_json("https://meta.fabricmc.net/v2/versions/installer")
    installer = next((i["version"] for i in installers if i.get("stable")),
                     installers[0]["version"])
    say(f"Fabric Loader {loader} / Installer {installer}")
    name = f"fabric-server-mc{mc_version}-loader{loader}-launcher{installer}.jar"
    url = (f"https://meta.fabricmc.net/v2/versions/loader/"
           f"{urllib.parse.quote(mc_version)}/{loader}/{installer}/server/jar")
    download(url, target_dir / name, tick, "下载服务端")
    say("Fabric 启动器下载完成（首次启动会自动补齐依赖库）")
    return {"type": "jar", "target": name}


def _install_forge(target_dir: Path, mc_version: str, build, java_path,
                   tick, say) -> dict:
    tick("准备", 1, "查询 Forge 版本")
    try:
        meta = http_json("https://files.minecraftforge.net/net/minecraftforge/"
                         "forge/maven-metadata.json")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法获取 Forge 版本表：{exc}") from exc
    if mc_version not in meta:
        raise RuntimeError(f"Forge 没有 {mc_version} 的版本")
    versions = meta[mc_version]
    if build and build in versions:
        forge_ver = build
    else:
        try:
            promos = http_json("https://files.minecraftforge.net/net/minecraftforge/"
                               "forge/promotions_slim.json")["promos"]
            forge_ver = (promos.get(f"{mc_version}-recommended")
                         or promos.get(f"{mc_version}-latest")
                         or versions[-1])
        except Exception:  # noqa: BLE001
            forge_ver = versions[-1]

    full = f"{mc_version}-{forge_ver}"
    say(f"Forge {full}，开始下载安装器")
    jar_name = f"forge-{full}-installer.jar"
    url = (f"https://maven.minecraftforge.net/net/minecraftforge/forge/"
           f"{full}/forge-{full}-installer.jar")
    download(url, target_dir / jar_name, tick, "下载安装器")

    java_exe = java_path or "java"
    say(f"运行安装器：{Path(java_exe).name} -jar {jar_name} --installServer")
    say("这一步可能要几分钟，请耐心等待…")
    tick("安装", 5, "正在解压依赖库")
    proc = subprocess.Popen(
        [java_exe, "-jar", jar_name, "--installServer"],
        cwd=str(target_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.stdout is not None
    from .util import decode_bytes
    for raw in iter(proc.stdout.readline, b""):
        line = decode_bytes(raw).rstrip()
        if line:
            say(f"[installer] {line}")
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"Forge 安装器退出码 {code}，请查看上方日志")

    # 1.12.2 及以前会生成 forge-<full>.jar；1.17+ 生成 args.txt
    universal = target_dir / f"forge-{full}.jar"
    if universal.exists():
        say("Forge 安装完成")
        return {"type": "jar", "target": universal.name}

    args_candidates = [
        target_dir / "libraries" / "net" / "minecraftforge" / "forge" / full / "win_args.txt",
        target_dir / "libraries" / "net" / "minecraftforge" / "forge" / full / "unix_args.txt",
    ]
    for cand in args_candidates:
        if cand.exists():
            rel = cand.relative_to(target_dir).as_posix()
            say(f"Forge 安装完成，启动参数文件：{rel}")
            return {"type": "argsfile", "target": rel}

    raise RuntimeError("Forge 安装完成但没找到启动入口，请检查目录")


def _install_spigot(target_dir: Path, mc_version: str, java_path, tick, say) -> dict:
    if not shutil.which("git"):
        say("警告：未检测到 Git，BuildTools 很可能失败。请先安装 Git。")
    tick("准备", 1, "下载 BuildTools")
    download("https://hub.spigotmc.org/jenkins/job/BuildTools/lastSuccessfulBuild/"
             "artifact/target/BuildTools.jar",
             target_dir / "BuildTools.jar", tick, "下载 BuildTools")

    java_exe = java_path or "java"
    say(f"开始编译 Spigot {mc_version}（视机器性能约需 5~20 分钟）")
    tick("编译", 5, "Git 拉取源码并编译")
    proc = subprocess.Popen(
        [java_exe, "-jar", "BuildTools.jar", "--rev", mc_version],
        cwd=str(target_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.stdout is not None
    from .util import decode_bytes
    for raw in iter(proc.stdout.readline, b""):
        line = decode_bytes(raw).rstrip()
        if line:
            say(f"[buildtools] {line}")
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"BuildTools 退出码 {code}，编译失败")
    jars = sorted(target_dir.glob("spigot-*.jar"))
    if not jars:
        raise RuntimeError("编译结束但没找到 spigot jar")
    say(f"编译完成：{jars[-1].name}")
    return {"type": "jar", "target": jars[-1].name}


# ---------------------------------------------------------------- 其他


def list_builds(core: str, mc_version: str) -> list[dict]:
    """列出某版本的可用构建号（仅 Paper / Purpur / Forge 有意义）。"""
    core = (core or "").lower()
    try:
        if core == "paper":
            builds = _paper_builds(mc_version)
            return [{"build": b.get("id"), "note": b.get("channel") or "",
                     "time": b.get("time", "")} for b in builds]
        if core == "purpur":
            info = http_json(f"https://api.purpurmc.org/v2/purpur/{mc_version}")
            builds = list(reversed(info.get("builds", {}).get("all", [])))
            return [{"build": b, "note": ""} for b in builds]
        if core == "forge":
            meta = http_json("https://files.minecraftforge.net/net/minecraftforge/"
                             "forge/maven-metadata.json")
            versions = list(reversed(meta.get(mc_version, [])))
            return [{"build": v, "note": ""} for v in versions]
    except Exception:  # noqa: BLE001
        return []
    return []


CORE_INFO = [
    {"id": "vanilla", "name": "Vanilla 原版", "desc": "Mojang 官方服务端，纯净无插件",
     "mods": False, "plugins": False},
    {"id": "paper", "name": "Paper", "desc": "最流行的优化端，支持 Bukkit/Spigot 插件",
     "mods": False, "plugins": True},
    {"id": "purpur", "name": "Purpur", "desc": "Paper 加强版，可玩性配置更多",
     "mods": False, "plugins": True},
    {"id": "fabric", "name": "Fabric", "desc": "轻量模组端，适合装性能/辅助模组",
     "mods": True, "plugins": False},
    {"id": "forge", "name": "Forge", "desc": "经典模组端，1.12.2 生态最全",
     "mods": True, "plugins": False},
    {"id": "spigot", "name": "Spigot", "desc": "插件端，通过 BuildTools 现场编译",
     "mods": False, "plugins": True},
    {"id": "custom", "name": "自定义核心", "desc": "自己上传 jar / 已有服务端整合包",
     "mods": True, "plugins": True},
]
