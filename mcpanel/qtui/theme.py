"""Qt 界面的主题常量与通用控件工厂。

配色全部对齐 siui 的深色主题（对应 SiColor 的 INTERFACE_BG_* / TEXT_* / THEME 等令牌），
这样普通 Qt 控件（输入框、日志、列表）和 siui 组件摆在一起观感才统一。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QSizePolicy, QVBoxLayout, QWidget)

# ---------------------------------------------------------------- 调色板
# 括号里是对应的 siui 颜色令牌名
BG_A = "#1C191F"        # INTERFACE_BG_A 窗口最底
BG_B = "#25222A"        # INTERFACE_BG_B 侧边栏
BG_C = "#332E38"        # INTERFACE_BG_C 卡片
BG_D = "#403A46"        # INTERFACE_BG_D 抬升面
BG_E = "#4C4554"        # INTERFACE_BG_E 边框 / 按钮底
TEXT_A = "#E5E5E5"      # TEXT_A 主要文字
TEXT_B = "#DFDFDF"      # TEXT_B
TEXT_C = "#C7C7C7"      # TEXT_C
TEXT_D = "#AFAFAF"      # TEXT_D 次要文字
TEXT_E = "#979797"      # TEXT_E 最弱
ACCENT = "#c58bc2"      # TEXT_THEME 主色（紫）
ACCENT_DEEP = "#855198"  # THEME
OK = "#519868"          # SIDE_MSG_THEME_SUCCESS
WARN = "#986351"        # SIDE_MSG_THEME_WARNING
ERR = "#98515B"         # SIDE_MSG_THEME_ERROR
INFO = "#855198"        # SIDE_MSG_THEME_INFO

# 日志行配色
LOG_COLORS = {
    "info": TEXT_C,
    "ok": OK,
    "warn": "#D8A657",
    "error": "#E06C75",
    "cmd": "#7FB4E8",
    "panel": ACCENT,
}

MONO_FAMILIES = ("Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New")


def mono_font(size: int = 9) -> QFont:
    """等宽字体 —— 日志区必须用等宽，否则时间戳对不齐。"""
    available = set(QFontDatabase().families())
    for name in MONO_FAMILIES:
        if name in available:
            font = QFont(name)
            break
    else:
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setPointSize(size)
    return font


# ---------------------------------------------------------------- 全局样式

def stylesheet() -> str:
    """普通 Qt 控件的样式，让它们和 siui 组件看起来是一套。"""
    return f"""
    QWidget {{ color: {TEXT_B}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
        background: {BG_A};
        border: 1px solid {BG_E};
        border-radius: 6px;
        padding: 5px 8px;
        color: {TEXT_A};
        selection-background-color: {ACCENT_DEEP};
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
        border: 1px solid {ACCENT};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
        color: {TEXT_E}; background: {BG_B};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {BG_C}; border: 1px solid {BG_E};
        selection-background-color: {ACCENT_DEEP}; outline: none;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 14px; background: {BG_D}; border: none; }}

    QPlainTextEdit {{
        background: #15131A; border: 1px solid {BG_E};
        border-radius: 8px; padding: 6px;
    }}

    QTableWidget, QListWidget {{
        background: {BG_C}; border: 1px solid {BG_E};
        border-radius: 8px; gridline-color: {BG_D}; outline: none;
    }}
    QTableWidget::item, QListWidget::item {{ padding: 4px 6px; border: none; }}
    QTableWidget::item:selected, QListWidget::item:selected {{
        background: {ACCENT_DEEP}; color: #FFFFFF;
    }}
    QHeaderView::section {{
        background: {BG_D}; color: {TEXT_C}; border: none;
        border-right: 1px solid {BG_E}; border-bottom: 1px solid {BG_E};
        padding: 5px 6px;
    }}
    QTableCornerButton::section {{ background: {BG_D}; border: none; }}

    QTabBar {{ background: transparent; }}
    QTabWidget::pane {{ border: none; }}

    QCheckBox {{ color: {TEXT_C}; spacing: 6px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px; border-radius: 4px;
        border: 1px solid {BG_E}; background: {BG_A};
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT_DEEP}; border-color: {ACCENT}; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {BG_E}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {ACCENT_DEEP}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {BG_E}; border-radius: 5px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QToolTip {{
        background: {BG_E}; color: {TEXT_A};
        border: 1px solid {ACCENT_DEEP}; padding: 4px;
    }}
    QMessageBox {{ background: {BG_C}; }}
    QMessageBox QLabel {{ color: {TEXT_B}; }}
    QDialog {{ background: {BG_C}; }}
    QMenu {{ background: {BG_C}; border: 1px solid {BG_E}; }}
    QMenu::item:selected {{ background: {ACCENT_DEEP}; }}
    QSplitter::handle {{ background: {BG_A}; }}
    """


def plain_button_style(kind: str = "normal") -> str:
    """给普通 QPushButton 用的按钮样式（标签页、快捷指令、列表操作等）。"""
    palette = {
        "normal": (BG_D, BG_E, TEXT_B, BG_E),
        "accent": (ACCENT_DEEP, ACCENT, "#FFFFFF", ACCENT),
        "danger": ("#6E2A31", ERR, "#F0DCDE", ERR),
        "ghost": ("transparent", BG_E, TEXT_C, BG_D),
    }
    bg, border, fg, hover = palette.get(kind, palette["normal"])
    return f"""
    QPushButton {{
        background: {bg}; border: 1px solid {border}; border-radius: 6px;
        padding: 5px 12px; color: {fg};
    }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:pressed {{ background: {ACCENT_DEEP}; }}
    QPushButton:disabled {{ color: {TEXT_E}; background: {BG_B}; border-color: {BG_D}; }}
    QPushButton:checked {{ background: {ACCENT_DEEP}; border-color: {ACCENT}; color: #FFFFFF; }}
    """


# ---------------------------------------------------------------- 控件工厂

def card(parent: QWidget | None = None, padding: int = 14, spacing: int = 10) -> QFrame:
    """一张卡片（siui 的卡片观感：BG_C 底 + 圆角 + 细边框）。"""
    frame = QFrame(parent)
    frame.setObjectName("siCard")
    frame.setStyleSheet(
        f"#siCard {{ background: {BG_C}; border: 1px solid {BG_E}; border-radius: 10px; }}")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(spacing)
    frame.body = layout                     # type: ignore[attr-defined]
    return frame


def card_title(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    font = label.font()
    font.setPointSize(font.pointSize() + 1)
    font.setBold(True)
    label.setFont(font)
    label.setStyleSheet(f"color: {TEXT_A};")
    return label


def hint(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setStyleSheet(f"color: {TEXT_E}; font-size: 11px;")
    label.setWordWrap(True)
    return label


def field_label(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setStyleSheet(f"color: {TEXT_D}; font-size: 12px;")
    return label


def row(*widgets: QWidget, spacing: int = 8, stretch_last: bool = False) -> QWidget:
    """把若干控件横向摆一行。"""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for index, widget in enumerate(widgets):
        if widget is None:
            layout.addStretch(1)
        else:
            layout.addWidget(widget)
            if stretch_last and index == len(widgets) - 1:
                widget.setSizePolicy(QSizePolicy.Expanding, widget.sizePolicy().verticalPolicy())
    return box


def plain_button(text: str, kind: str = "normal", tip: str = "") -> "QPushButton":
    from PyQt5.QtWidgets import QPushButton
    button = QPushButton(text)
    button.setStyleSheet(plain_button_style(kind))
    button.setCursor(Qt.PointingHandCursor)
    if tip:
        button.setToolTip(tip)
    return button


def clear_layout(layout) -> None:
    """清空一个布局里的所有控件（切页/刷新时用）。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def reset_column(layout: QVBoxLayout) -> None:
    """清空动态列并补回末尾的 stretch。

    必须补：clear_layout 会把末尾那个 stretch 也一起 takeAt 掉，
    之后 insert_before_stretch 就会插到最后一个真实控件后面，顺序全乱。
    """
    clear_layout(layout)
    layout.addStretch(1)


def scroll_column(parent: QWidget | None = None) -> tuple[QWidget, QVBoxLayout]:
    """返回 (滚动区域, 内部竖向布局) —— 配置页那种长表单用它。"""
    from PyQt5.QtWidgets import QScrollArea
    area = QScrollArea(parent)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.setSpacing(12)
    layout.addStretch(1)
    area.setWidget(inner)
    return area, layout


def insert_before_stretch(layout: QVBoxLayout, widget: QWidget) -> None:
    """插到末尾那个 stretch 之前（配置页是动态刷新的）。"""
    layout.insertWidget(max(0, layout.count() - 1), widget)


def style_line_edit(widget: QLineEdit, placeholder: str = "") -> QLineEdit:
    if placeholder:
        widget.setPlaceholderText(placeholder)
    return widget
