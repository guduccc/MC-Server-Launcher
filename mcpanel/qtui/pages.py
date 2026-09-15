"""Qt 界面的六个页面。

每个页面都只负责"把后端数据画出来 + 把用户操作转成后端调用"，
业务逻辑（进程管理、下载、配置读写）全部复用 mcpanel.instance / downloader / properties，
和网页版共用同一套后端，不存在两套实现。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                             QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPlainTextEdit, QPushButton,
                             QSpinBox, QTableWidget, QTableWidgetItem,
                             QTextEdit, QVBoxLayout, QWidget)

from .. import downloader, properties as propsmod
from ..instance import JVM_PRESETS
from . import theme as T

QUICK_COMMANDS = ["list", "save-all", "whitelist list", "time set day",
                  "weather clear", "difficulty peaceful", "tps"]
ADMIN_COMMANDS = ["op ", "deop ", "kick ", "ban ", "whitelist add ", "gamemode creative ",
                  "tp ", "give ", "clear ", "stop"]


class BasePage(QWidget):
    """页面基类：主窗口通过 on_show / on_tick / on_instance_changed 驱动。"""

    title = "页面"

    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(12)
        self._built_for: str | None = None

    # ------------------------------------------------------------ 便捷访问
    @property
    def instance(self):
        return self.app.current_instance()

    def notify(self, text: str, kind: str = "ok") -> None:
        self.app.notify(text, kind)

    def guard(self) -> bool:
        """没有选中实例时提示一次。"""
        if self.instance is None:
            self.notify("先选一个实例", "warn")
            return False
        return True

    # ------------------------------------------------------------ 生命周期
    def on_show(self) -> None:
        pass

    def on_tick(self, snap: dict | None) -> None:
        pass

    def on_instance_changed(self) -> None:
        self._built_for = None
        self.on_show()


# ================================================================ 控制台

class ConsolePage(BasePage):
    title = "控制台"

    def __init__(self, app, parent=None) -> None:
        super().__init__(app, parent)
        self._seq = 0
        self._history: list[str] = []
        self._history_index = 0
        self._filter_progress = False

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(T.mono_font(9))
        self.log.setMaximumBlockCount(3000)      # 自动裁掉老行，防止越跑越卡
        self.log.setPlaceholderText("服务端日志会显示在这里…")
        self.log.setStyleSheet(f"QPlainTextEdit {{ background: #15131A; border: 1px solid {T.BG_E};"
                               f" border-radius: 8px; color: {T.TEXT_C}; }}")
        self.root.addWidget(self.log, 1)

        # 工具行
        bar = QHBoxLayout()
        bar.setSpacing(8)
        scroll_box = QCheckBox("自动滚动")
        scroll_box.setChecked(True)
        scroll_box.setStyleSheet(f"color: {T.TEXT_D};")
        self.auto_scroll = scroll_box

        filter_box = QCheckBox("过滤刷屏进度")
        filter_box.setStyleSheet(f"color: {T.TEXT_D};")
        filter_box.toggled.connect(self._on_filter_toggled)
        self.filter_box = filter_box

        clear_btn = T.plain_button("清屏", "ghost")
        clear_btn.clicked.connect(self._clear)
        copy_btn = T.plain_button("复制全部", "ghost")
        copy_btn.clicked.connect(self._copy_all)

        bar.addWidget(scroll_box)
        bar.addWidget(filter_box)
        bar.addStretch(1)
        bar.addWidget(copy_btn)
        bar.addWidget(clear_btn)
        bar.addWidget(self.app.siui_button("重载日志", self._reload, small=True))
        self.root.addLayout(bar)

        # 快捷指令
        quick = QHBoxLayout()
        quick.setSpacing(6)
        for command in QUICK_COMMANDS:
            btn = T.plain_button(command, "ghost")
            btn.setFont(T.mono_font(8))
            btn.clicked.connect(lambda _=False, c=command: self._send(c))
            quick.addWidget(btn)
        quick.addStretch(1)
        self.root.addLayout(quick)

        # 输入行
        line = QHBoxLayout()
        line.setSpacing(8)
        prompt = QLabel("›")
        prompt.setStyleSheet(f"color: {T.ACCENT}; font-size: 16px;")
        self.input = QLineEdit()
        self.input.setFont(T.mono_font(9))
        self.input.setPlaceholderText("输入服务端指令（list / op Steve / say 你好），↑↓ 翻历史")
        self.input.returnPressed.connect(self._send_current)
        self.input.installEventFilter(self)
        send_btn = self.app.siui_button("发送", self._send_current)
        line.addWidget(prompt)
        line.addWidget(self.input, 1)
        line.addWidget(send_btn)
        self.root.addLayout(line)

    # ------------------------------------------------------------ 交互
    def eventFilter(self, obj, event):
        """↑↓ 翻指令历史。"""
        if obj is self.input and event.type() == event.KeyPress:
            if event.key() == Qt.Key_Up:
                if self._history:
                    self._history_index = max(0, self._history_index - 1)
                    self.input.setText(self._history[self._history_index])
                return True
            if event.key() == Qt.Key_Down:
                if self._history:
                    self._history_index = min(len(self._history), self._history_index + 1)
                    self.input.setText(self._history[self._history_index]
                                       if self._history_index < len(self._history) else "")
                return True
        return super().eventFilter(obj, event)

    def _on_filter_toggled(self, value: bool) -> None:
        self._filter_progress = value
        self._reload()

    def _clear(self) -> None:
        self.log.clear()

    def _copy_all(self) -> None:
        from PyQt5.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log.toPlainText())
        self.notify("已复制控制台内容")

    def _reload(self) -> None:
        self._seq = 0
        self.log.clear()
        self._poll()

    def _send_current(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._history.append(text)
        self._history_index = len(self._history)
        self._send(text)

    def _send(self, command: str) -> None:
        if not self.guard():
            return
        try:
            self.instance.send(command)
            if command.strip() == "stop":
                self.notify("已发送停止指令，服务端正在保存数据…", "warn")
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")

    # ------------------------------------------------------------ 日志轮询
    def on_instance_changed(self) -> None:
        self._seq = 0
        self.log.clear()
        super().on_instance_changed()

    def on_show(self) -> None:
        if self.instance is not None and self._seq == 0:
            self._poll()

    def on_tick(self, snap: dict | None) -> None:
        self._poll()

    def _poll(self) -> None:
        inst = self.instance
        if inst is None:
            return
        # 后端是"seq >= since"语义，直接把上次渲染到的序号传过去。
        # 必须再按 seq 去重一次：否则最后一行会被重复取回来、在界面上重复出现。
        try:
            data = inst.get_logs(self._seq)
        except Exception:  # noqa: BLE001
            return

        lines = data.get("lines", [])
        scroll = self.auto_scroll.isChecked()
        bar = self.log.verticalScrollBar()
        appended = False
        for entry in lines:
            seq = int(entry.get("seq", 0))
            if seq <= self._seq:
                continue
            self._seq = seq
            if self._filter_progress and entry.get("progress"):
                continue
            self._append_line(entry)
            appended = True
        if appended and scroll:
            bar.setValue(bar.maximum())

    def _append_line(self, entry: dict) -> None:
        import html
        stamp = entry.get("t", 0)
        from datetime import datetime
        time_text = datetime.fromtimestamp(stamp).strftime("%H:%M:%S")
        color = T.LOG_COLORS.get(entry.get("level", "info"), T.TEXT_C)
        text = html.escape(str(entry.get("text", "")))
        self.log.appendHtml(
            f'<span style="color:{T.TEXT_E}">{time_text}</span> '
            f'<span style="color:{color}">{text}</span>')


# ================================================================ 玩家

class PlayersPage(BasePage):
    title = "玩家"

    def __init__(self, app, parent=None) -> None:
        super().__init__(app, parent)
        self._players: list[str] = []

        card = T.card(self)
        card.body.addWidget(T.card_title("在线玩家"))
        self.hint = T.hint("数据来自服务端日志")
        card.body.addWidget(self.hint)
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(6)
        card.body.addLayout(self.list_box)
        self.root.addWidget(card)

        admin = T.card(self)
        admin.body.addWidget(T.card_title("常用管理指令"))
        grid = QGridLayout()
        grid.setSpacing(6)
        for index, command in enumerate(ADMIN_COMMANDS):
            btn = T.plain_button(command.strip(), "ghost")
            btn.setFont(T.mono_font(8))
            btn.clicked.connect(lambda _=False, c=command: self._fill_command(c))
            grid.addWidget(btn, index // 6, index % 6)
        admin.body.addLayout(grid)
        admin.body.addWidget(T.hint("点一下会填到控制台输入框，补完参数再回车发送"))
        self.root.addWidget(admin)
        self.root.addStretch(1)

    def _fill_command(self, command: str) -> None:
        self.app.show_page("console")
        self.app.console.input.setText(command)
        self.app.console.input.setFocus()

    def on_instance_changed(self) -> None:
        self._players = []
        super().on_instance_changed()

    def on_tick(self, snap: dict | None) -> None:
        players = list((snap or {}).get("players") or [])
        if players == self._players:
            return
        self._players = players
        T.clear_layout(self.list_box)
        if not players:
            self.list_box.addWidget(T.hint("当前没有玩家在线"))
            return
        for name in players:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            avatar = QLabel(name[:1].upper())
            avatar.setFixedSize(26, 26)
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setStyleSheet(f"background: {T.BG_D}; border-radius: 6px;"
                                 f" color: {T.ACCENT}; font-weight: bold;")
            label = QLabel(name)
            label.setStyleSheet(f"color: {T.TEXT_B};")
            op_btn = T.plain_button("OP", "ghost")
            op_btn.clicked.connect(lambda _=False, n=name: self._command(f"op {n}"))
            kick_btn = T.plain_button("踢出", "ghost")
            kick_btn.clicked.connect(lambda _=False, n=name: self._command(f"kick {n}"))
            layout.addWidget(avatar)
            layout.addWidget(label, 1)
            layout.addWidget(op_btn)
            layout.addWidget(kick_btn)
            self.list_box.addWidget(row)

    def _command(self, command: str) -> None:
        if not self.guard():
            return
        try:
            self.instance.send(command)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")


# ================================================================ 服务端配置

class PropertiesPage(BasePage):
    title = "服务端配置"

    def __init__(self, app, parent=None) -> None:
        super().__init__(app, parent)
        self._editors: list[tuple] = []      # (key, widget, kind, 原始值)

        self.scroll, self.column = T.scroll_column(self)
        self.root.addWidget(self.scroll, 1)

        bar = QHBoxLayout()
        self.status = T.hint("未修改")
        reload_btn = T.plain_button("重新加载", "ghost")
        reload_btn.clicked.connect(self.on_show)
        save_btn = self.app.siui_button("保存修改", self._save)
        bar.addWidget(self.status, 1)
        bar.addWidget(reload_btn)
        bar.addWidget(save_btn)
        self.root.addLayout(bar)

    def on_show(self) -> None:
        inst = self.instance
        T.reset_column(self.column)
        self._editors = []
        if inst is None:
            T.insert_before_stretch(self.column, T.hint("先在左边选一个实例"))
            return
        try:
            form = inst.properties()
        except Exception as exc:  # noqa: BLE001
            T.insert_before_stretch(self.column, T.hint(f"读取 server.properties 失败：{exc}"))
            return

        for group in form["groups"]:
            card = T.card()
            card.body.addWidget(T.card_title(group["title"]))
            for field in group["fields"]:
                card.body.addWidget(self._field_row(field))
            T.insert_before_stretch(self.column, card)
        self._mark_dirty()

    def _field_row(self, field: dict) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        head = QHBoxLayout()
        name = QLabel(field["label"])
        name.setStyleSheet(f"color: {T.TEXT_C};")
        key = QLabel(field["key"])
        key.setStyleSheet(f"color: {T.TEXT_E}; font-size: 11px;")
        head.addWidget(name, 1)
        head.addWidget(key)
        layout.addLayout(head)

        kind = field["type"]
        value = field["value"]
        if kind == "bool":
            widget = QCheckBox("开启")
            widget.setChecked(str(value).lower() == "true")
            widget.toggled.connect(self._mark_dirty)
        elif kind == "enum":
            widget = QComboBox()
            for choice in field.get("choices", []):
                widget.addItem(choice["label"], choice["value"])
            index = widget.findData(value)
            widget.setCurrentIndex(index if index >= 0 else -1)
            widget.currentIndexChanged.connect(self._mark_dirty)
        elif kind == "int":
            widget = QSpinBox()
            widget.setRange(-2147483647, 2147483647)
            try:
                widget.setValue(int(value))
            except (TypeError, ValueError):
                widget.setValue(0)
            widget.valueChanged.connect(self._mark_dirty)
        else:
            widget = QLineEdit(str(value))
            if kind == "password":
                widget.setEchoMode(QLineEdit.Password)
            if not field.get("present"):
                widget.setPlaceholderText("未设置（不会写入文件）")
            widget.textChanged.connect(self._mark_dirty)
        layout.addWidget(widget)

        if field.get("desc"):
            layout.addWidget(T.hint(field["desc"]))
        # 原始值用和 _read 完全一致的方式取，避免"看起来没改却算改了"
        self._editors.append((field["key"], widget, kind, self._read(widget, kind)))
        return box

    @staticmethod
    def _read(widget, kind: str) -> str:
        """把控件当前值统一读成字符串，方便和原始值直接比较。"""
        if kind == "bool":
            return "true" if widget.isChecked() else "false"
        if kind == "enum":
            data = widget.currentData()
            return "" if data is None else str(data)
        if kind == "int":
            return str(widget.value())
        return widget.text()

    def _updates(self) -> dict:
        """只返回真正被改过的字段。"""
        updates = {}
        for key, widget, kind, original in self._editors:
            current = self._read(widget, kind)
            if current != original:
                updates[key] = current
        return updates

    def _mark_dirty(self) -> None:
        count = len(self._updates())
        self.status.setText(f"已修改 {count} 项" if count else "未修改")
        self.status.setStyleSheet(
            f"color: {T.OK if count else T.TEXT_E}; font-size: 11px;")

    def _save(self) -> None:
        if not self.guard():
            return
        updates = self._updates()
        if not updates:
            self.notify("没有需要保存的修改")
            return
        try:
            self.instance.update_properties(updates)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        self.notify(f"已保存 {len(updates)} 项配置，重启服务端后生效")
        self.on_show()


# ================================================================ 文件

class FilesPage(BasePage):
    title = "文件"

    def __init__(self, app, parent=None) -> None:
        super().__init__(app, parent)
        self.path = ""

        bar = QHBoxLayout()
        self.crumb = QLabel("实例根目录")
        self.crumb.setStyleSheet(f"color: {T.TEXT_C}; font-family: Consolas, monospace;")
        back_btn = T.plain_button("← 上级", "ghost")
        back_btn.clicked.connect(self._go_up)
        up_btn = T.plain_button("上传", "ghost")
        up_btn.clicked.connect(self._upload)
        mkdir_btn = T.plain_button("新建文件夹", "ghost")
        mkdir_btn.clicked.connect(self._mkdir)
        refresh_btn = T.plain_button("刷新", "ghost")
        refresh_btn.clicked.connect(lambda: self._load(self.path))
        bar.addWidget(self.crumb, 1)
        bar.addWidget(back_btn)
        bar.addWidget(up_btn)
        bar.addWidget(mkdir_btn)
        bar.addWidget(refresh_btn)
        self.root.addLayout(bar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "大小", "修改时间", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setColumnWidth(0, 330)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 150)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.root.addWidget(self.table, 1)

        self.root.addWidget(T.hint("双击目录进入，双击文本文件在线编辑；"
                                   "支持 .properties/.yml/.json/.txt/.conf/.log"))

    def on_instance_changed(self) -> None:
        self.path = ""
        super().on_instance_changed()

    def on_show(self) -> None:
        self._load(self.path)

    def _load(self, path: str) -> None:
        inst = self.instance
        if inst is None:
            self.table.setRowCount(0)
            return
        try:
            data = inst.list_dir(path)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        self.path = data.get("path", "")
        self.crumb.setText("实例根目录" + (f" / {self.path.replace('/', ' / ')}" if self.path else ""))
        entries = data.get("entries", [])
        self.table.setRowCount(len(entries))
        from datetime import datetime
        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(("📁 " if entry["dir"] else "📄 ") + entry["name"])
            name_item.setData(Qt.UserRole, entry)
            if entry["dir"]:
                name_item.setForeground(Qt.cyan)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(
                "<DIR>" if entry["dir"] else entry.get("size_text", "")))
            self.table.setItem(row, 2, QTableWidgetItem(
                datetime.fromtimestamp(entry["mtime"]).strftime("%Y-%m-%d %H:%M")))
            self.table.setCellWidget(row, 3, self._ops(entry))

    def _ops(self, entry: dict) -> QWidget:
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if not entry["dir"] and entry.get("editable"):
            edit = T.plain_button("编辑", "ghost")
            edit.clicked.connect(lambda _=False, e=entry: self._edit(e["path"], e["name"]))
            layout.addWidget(edit)
        if not entry["dir"]:
            save = T.plain_button("另存为", "ghost")
            save.clicked.connect(lambda _=False, e=entry: self._download(e["path"], e["name"]))
            layout.addWidget(save)
        delete = T.plain_button("删除", "danger")
        delete.clicked.connect(lambda _=False, e=entry: self._delete(e))
        layout.addWidget(delete)
        layout.addStretch(1)
        return box

    def _go_up(self) -> None:
        if not self.path:
            return
        self._load("/".join(self.path.split("/")[:-1]))

    def _on_double_click(self, row: int, _column: int) -> None:
        entry = self.table.item(row, 0).data(Qt.UserRole)
        if entry["dir"]:
            self._load(entry["path"])
        elif entry.get("editable"):
            self._edit(entry["path"], entry["name"])
        else:
            self.notify("该类型文件不支持在线编辑", "warn")

    def _delete(self, entry: dict) -> None:
        if QMessageBox.question(self, "删除确认", f"确定删除 {entry['name']} 吗？此操作不可恢复。",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.instance.delete_entry(entry["path"])
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        self.notify(f"已删除 {entry['name']}")
        self._load(self.path)

    def _mkdir(self) -> None:
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名（可用 a/b 建多级）")
        if not ok or not name.strip():
            return
        rel = f"{self.path}/{name.strip()}" if self.path else name.strip()
        try:
            self.instance.make_dir(rel)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        self._load(self.path)

    def _upload(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传的文件")
        if not paths:
            return
        for path in paths:
            from pathlib import Path
            file = Path(path)
            try:
                self.instance.save_upload(self.path, file.name, file.read_bytes())
            except Exception as exc:  # noqa: BLE001
                self.notify(f"{file.name} 上传失败：{exc}", "error")
                continue
            self.notify(f"已上传 {file.name}")
        self._load(self.path)

    def _download(self, rel: str, name: str) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "保存到", name)
        if not target:
            return
        try:
            data = self.instance.read_file(rel)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        from pathlib import Path
        Path(target).write_text(data["content"], encoding="utf-8")
        self.notify(f"已保存到 {target}")

    def _edit(self, rel: str, name: str) -> None:
        try:
            data = self.instance.read_file(rel)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        dialog = FileEditorDialog(self, name, rel, data["content"])
        if dialog.exec_() != QDialog.Accepted:
            return
        try:
            self.instance.write_file(rel, dialog.content())
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        self.notify(f"已保存 {name}")
        self._load(self.path)


class FileEditorDialog(QDialog):
    def __init__(self, parent, name: str, rel: str, content: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑 {name}")
        self.resize(860, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        path_label = T.hint(rel)
        layout.addWidget(path_label)
        self.editor = QPlainTextEdit(content)
        self.editor.setFont(T.mono_font(9))
        layout.addWidget(self.editor, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = T.plain_button("取消", "ghost")
        cancel.clicked.connect(self.reject)
        save = T.plain_button("保存", "accent")
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def content(self) -> str:
        return self.editor.toPlainText()


# ================================================================ 实例设置

class SettingsPage(BasePage):
    title = "实例设置"

    def __init__(self, app, parent=None) -> None:
        super().__init__(app, parent)
        self.scroll, self.column = T.scroll_column(self)
        self.root.addWidget(self.scroll, 1)

        # ---- 运行配置
        run_card = T.card()
        run_card.body.addWidget(T.card_title("运行配置"))

        self.core_label = T.hint("-")
        run_card.body.addWidget(T.field_label("核心类型"))
        run_card.body.addWidget(self.core_label)

        self.version = QLineEdit()
        run_card.body.addWidget(T.field_label("游戏版本"))
        run_card.body.addWidget(self.version)

        self.java = QComboBox()
        run_card.body.addWidget(T.field_label("Java 路径"))
        run_card.body.addWidget(self.java)

        self.min_mem = QSpinBox()
        self.min_mem.setRange(256, 65536)
        self.min_mem.setSingleStep(256)
        self.min_mem.setSuffix(" MB")
        self.max_mem = QSpinBox()
        self.max_mem.setRange(512, 262144)
        self.max_mem.setSingleStep(256)
        self.max_mem.setSuffix(" MB")
        mem_row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(T.field_label("最小内存"))
        left.addWidget(self.min_mem)
        right = QVBoxLayout()
        right.addWidget(T.field_label("最大内存"))
        right.addWidget(self.max_mem)
        mem_row.addLayout(left)
        mem_row.addLayout(right)
        run_card.body.addLayout(mem_row)

        run_card.body.addWidget(T.field_label("JVM 参数"))
        presets = QHBoxLayout()
        presets.setSpacing(6)
        for preset in JVM_PRESETS:
            btn = T.plain_button(preset["name"], "ghost")
            btn.clicked.connect(lambda _=False, a=preset["args"]: self.jvm.setPlainText(a))
            presets.addWidget(btn)
        presets.addStretch(1)
        run_card.body.addLayout(presets)
        self.jvm = QPlainTextEdit()
        self.jvm.setFixedHeight(78)
        self.jvm.setFont(T.mono_font(8))
        run_card.body.addWidget(self.jvm)

        self.server_args = QLineEdit()
        run_card.body.addWidget(T.field_label("服务端参数"))
        run_card.body.addWidget(self.server_args)

        self.stop_timeout = QSpinBox()
        self.stop_timeout.setRange(10, 600)
        self.stop_timeout.setSingleStep(10)
        self.stop_timeout.setSuffix(" 秒")
        run_card.body.addWidget(T.field_label("停机最长等待（大世界建议调大）"))
        run_card.body.addWidget(self.stop_timeout)

        self.note = QLineEdit()
        run_card.body.addWidget(T.field_label("备注"))
        run_card.body.addWidget(self.note)

        self.eula = QCheckBox("自动同意 EULA（eula.txt = true）")
        self.autorestart = QCheckBox("进程异常退出时自动重启")
        run_card.body.addWidget(self.eula)
        run_card.body.addWidget(self.autorestart)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self.app.siui_button("保存设置", self._save))
        run_card.body.addLayout(save_row)
        T.insert_before_stretch(self.column, run_card)

        # ---- 实例信息
        info_card = T.card()
        info_card.body.addWidget(T.card_title("实例信息"))
        self.info_box = QVBoxLayout()
        self.info_box.setSpacing(4)
        info_card.body.addLayout(self.info_box)
        T.insert_before_stretch(self.column, info_card)

        # ---- 危险区
        danger = T.card()
        danger.body.addWidget(T.card_title("危险操作"))
        danger.body.addWidget(T.hint("删除实例会移除面板中的记录并停止进程。"
                                     "勾选下面的选项会连实例目录（含世界存档）一起删掉。"))
        self.remove_files = QCheckBox("同时删除实例目录及全部文件")
        danger.body.addWidget(self.remove_files)
        row = QHBoxLayout()
        row.addStretch(1)
        delete_btn = T.plain_button("删除实例", "danger")
        delete_btn.clicked.connect(self._delete)
        row.addWidget(delete_btn)
        danger.body.addLayout(row)
        T.insert_before_stretch(self.column, danger)

    # ------------------------------------------------------------ 数据
    def on_instance_changed(self) -> None:
        self._built_for = None
        super().on_instance_changed()

    def on_show(self) -> None:
        inst = self.instance
        if inst is None:
            return
        detail = inst.detail()
        self.core_label.setText(f"{detail.get('core')} {detail.get('mc_version')}")
        self.version.setText(detail.get("mc_version") or "")
        self.min_mem.setValue(int(detail.get("min_memory") or 1024))
        self.max_mem.setValue(int(detail.get("max_memory") or 2048))
        self.jvm.setPlainText(detail.get("jvm_args") or "")
        self.server_args.setText(detail.get("server_args") or "")
        self.stop_timeout.setValue(int(detail.get("stop_timeout") or 90))
        self.note.setText(detail.get("note") or "")
        self.eula.setChecked(bool(detail.get("accept_eula")))
        self.autorestart.setChecked(bool(detail.get("auto_restart")))

        self.java.clear()
        self.java.addItem("自动选择（推荐）", "")
        current = detail.get("java_path") or ""
        found = False
        for java in self.app.manager.javas():
            self.java.addItem(f"Java {java['version']} {java['arch']} — {java['path']}",
                              java["path"])
            if java["path"] == current:
                found = True
        if current and not found:
            self.java.addItem(f"当前：{current}", current)
        index = self.java.findData(current)
        self.java.setCurrentIndex(index if index >= 0 else 0)

        T.clear_layout(self.info_box)
        rows = [
            ("实例目录", detail.get("root")),
            ("启动入口", (detail.get("launch") or {}).get("target") or "未安装"),
            ("创建时间", self._fmt(detail.get("created_at"))),
            ("累计运行", self._duration(detail.get("total_uptime"))),
            ("EULA", "已自动同意" if detail.get("accept_eula") else "未同意"),
            ("自动重启", "开" if detail.get("auto_restart") else "关"),
        ]
        for name, value in rows:
            line = QHBoxLayout()
            key = QLabel(name)
            key.setStyleSheet(f"color: {T.TEXT_E};")
            val = QLabel(str(value))
            val.setStyleSheet(f"color: {T.TEXT_C};")
            val.setWordWrap(True)
            line.addWidget(key)
            line.addStretch(1)
            line.addWidget(val)
            self.info_box.addLayout(line)

    @staticmethod
    def _fmt(stamp) -> str:
        if not stamp:
            return "-"
        from datetime import datetime
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _duration(seconds) -> str:
        seconds = int(seconds or 0)
        if seconds <= 0:
            return "-"
        day, rest = divmod(seconds, 86400)
        hour, rest = divmod(rest, 3600)
        minute, sec = divmod(rest, 60)
        if day:
            return f"{day} 天 {hour} 小时"
        if hour:
            return f"{hour} 小时 {minute} 分"
        if minute:
            return f"{minute} 分 {sec} 秒"
        return f"{sec} 秒"

    def _save(self) -> None:
        if not self.guard():
            return
        if self.min_mem.value() > self.max_mem.value():
            self.notify("最小内存不能大于最大内存", "error")
            return
        payload = {
            "jvm_args": self.jvm.toPlainText().strip(),
            "server_args": self.server_args.text().strip(),
            "min_memory": self.min_mem.value(),
            "max_memory": self.max_mem.value(),
            "stop_timeout": self.stop_timeout.value(),
            "java_path": self.java.currentData() or "",
            "accept_eula": self.eula.isChecked(),
            "auto_restart": self.autorestart.isChecked(),
            "note": self.note.text().strip(),
            "mc_version": self.version.text().strip(),
        }
        try:
            self.instance.update_config(payload)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        self.notify("设置已保存，下次启动生效")
        self.app.refresh_instances()

    def _delete(self) -> None:
        if not self.guard():
            return
        name = self.instance.name
        remove = self.remove_files.isChecked()
        tip = "连同全部文件一起删除" if remove else "只从面板移除（保留磁盘文件）"
        if QMessageBox.question(self, "删除实例", f"确定要删除实例「{name}」吗？\n{tip}",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            result = self.app.manager.delete(name, remove_files=remove)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        if result.get("warning"):
            self.notify(result["warning"], "warn")
        else:
            self.notify(f"实例 {name} 已删除")
        self.app.on_instances_changed()


# ================================================================ 安装 / 升级

class InstallPage(BasePage):
    title = "安装 / 升级"

    def __init__(self, app, parent=None) -> None:
        super().__init__(app, parent)
        self.scroll, self.column = T.scroll_column(self)
        self.root.addWidget(self.scroll, 1)

        card = T.card()
        card.body.addWidget(T.card_title("下载服务端核心"))
        card.body.addWidget(T.hint("下载和安装期间可以在「控制台」看进度日志"))

        card.body.addWidget(T.field_label("核心类型"))
        self.core = QComboBox()
        for info in downloader.CORE_INFO:
            self.core.addItem(f"{info['name']} — {info['desc']}", info["id"])
        self.core.currentIndexChanged.connect(self._on_core_changed)
        card.body.addWidget(self.core)

        card.body.addWidget(T.field_label("游戏版本"))
        version_row = QHBoxLayout()
        self.version = QLineEdit()
        self.version.setPlaceholderText("例如 1.20.1 / 1.12.2")
        load_btn = T.plain_button("拉取版本列表", "ghost")
        load_btn.clicked.connect(self._load_versions)
        version_row.addWidget(self.version, 1)
        version_row.addWidget(load_btn)
        card.body.addLayout(version_row)
        self.version_hint = T.hint("")
        card.body.addWidget(self.version_hint)

        card.body.addWidget(T.field_label("构建版本（可选，留空取最新）"))
        build_row = QHBoxLayout()
        self.build = QComboBox()
        self.build.setEditable(True)
        self.build.addItem("latest", "")
        build_btn = T.plain_button("查询构建", "ghost")
        build_btn.clicked.connect(self._load_builds)
        build_row.addWidget(self.build, 1)
        build_row.addWidget(build_btn)
        card.body.addLayout(build_row)

        install_row = QHBoxLayout()
        install_row.addStretch(1)
        install_row.addWidget(self.app.siui_button("开始下载 / 安装", self._install))
        card.body.addLayout(install_row)
        T.insert_before_stretch(self.column, card)

        progress_card = T.card()
        progress_card.body.addWidget(T.card_title("安装进度"))
        from siui.components.progress_bar_ import SiProgressBarRefactor
        self.progress = SiProgressBarRefactor(progress_card)
        self.progress.setMaximum(1000)
        progress_card.body.addWidget(self.progress)
        self.phase = T.hint("阶段：-")
        self.message = T.hint("状态：-")
        progress_card.body.addWidget(self.phase)
        progress_card.body.addWidget(self.message)
        T.insert_before_stretch(self.column, progress_card)

        self.current_card = T.card()
        self.current_card.body.addWidget(T.card_title("当前核心"))
        self.current_box = QVBoxLayout()
        self.current_card.body.addLayout(self.current_box)
        T.insert_before_stretch(self.column, self.current_card)

    # ------------------------------------------------------------ 逻辑
    def _on_core_changed(self) -> None:
        core = self.core.currentData()
        tips = {
            "vanilla": "官方原版：无插件、无模组，适合纯净生存。",
            "paper": "Paper：性能好、生态最全，最多人用的插件端。",
            "purpur": "Purpur：在 Paper 基础上多了很多可调玩法选项。",
            "fabric": "Fabric：模组端，需自己往 mods 文件夹放模组。",
            "forge": "Forge：1.12.2 等老版本模组生态最好；安装器需联网拉依赖库。",
            "spigot": "Spigot：通过 BuildTools 现场编译，需要本机安装 Git，耗时较长。",
            "custom": "自定义：把整合包 jar 上传到实例目录后会自动识别。",
        }
        self.version_hint.setText(tips.get(core, ""))

    def on_show(self) -> None:
        inst = self.instance
        T.clear_layout(self.current_box)
        if inst is None:
            self.current_box.addWidget(T.hint("先在左边选一个实例"))
            return
        detail = inst.detail()
        for name, value in (
            ("核心类型", detail.get("core")),
            ("游戏版本", detail.get("mc_version")),
            ("启动入口", (detail.get("launch") or {}).get("target") or "未安装"),
            ("是否已安装", "是" if detail.get("installed") else "否"),
        ):
            line = QHBoxLayout()
            key = QLabel(name)
            key.setStyleSheet(f"color: {T.TEXT_E};")
            val = QLabel(str(value))
            val.setStyleSheet(f"color: {T.TEXT_C};")
            val.setWordWrap(True)
            line.addWidget(key)
            line.addStretch(1)
            line.addWidget(val)
            self.current_box.addLayout(line)
        if not self.version.text().strip():
            self.version.setText(detail.get("mc_version") or "")
        index = self.core.findData(detail.get("core"))
        if index >= 0:
            self.core.setCurrentIndex(index)

    def on_instance_changed(self) -> None:
        self._was_running = False
        super().on_instance_changed()

    def on_tick(self, snap: dict | None) -> None:
        task = (snap or {}).get("task")
        if not task:
            # 刚刚装完：刷新"当前核心"和侧边栏，否则界面上还写着"未安装"
            if getattr(self, "_was_running", False):
                self._was_running = False
                self.on_show()
                self.app.refresh_instances()
            self.progress.setValue(0)
            self.phase.setText("阶段：-")
            self.message.setText("状态：-")
            return
        percent = float(task.get("percent") or 0)
        self.progress.setValue(int(min(100, max(0, percent)) * 10))
        self._was_running = bool(task.get("running"))
        self.phase.setText(f"阶段：{task.get('phase', '-')}")
        if task.get("running"):
            self.progress.setState(self.progress.State.Loading)
        elif task.get("error"):
            self.progress.setState(self.progress.State.Error)
        else:
            self.progress.setState(self.progress.State.Idle)
        text = task.get("message") or "-"
        if task.get("error"):
            text = f"{text}（错误：{task['error']}）"
        self.message.setText(f"状态：{text}")

    def _load_versions(self) -> None:
        core = self.core.currentData()
        self.notify(f"正在拉取 {core} 的版本列表…")
        self.app.run_async(
            lambda: downloader.list_versions(core),
            on_done=self._versions_loaded,
            on_fail=lambda message: self.notify(message, "error"))

    def _versions_loaded(self, versions: list[dict]) -> None:
        releases = [v for v in versions if v.get("type") == "release"]
        self.notify(f"共 {len(releases)} 个正式版本")
        self.version_hint.setText(
            "、".join(v["version"] for v in releases[:12]) + (" …" if len(releases) > 12 else ""))
        if not self.version.text().strip() and releases:
            self.version.setText(releases[0]["version"])

    def _load_builds(self) -> None:
        core = self.core.currentData()
        version = self.version.text().strip()
        if not version:
            self.notify("先填游戏版本", "warn")
            return
        if core in ("fabric", "spigot", "custom"):
            self.notify(f"{core} 没有构建号的概念，直接填版本即可", "warn")
            return
        self.app.run_async(
            lambda: downloader.list_builds(core, version),
            on_done=self._builds_loaded,
            on_fail=lambda message: self.notify(message, "error"))

    def _builds_loaded(self, builds: list[dict]) -> None:
        self.build.clear()
        self.build.addItem("latest（最新）", "")
        for build in builds[:200]:
            note = build.get("note") or ""
            self.build.addItem(f"#{build['build']} {note}".strip(), str(build["build"]))
        self.notify(f"找到 {len(builds)} 个构建")

    def _install(self) -> None:
        if not self.guard():
            return
        core = self.core.currentData()
        version = self.version.text().strip()
        if not version:
            self.notify("请填写游戏版本", "warn")
            return
        if QMessageBox.question(
                self, "确认安装",
                f"为实例「{self.instance.name}」下载安装 {core} {version}？\n"
                "安装会覆盖同名的服务端核心 jar。",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        build = self.build.currentData() or None
        try:
            self.app.manager.start_install(self.instance.name, core, version, build)
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), "error")
            return
        self.notify("安装已开始，切到「控制台」可以看进度")
        self.app.show_page("console")
