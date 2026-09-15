"""server.properties 的解析、写回，以及字段元数据（用于生成中文表单）。

两个关键兼容点（踩过坑）：
1. Minecraft 用 Java 的 java.util.Properties 读取该文件 —— 编码是 **ISO-8859-1**，
   非 Latin-1 字符（中文、emoji）必须写成 \\uXXXX 转义，否则服务端会读成乱码。
2. 1.12.2 及更早版本里 difficulty / gamemode 存的是数字（0/1/2/3），
   1.13+ 才是字符串。表单会根据文件里现有值的形态自动切换选项。
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------- 转义


_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def unescape(text: str) -> str:
    """把 \\u00A7a\\u6B22 这类转义还原成可读文本。"""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "u" and i + 5 < n:
                hexpart = text[i + 2:i + 6]
                if all(c in "0123456789abcdefABCDEF" for c in hexpart):
                    out.append(chr(int(hexpart, 16)))
                    i += 6
                    continue
            if nxt in "\\#!=: \t":
                out.append(nxt)
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def escape(text: str) -> str:
    """把非 ASCII 字符转成 \\uXXXX，供 Java 的 Properties 读取。

    严格对齐 java.util.Properties.store 的行为：只保留 0x20~0x7E 的可打印
    ASCII，其余（中文、§、重音字母）一律转义。这样写出的文件是纯 ASCII，
    不会被工具误判成二进制，也不会因编码猜错而乱码。
    """
    out: list[str] = []
    for ch in str(text):
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif 0x20 <= code <= 0x7E:
            out.append(ch)
        else:
            out.append("\\u%04X" % code)
    return "".join(out)

# ---------------------------------------------------------------- 字段说明
# type: bool / int / str / enum / password
PROPERTY_META: dict[str, dict] = {
    # 基础
    "motd": {"label": "服务器标语 (MOTD)", "type": "str",
             "desc": "服务器列表里显示的一行字，支持 § 颜色代码"},
    "server-port": {"label": "服务器端口", "type": "int", "desc": "默认 25565"},
    "server-ip": {"label": "绑定 IP", "type": "str",
                  "desc": "留空 = 监听所有网卡"},
    "max-players": {"label": "最大玩家数", "type": "int"},
    "online-mode": {"label": "正版验证", "type": "bool",
                    "desc": "开启后只有正版账号能进，离线服请关闭"},
    "white-list": {"label": "启用白名单", "type": "bool"},
    "enforce-whitelist": {"label": "踢出非白名单玩家", "type": "bool"},
    "difficulty": {"label": "难度", "type": "enum",
                   "options": ["peaceful", "easy", "normal", "hard"]},
    "gamemode": {"label": "默认游戏模式", "type": "enum",
                 "options": ["survival", "creative", "adventure", "spectator"]},
    "force-gamemode": {"label": "强制游戏模式", "type": "bool"},
    "hardcore": {"label": "极限模式", "type": "bool"},
    "pvp": {"label": "允许 PVP", "type": "bool"},
    "player-idle-timeout": {"label": "挂机踢出(分钟)", "type": "int",
                            "desc": "0 = 不踢"},
    "allow-flight": {"label": "允许飞行", "type": "bool"},
    "enable-command-block": {"label": "允许命令方块", "type": "bool"},

    # 世界
    "level-name": {"label": "世界存档名", "type": "str", "desc": "改这个名就会换存档"},
    "level-seed": {"label": "世界种子", "type": "str"},
    "level-type": {"label": "世界类型", "type": "str",
                   "desc": "1.12.2: DEFAULT / FLAT / LARGEBIOMES / AMPLIFIED；新版用 minecraft:normal 等"},
    "generator-settings": {"label": "自定义地形设置", "type": "str"},
    "allow-nether": {"label": "允许下界", "type": "bool"},
    "spawn-animals": {"label": "生成动物", "type": "bool"},
    "spawn-monsters": {"label": "生成怪物", "type": "bool"},
    "spawn-npcs": {"label": "生成村民", "type": "bool"},
    "generate-structures": {"label": "生成建筑结构", "type": "bool"},
    "max-world-size": {"label": "世界边界半径", "type": "int"},
    "spawn-protection": {"label": "出生点保护半径", "type": "int"},
    "max-build-height": {"label": "最大建筑高度", "type": "int"},

    # 性能 / 网络
    "view-distance": {"label": "视距(区块)", "type": "int", "desc": "经典值 6~10"},
    "simulation-distance": {"label": "模拟距离(区块)", "type": "int", "desc": "1.18+"},
    "max-tick-time": {"label": "单刻最大耗时(ms)", "type": "int",
                      "desc": "-1 关闭看门狗，卡顿误杀可调大"},
    "network-compression-threshold": {"label": "网络压缩阈值", "type": "int",
                                      "desc": "一般 256，内网可设 -1 关压缩"},
    "sync-chunk-writes": {"label": "同步写入区块", "type": "bool"},
    "entity-broadcast-range-percentage": {"label": "实体广播范围(%)", "type": "int"},
    "enable-status": {"label": "响应服务器列表查询", "type": "bool"},
    "max-entity-crash-per-tick": {"label": "单刻最大崩溃实体数", "type": "int",
                                  "desc": "1.20.3+"},
    "rate-limit": {"label": "数据包速率限制", "type": "int"},

    # 远程管理 / 安全
    "enable-rcon": {"label": "启用 RCON", "type": "bool"},
    "rcon.port": {"label": "RCON 端口", "type": "int"},
    "rcon.password": {"label": "RCON 密码", "type": "password"},
    "enable-query": {"label": "启用 Query 查询", "type": "bool"},
    "query.port": {"label": "Query 端口", "type": "int"},
    "op-permission-level": {"label": "OP 权限等级", "type": "int"},
    "function-permission-level": {"label": "函数权限等级", "type": "int"},
    "hide-online-players": {"label": "隐藏在线玩家列表", "type": "bool"},
    "prevent-proxy-connections": {"label": "拒绝代理连接", "type": "bool",
                                  "desc": "会拦掉部分加速器，谨慎开启"},
    "log-ips": {"label": "日志记录 IP", "type": "bool"},
    "broadcast-console-to-ops": {"label": "控制台消息广播给 OP", "type": "bool"},
    "broadcast-rcon-to-ops": {"label": "RCON 消息广播给 OP", "type": "bool"},

    # 资源包
    "resource-pack": {"label": "资源包直链", "type": "str"},
    "resource-pack-sha1": {"label": "资源包 SHA1", "type": "str"},
    "require-resource-pack": {"label": "强制使用资源包", "type": "bool"},
    "resource-pack-prompt": {"label": "资源包提示语", "type": "str"},
}

# 1.12.2 及更早版本里，difficulty / gamemode 写的是数字
LEGACY_ENUMS: dict[str, dict[str, str]] = {
    "difficulty": {"0": "peaceful", "1": "easy", "2": "normal", "3": "hard"},
    "gamemode": {"0": "survival", "1": "creative", "2": "adventure", "3": "spectator"},
}

# 枚举值的中文显示
ENUM_LABELS: dict[str, str] = {
    "peaceful": "和平", "easy": "简单", "normal": "普通", "hard": "困难",
    "survival": "生存", "creative": "创造", "adventure": "冒险", "spectator": "旁观",
}

GROUP_LAYOUT: list[tuple[str, list[str]]] = [    ("基础设置", ["motd", "server-port", "server-ip", "max-players", "difficulty",
                  "gamemode", "force-gamemode", "hardcore", "pvp",
                  "online-mode", "white-list", "enforce-whitelist",
                  "player-idle-timeout", "allow-flight", "enable-command-block"]),
    ("世界生成", ["level-name", "level-seed", "level-type", "generator-settings",
                  "allow-nether", "spawn-animals", "spawn-monsters", "spawn-npcs",
                  "generate-structures", "max-world-size", "spawn-protection",
                  "max-build-height"]),
    ("性能与网络", ["view-distance", "simulation-distance", "max-tick-time",
                    "network-compression-threshold", "sync-chunk-writes",
                    "entity-broadcast-range-percentage", "enable-status",
                    "max-entity-crash-per-tick", "rate-limit"]),
    ("远程管理", ["enable-rcon", "rcon.port", "rcon.password", "enable-query",
                  "query.port", "op-permission-level", "function-permission-level",
                  "hide-online-players", "prevent-proxy-connections", "log-ips",
                  "broadcast-console-to-ops", "broadcast-rcon-to-ops"]),
    ("资源包", ["resource-pack", "resource-pack-sha1", "require-resource-pack",
                "resource-pack-prompt"]),
]

# 实例创建时写入的初始配置
STARTER_PROPERTIES: dict[str, str] = {
    "motd": "A Minecraft Server powered by MC Panel",
    "server-port": "25565",
    "max-players": "20",
    "online-mode": "true",
    "difficulty": "normal",
    "gamemode": "survival",
    "level-name": "world",
    "view-distance": "8",
    "spawn-protection": "16",
    "enable-command-block": "false",
    "allow-flight": "false",
    "pvp": "true",
    "white-list": "false",
    "enable-rcon": "false",
}


# ---------------------------------------------------------------- 解析


def parse(text: str) -> tuple[dict[str, str], list[str]]:
    """返回 (键值对, 原始行顺序外的注释行列表)。"""
    values: dict[str, str] = {}
    comments: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in "#!":
            comments.append(line)
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value
    return values, comments


def dump(values: dict[str, str], header: str = "") -> str:
    lines: list[str] = []
    if header:
        lines.append(f"# {header}")
        lines.append("")
    for key, value in values.items():
        lines.append(f"{key}={escape(value)}")
    lines.append("")
    return "\n".join(lines)


def read_properties(path: Path) -> dict[str, str]:
    """读取并按 Java Properties 规则解码（ISO-8859-1 + \\uXXXX）。"""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return {}
    text = raw.decode("latin-1")
    values, _ = parse(text)
    return {key: unescape(value) for key, value in values.items()}


def write_properties(path: Path, values: dict[str, str]) -> None:
    """写回。必须用 Latin-1 编码 + 转义，否则服务端读到的中文是乱码。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dump(values, "Minecraft server properties (managed by MC Panel)")
    path.write_bytes(body.encode("latin-1", "replace"))


