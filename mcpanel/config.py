"""面板全局配置（panel.json）。"""
from __future__ import annotations

from pathlib import Path

from . import __version__
from .util import app_root, read_json, write_json

DEFAULT_CONFIG: dict = {
    "host": "127.0.0.1",
    "port": 8080,
    "open_browser": True,
    # 面板登录密码；留空表示不校验（仅建议监听 127.0.0.1 时使用）
    "password": "",
    "servers_dir": "servers",
    "java_path": "",
    "console_buffer": 3000,
    "auto_scan_java": True,
}

# 面板数据目录：源码运行 = 项目目录；打包成 exe = exe 所在目录
PANEL_ROOT = app_root()


class PanelConfig:
    """全局配置，读写 panel.json。"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else PANEL_ROOT
        self.path = self.root / "panel.json"
        self.data = dict(DEFAULT_CONFIG)
        self.data.update(read_json(self.path, {}) or {})

    # -------------------------------------------------------------- 属性
    def __getitem__(self, key):
        return self.data.get(key, DEFAULT_CONFIG.get(key))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, **kwargs) -> dict:
        for k, v in kwargs.items():
            if k in DEFAULT_CONFIG:
                self.data[k] = v
        self.save()
        return self.data

    def save(self) -> None:
        write_json(self.path, self.data)

    # -------------------------------------------------------------- 路径
    @property
    def servers_dir(self) -> Path:
        d = Path(self.data.get("servers_dir") or "servers")
        if not d.is_absolute():
            d = self.root / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def auth_enabled(self) -> bool:
        return bool(self.data.get("password"))

    @property
    def guest_mode(self) -> bool:
        return not self.auth_enabled

    def public(self) -> dict:
        """给前端的安全视图（不含密码明文）。"""
        return {
            "version": __version__,
            "host": self.data.get("host"),
            "port": self.data.get("port"),
            "servers_dir": str(self.servers_dir),
            "auth_enabled": self.auth_enabled,
            "java_path": self.data.get("java_path") or "",
        }
