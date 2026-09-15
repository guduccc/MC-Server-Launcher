"""基于 PyQt-SiliconUI 的原生 Qt 界面（可选组件）。

这个包**不是必需依赖**：只有 import 成功时才用得到。
装了 PyQt5 + siui 就用 Qt 界面，没装就退回网页界面，主程序不受影响。

依赖（要单独装到跑面板的那个 Python 里）：
    pip install PyQt5 PyQt-SiliconUI numpy
"""
from __future__ import annotations

import importlib.util


def qt_available() -> bool:
    """PyQt5 + siui 是否都就绪。"""
    for module in ("PyQt5", "siui"):
        if importlib.util.find_spec(module) is None:
            return False
    return True


def qt_missing_reason() -> str:
    """缺什么，给用户一句能照做的话。"""
    missing = [name for name in ("PyQt5", "siui")
               if importlib.util.find_spec(name) is None]
    if not missing:
        return ""
    return (f"缺少 {' / '.join(missing)}。装好就能用 Qt 界面：\n"
            "    pip install PyQt5 PyQt-SiliconUI numpy\n"
            "（PyQt-SiliconUI 没发布到 PyPI，需要先 git clone 后 pip install 本地目录）")


def run_qt(server, config, manager, stop_all_on_exit: bool = False,
           selftest: float = 0.0) -> int:
    """启动 Qt 界面。真正的实现在 window.py 里，这里只做延迟导入。

    参数顺序和 desktop.run_desktop 保持一致：(server, config, manager)。
    """
    from .window import run_qt as _run
    return _run(server, config, manager, stop_all_on_exit, selftest)