def merge_properties(path: Path, updates: dict[str, str]) -> dict[str, str]:
    """只覆盖指定字段，保留顺序和原有未知字段。"""
    values = read_properties(path)
    for key, value in updates.items():
        values[str(key)] = _stringify(value)
    write_properties(path, values)
    return values


def _stringify(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_form(values: dict[str, str], mc_version: str = "") -> dict:
    """把键值对组装成前端可直接渲染的分组表单结构。"""
    groups: list[dict] = []

    def make_field(key: str) -> dict:
        meta = PROPERTY_META.get(key, {})
        raw = values.get(key, "")
        ftype = meta.get("type", "str")
        field = {
            "key": key,
            "label": meta.get("label") or key,
            "type": ftype,
            "desc": meta.get("desc", ""),
            "value": raw,
            "present": key in values,
        }
        if ftype == "enum":
            legacy = LEGACY_ENUMS.get(key, {})
            # 文件里已经是数字（1.12.2 及更早）或版本较老 -> 用数字选项，保持与原格式一致
            use_legacy = bool(legacy) and (raw in legacy
                                           or (not raw and is_legacy_version(mc_version)))
            choices: list[dict] = []
            if use_legacy:
                for num, name in legacy.items():
                    choices.append({"value": num,
                                    "label": f"{ENUM_LABELS.get(name, name)}（{name}）"})
            else:
                for opt in meta.get("options", []):
                    choices.append({"value": opt, "label": ENUM_LABELS.get(opt, opt)})
                if raw and raw not in {c["value"] for c in choices}:
                    choices.append({"value": raw, "label": f"{raw}（未知值）"})
            field["choices"] = choices
            field["legacy"] = use_legacy
        return field

    listed: set[str] = set()
    for title, keys in GROUP_LAYOUT:
        fields = [make_field(k) for k in keys]
        listed.update(keys)
        groups.append({"title": title, "fields": fields})

    extra = [k for k in values if k not in listed]
    if extra:
        groups.append({"title": "其他", "fields": [make_field(k) for k in extra]})

    return {"groups": groups, "values": values, "mc_version": mc_version}


def is_legacy_version(mc_version: str) -> bool:
    """1.12.2 及更早 = 老版本（数字枚举、Java 8）。"""
    try:
        parts = [int(x) for x in str(mc_version).split(".")[:3]]
    except (ValueError, TypeError):
        return False
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts
    if major != 1:
        return False
    if minor < 13:
        return True
    return minor == 12 and patch <= 2


def unknown_or_missing(values: dict[str, str]) -> list[str]:
    return [k for k in PROPERTY_META if k not in values]
