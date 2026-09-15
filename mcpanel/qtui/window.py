"""Qt 主窗口：侧边栏实例列表 + 顶部工具栏 + 六个页面 + 状态栏。

和网页版是同一个进程里的两个前端，共用同一个 InstanceManager，
所以哪边操作都即时可见，不存在两套状态。
"""
from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                             QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMainWindow, QMessageBox, QScrollArea, QSpinBox,
                             QStackedWidget, QVBoxLayout, QWidget)

import siui                                          # noqa: F401  必须先导入完成初始化

from .. import downloader
from . import theme as T
from .pages import (ConsolePage, FilesPage, InstallPage, PlayersPage,
                    PropertiesPage, SettingsPage)

STATE_TEXT = {"stopped": "已停止", "starting": "启动中", "running": "运行中",
              "stopping": "停止中", "crashed": "已崩溃"}
STATE_COLOR = {"stopped": T.TEXT_E, "starting": "#D8A657", "running": T.OK,
               "stopping": "#D8A657", "crashed": T.ERR, "ready": T.OK}


def siui_button(text: str, callback=None, tip: str = "") -> QWidget:
    """造一个 siui 按钮（失败时退回普通按钮，保证界面不会因为 API 变化整个起不来）。"""
    try:
        from siui.components.button import SiPushButtonRefactor
        button = SiPushButtonRefactor.withText(text)
        button.adjustSize()
    except Exception:  # noqa: BLE001
        button = T.plain_button(text, "accent")
    if tip:
        button.setToolTip(tip)
    if callback is not None:
        button.clicked.connect(lambda _=False: callback())
    return button


def duration_text(seconds) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "-"
    day, rest = divmod(seconds, 86400)
    hour, rest = divmod(rest, 3600)
    minute, sec = divmod(rest, 60)
    if day:
        return f"{day}天{hour}小时"
    if hour:
        return f"{hour}小时{minute}分"
    if minute:
        return f"{minute}分{sec}秒"
    return f"{sec}秒"


def size_text(num) -> str:
    if not num:
        return "-"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "-"


class InstanceItem(QFrame):
    """侧边栏里的一条实例。"""

    def __init__(self, window: "MainWindow", name: str) -> None:
        super().__init__()
        self.window = window
        self.name = name
        self.setObjectName("instItem")
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(7)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.title = QLabel(name)
        self.title.setStyleSheet(f"color: {T.TEXT_A}; font-weight: bold;")
        head.addWidget(self.dot)
        head.addWidget(self.title, 1)
        layout.addLayout(head)
        self.sub = QLabel("-")
        self.sub.setStyleSheet(f"color: {T.TEXT_E}; font-size: 11px;")
        layout.addWidget(self.sub)
        self._active = False
        self._state = "stopped"
        self._apply_style()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.window.select_instance(self.name)
        super().mousePressEvent(event)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._apply_style()

    def update_state(self, snapshot: dict) -> None:
        self._state = snapshot.get("state", "stopped")
        color = STATE_COLOR.get(self._state, T.TEXT_E)
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        if self._state == "running":
            self.sub.setText(f"玩家 {snapshot.get('player_count', 0)}/"
                             f"{snapshot.get('max_players', 0)} · "
                             f"{duration_text(snapshot.get('uptime'))}")
        else:
            self.sub.setText(f"{snapshot.get('core')} {snapshot.get('mc_version')} · "
                             f"{STATE_TEXT.get(self._state, self._state)}")
        self._apply_style()

    def _apply_style(self) -> None:
        border = T.ACCENT if self._active else "transparent"
        self.setStyleSheet(
            f"#instItem {{ background: {T.BG_D if self._active else T.BG_C};"
            f" border: 1px solid {border}; border-radius: 8px; }}")


class CreateInstanceDialog(QDialog):
    """新建实例。"""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("新建服务端实例")
        self.resize(520, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        root.addWidget(T.hint("实例名会作为文件夹名，只能用字母、数字、- 和 _"))

        root.addWidget(T.field_label("实例名称"))
        self.name = QLineEdit("server")
        root.addWidget(self.name)

        root.addWidget(T.field_label("核心类型"))
        self.core = QComboBox()
        for info in downloader.CORE_INFO:
            self.core.addItem(f"{info['name']} — {info['desc']}", info["id"])
        root.addWidget(self.core)

        line = QHBoxLayout()
        version_box = QVBoxLayout()
        version_box.addWidget(T.field_label("游戏版本"))
        self.version = QLineEdit("1.20.1")
        version_box.addWidget(self.version)
        port_box = QVBoxLayout()
        port_box.addWidget(T.field_label("服务端端口"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(25565)
        port_box.addWidget(self.port)
        line.addLayout(version_box)
        line.addLayout(port_box)
        root.addLayout(line)

        root.addWidget(T.field_label("服务器标语 MOTD"))
        self.motd = QLineEdit("欢迎来到我的服务器！")
        root.addWidget(self.motd)

        mem = QHBoxLayout()
        min_box = QVBoxLayout()
        min_box.addWidget(T.field_label("最小内存"))
        self.min_mem = QSpinBox()
        self.min_mem.setRange(256, 65536)
        self.min_mem.setSingleStep(256)
        self.min_mem.setValue(1024)
        self.min_mem.setSuffix(" MB")
        min_box.addWidget(self.min_mem)
        max_box = QVBoxLayout()
        max_box.addWidget(T.field_label("最大内存"))
        self.max_mem = QSpinBox()
        self.max_mem.setRange(512, 262144)
        self.max_mem.setSingleStep(256)
        self.max_mem.setValue(2048)
        self.max_mem.setSuffix(" MB")
        max_box.addWidget(self.max_mem)
        mem.addLayout(min_box)
        mem.addLayout(max_box)
        root.addLayout(mem)

        root.addWidget(T.field_label("Java 环境"))
        self.java = QComboBox()
        self.java.addItem("自动选择（推荐）", "")
        for java in window.manager.javas():
            self.java.addItem(f"Java {java['version']} {java['arch']}", java["path"])
        root.addWidget(self.java)

        self.eula = QCheckBox("自动同意 Minecraft EULA")
        self.eula.setChecked(True)
        self.install_now = QCheckBox("创建后立即下载服务端核心")
        self.install_now.setChecked(True)
        root.addWidget(self.eula)
        root.addWidget(self.install_now)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = T.plain_button("取消", "ghost")
        cancel.clicked.connect(self.reject)
        create = T.plain_button("创建实例", "accent")
        create.clicked.connect(self._create)
        buttons.addWidget(cancel)
        buttons.addWidget(create)
        root.addLayout(buttons)

    def _create(self) -> None:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请填写实例名称")
            return
        payload = {
            "port": self.port.value(),
            "motd": self.motd.text(),
            "min_memory": self.min_mem.value(),
            "max_memory": self.max_mem.value(),
            "java_path": self.java.currentData() or "",
            "accept_eula": self.eula.isChecked(),
        }
        try:
            instance = self.window.manager.create(
                name, self.core.currentData(), self.version.text().strip(), **payload)
            if self.install_now.isChecked():
                self.window.manager.start_install(
                    instance.name, instance.cfg.get("core"),
                    instance.cfg.get("mc_version"), None)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "创建失败", str(exc))
            return
        self.window.notify(f"实例 {instance.name} 创建成功")
        self.accept()
        self.window.on_instances_changed(select=instance.name)


class MainWindow(QMainWindow):
    # 从别的线程（HTTP 请求）唤起窗口，必须走信号回 GUI 线程
    request_activate = pyqtSignal()

    def __init__(self, server, config, manager, stop_all_on_exit: bool = False) -> None:
        super().__init__()
        self.server = server
        self.manager = manager
        self.config = config
        self.stop_all_on_exit = stop_all_on_exit
        self.current_name: str | None = None
        self._items: dict[str, InstanceItem] = {}
        self._bridges: list[object] = []
        self._last_state: dict[str, str] = {}

        self.setWindowTitle("MC Panel · Minecraft 面板开服器")
        self.resize(1360, 860)
        self.setMinimumSize(1080, 680)
        self.setStyleSheet(f"QMainWindow {{ background: {T.BG_A}; }}"
                           f"QWidget {{ color: {T.TEXT_B}; }}"
                           + T.stylesheet())

        self._build_toolbar()
        self._build_body()
        self._build_status()

        self.request_activate.connect(self._activate)

        # 页面
        self.console = ConsolePage(self)
        self.players = PlayersPage(self)
        self.properties = PropertiesPage(self)
        self.files = FilesPage(self)
        self.settings = SettingsPage(self)
        self.install = InstallPage(self)
        self.pages = {
            "console": self.console,
            "players": self.players,
            "props": self.properties,
            "files": self.files,
            "settings": self.settings,
            "install": self.install,
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self.refresh_instances()
        self.show_page("console")

    # ============================================================ 界面搭建
    def _build_toolbar(self) -> None:
        bar = QFrame()
        bar.setStyleSheet(f"QFrame {{ background: {T.BG_B};"
                          f" border-bottom: 1px solid {T.BG_E}; }}")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(14)

        brand = QLabel("MC Panel")
        brand.setStyleSheet(f"color: {T.TEXT_A}; font-size: 16px; font-weight: bold;")
        layout.addWidget(brand)

        self.title = QLabel("未选择实例")
        self.title.setStyleSheet(f"color: {T.TEXT_A}; font-size: 15px;")
        layout.addWidget(self.title)

        self.state_label = QLabel("空闲")
        layout.addWidget(self.state_label)

        layout.addSpacing(6)
        self.stat_labels = {}
        for key, caption in (("cpu", "CPU"), ("mem", "内存"),
                             ("uptime", "运行"), ("players", "玩家")):
            box = QVBoxLayout()
            box.setSpacing(0)
            value = QLabel("-")
            value.setStyleSheet(f"color: {T.TEXT_A}; font-size: 13px;")
            value.setAlignment(Qt.AlignRight)
            cap = QLabel(caption)
            cap.setStyleSheet(f"color: {T.TEXT_E}; font-size: 10px;")
            cap.setAlignment(Qt.AlignRight)
            box.addWidget(value)
            box.addWidget(cap)
            layout.addLayout(box)
            self.stat_labels[key] = value

        layout.addStretch(1)
        self.btn_start = siui_button("启动", lambda: self._power("start"))
        self.btn_restart = siui_button("重启", lambda: self._power("restart"))
        self.btn_stop = siui_button("停止", lambda: self._power("stop"))
        self.btn_kill = T.plain_button("强杀", "danger", "强制结束进程，可能丢存档")
        self.btn_kill.clicked.connect(lambda: self._power("kill"))
        for button in (self.btn_start, self.btn_restart, self.btn_stop, self.btn_kill):
            layout.addWidget(button)

        layout.addSpacing(8)
        self.btn_web = siui_button("切换到网页界面", self.open_web_ui,
                                   "在独立窗口里打开网页版界面")
        layout.addWidget(self.btn_web)

        self.setMenuWidget(bar)

    def _build_body(self) -> None:
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- 侧边栏
        side = QFrame()
        side.setFixedWidth(240)
        side.setStyleSheet(f"QFrame {{ background: {T.BG_B};"
                           f" border-right: 1px solid {T.BG_E}; }}")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(10)

        caption = QLabel("服务端实例")
        caption.setStyleSheet(f"color: {T.TEXT_E}; font-size: 11px;")
        side_layout.addWidget(caption)

        self.inst_scroll = QScrollArea()
        self.inst_scroll.setWidgetResizable(True)
        self.inst_scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self.inst_box = QVBoxLayout(holder)
        self.inst_box.setContentsMargins(0, 0, 0, 0)
        self.inst_box.setSpacing(6)
        self.inst_box.addStretch(1)
        self.inst_scroll.setWidget(holder)
        side_layout.addWidget(self.inst_scroll, 1)

        new_btn = siui_button("＋ 新建实例", self._create_instance)
        side_layout.addWidget(new_btn)
        layout.addWidget(side)

        # ---- 主区
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(16, 12, 16, 8)
        main_layout.setSpacing(10)

        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        self.tab_buttons: dict[str, QWidget] = {}
        for key, page in (("console", ConsolePage), ("players", PlayersPage),
                          ("props", PropertiesPage), ("files", FilesPage),
                          ("settings", SettingsPage), ("install", InstallPage)):
            button = T.plain_button(page.title, "ghost")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            tab_row.addWidget(button)
            self.tab_buttons[key] = button
        tab_row.addStretch(1)
        main_layout.addLayout(tab_row)

        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)
        layout.addWidget(main, 1)
        self.setCentralWidget(central)

    def _build_status(self) -> None:
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(f"color: {T.TEXT_D}; font-size: 11px;")
        self.statusBar().setStyleSheet(
            f"QStatusBar {{ background: {T.BG_B}; color: {T.TEXT_D};"
            f" border-top: 1px solid {T.BG_E}; }}")
        self.statusBar().addWidget(self.status_label)
        self.statusBar().setSizeGripEnabled(True)

    # ============================================================ 给页面的 API
    def current_instance(self):
        if not self.current_name:
            return None
        try:
            return self.manager.get(self.current_name)
        except Exception:  # noqa: BLE001
            return None

    def notify(self, text: str, kind: str = "ok") -> None:
        color = {"ok": T.OK, "warn": "#D8A657", "error": T.ERR}.get(kind, T.TEXT_D)
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.status_label.setText(str(text))

    def siui_button(self, text: str, callback=None, small: bool = False) -> QWidget:
        return siui_button(text, callback)

    def run_async(self, fn, on_done=None, on_fail=None) -> None:
        """在后台线程跑阻塞操作（停服、拉版本列表…），结果回 GUI 线程。"""
        bridge = _Bridge()

        def handle_done(result):
            if on_done:
                on_done(result)
            self._drop(bridge)

        def handle_fail(message):
            if on_fail:
                on_fail(message)
            else:
                self.notify(message, "error")
            self._drop(bridge)

        bridge.done.connect(handle_done)
        bridge.failed.connect(handle_fail)
        self._bridges.append(bridge)

        def work():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                bridge.failed.emit(str(exc))
                return
            bridge.done.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _drop(self, bridge) -> None:
        if bridge in self._bridges:
            self._bridges.remove(bridge)

    # ============================================================ 实例管理
    def refresh_instances(self) -> None:
        instances = self.manager.all()
        names = [instance.name for instance in instances]
        if self.current_name not in names:
            # 优先选中正在跑的实例：一打开就能看到活的日志和状态
            running = next((i for i in instances if i.state != "stopped"), None)
            self.current_name = (running or instances[0]).name if instances else None

        for name in list(self._items):
            if name not in names:
                item = self._items.pop(name)
                item.setParent(None)
                item.deleteLater()

        for index, name in enumerate(names):
            item = self._items.get(name)
            if item is None:
                item = InstanceItem(self, name)
                self._items[name] = item
                self.inst_box.insertWidget(index, item)
            item.set_active(name == self.current_name)

        if not names:
            self.title.setText("没有实例")
            self.state_label.setText("点左下角新建一个")
        self._apply_instance_headers()
        self._update_buttons(None)

    def on_instances_changed(self, select: str | None = None) -> None:
        self.refresh_instances()
        if select:
            self.select_instance(select)
        else:
            self.on_instance_changed()

    def select_instance(self, name: str) -> None:
        if name == self.current_name:
            # 再点一次也刷新一下当前页，避免界面卡在旧数据
            self.on_instance_changed()
            return
        self.current_name = name
        for item_name, item in self._items.items():
            item.set_active(item_name == name)
        self.console.on_instance_changed()
        self.on_instance_changed()

    def on_instance_changed(self) -> None:
        page = self.stack.currentWidget()
        if isinstance(page, (PropertiesPage, SettingsPage, FilesPage, InstallPage)):
            page.on_show()
        self._apply_instance_headers()

    def _apply_instance_headers(self) -> None:
        instance = self.current_instance()
        if instance is None:
            self.title.setText("未选择实例")
            self.state_label.setText("空闲")
            for label in self.stat_labels.values():
                label.setText("-")
            return
        snapshot = instance.snapshot()
        self.title.setText(snapshot["name"])
        self._apply_state(snapshot)

    def _apply_state(self, snapshot: dict) -> None:
        state = snapshot.get("state", "stopped")
        text = STATE_TEXT.get(state, state)
        if state == "stopped" and snapshot.get("installed") is False:
            text = f"{text}（未安装核心）"
        self.state_label.setText(f"· {text}")
        color = STATE_COLOR.get(state, T.TEXT_E)
        self.state_label.setStyleSheet(
            f"color: {color}; background: rgba(255,255,255,0.06);"
            f" border-radius: 9px; padding: 2px 10px;")
        self.stat_labels["cpu"].setText(
            "-" if snapshot.get("cpu") is None else f"{snapshot['cpu']}%")
        self.stat_labels["mem"].setText(size_text(snapshot.get("mem_used")))
        self.stat_labels["uptime"].setText(duration_text(snapshot.get("uptime")))
        self.stat_labels["players"].setText(
            f"{snapshot.get('player_count', 0)}/{snapshot.get('max_players', 0)}")

    def _update_buttons(self, snapshot: dict | None) -> None:
        busy = bool(snapshot) and snapshot.get("state") != "stopped"
        self.btn_start.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.btn_restart.setEnabled(busy)
        self.btn_kill.setEnabled(busy)

    # ============================================================ 电源操作
    def _power(self, action: str) -> None:
        instance = self.current_instance()
        if instance is None:
            self.notify("先选一个实例", "warn")
            return
        questions = {
            "kill": "强制结束进程可能导致世界数据未保存，确定吗？",
            "restart": "重启会先保存并关闭服务端，玩家会掉线，确定吗？",
        }
        if action in questions:
            if QMessageBox.question(self, "确认", questions[action],
                                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return

        def call():
            if action == "start":
                return instance.start()
            if action == "stop":
                return instance.stop()
            if action == "kill":
                return instance.kill(force=True)
            return instance.restart()

        messages = {"start": "已发送启动指令", "stop": "已发送停止指令，正在保存数据…",
                    "kill": "已强制结束进程", "restart": "正在重启…"}
        self.notify(f"{messages.get(action, '完成')}（{instance.name}）",
                    "warn" if action != "start" else "ok")
        # 启停都可能阻塞（尤其是 stop 要等服务端存档），丢到后台线程
        self.run_async(call, on_done=lambda _r: self._tick(),
                       on_fail=lambda message: self.notify(message, "error"))

    def _create_instance(self) -> None:
        dialog = CreateInstanceDialog(self)
        dialog.exec_()

    # ============================================================ 页面切换
    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        for name, button in self.tab_buttons.items():
            button.setChecked(name == key)
        page.on_show()

    def open_web_ui(self) -> None:
        """在当前进程的 HTTP 服务上打开网页界面。"""
        from ..desktop import open_web_window
        host = self.config.get("host") or "127.0.0.1"
        port = int(self.config.get("port") or 8080)
        url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        url = f"http://{url_host}:{port}/"
        if open_web_window(url, Path(self.config.root)):
            self.notify(f"已在独立窗口打开网页界面：{url}")
        else:
            self.notify("没能打开网页窗口，面板地址见状态栏下方提示", "warn")

    # ============================================================ 定时刷新
    def _tick(self) -> None:
        instance = self.current_instance()
        snapshot = None
        for item_instance in self.manager.all():
            try:
                item_snapshot = item_instance.snapshot()
            except Exception:  # noqa: BLE001
                continue
            item = self._items.get(item_instance.name)
            if item is not None:
                item.update_state(item_snapshot)
            self._last_state[item_instance.name] = item_snapshot.get("state")
            if instance is not None and item_instance.name == instance.name:
                snapshot = item_snapshot
        if snapshot is not None:
            self._apply_state(snapshot)
            self._update_buttons(snapshot)

        page = self.stack.currentWidget()
        if page is not None:
            try:
                page.on_tick(snapshot)
            except Exception as exc:  # noqa: BLE001
                self.notify(f"页面刷新出错：{exc}", "error")

    # ============================================================ 窗口激活
    def activate_from_any_thread(self) -> None:
        """供 HTTP 线程调用（网页版点「切换到 Qt 界面」时）。"""
        self.request_activate.emit()

    def _activate(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        running = [i.name for i in self.manager.all() if i.state != "stopped"]
        if running and not self.stop_all_on_exit:
            answer = QMessageBox.question(
                self, "退出 MC Panel",
                "还有 %d 个服务端在运行：%s\n\n"
                "关闭窗口后面板退出，服务端会继续在后台运行。\n要现在一并停止吗？"
                % (len(running), "、".join(running)),
                QMessageBox.Yes | QMessageBox.No)
            if answer == QMessageBox.Yes:
                self._stop_all_running()
        elif running and self.stop_all_on_exit:
            if QMessageBox.question(
                    self, "退出 MC Panel",
                    f"还有 {len(running)} 个服务端在运行，确定停止它们并退出吗？",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                event.ignore()
                return
            self._stop_all_running()
        self.timer.stop()
        event.accept()

    def _stop_all_running(self, limit: float = 12.0) -> None:
        """把 stop 指令发出去，然后有上限地等它们存档退出（别把关窗口变成卡死）。"""
        import time
        running = [i for i in self.manager.all() if i.state != "stopped"]
        if not running:
            return
        self.notify(f"正在停止 {len(running)} 个服务端…", "warn")
        for instance in running:
            try:
                instance.send("stop", echo=False)
                instance._stop_requested = True
            except Exception:  # noqa: BLE001
                pass
        deadline = time.time() + limit
        while time.time() < deadline:
            if all(i.state == "stopped" for i in running):
                break
            QApplication.processEvents()
            time.sleep(0.25)


class _Bridge(QObject):
    """跨线程回调用的信号载体。对象建在主线程，所以信号会自动排队回主线程执行。"""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)


def run_qt(server, config, manager, stop_all_on_exit: bool = False,
           selftest: float = 0.0) -> int:
    """启动 Qt 界面（主线程归 Qt，HTTP 服务已经在后台线程跑着了）。"""
    app = QApplication.instance() or QApplication([])
    window = MainWindow(server, config, manager, stop_all_on_exit)

    # 让网页版的「切换到 Qt 界面」按钮能把窗口叫到前面
    if server is not None:
        if hasattr(server, "set_qt_activator"):
            server.set_qt_activator(window.activate_from_any_thread)
        else:
            server.qt_activator = window.activate_from_any_thread

    window.show()

    if selftest:
        def finish():
            # 光有窗口尺寸证明不了画面渲染出来了，这里真抓一张图统计颜色种类：
            # 界面有内容的话颜色必然不止一两种，空白窗口则只有底色。
            try:
                image = window.grab().toImage()
                colors = set()
                for y in range(0, image.height(), 13):
                    for x in range(0, image.width(), 13):
                        colors.add(image.pixelColor(x, y).name())
                print(f"UI_QT_RENDER w={image.width()} h={image.height()} "
                      f"colors={len(colors)}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"UI_QT_RENDER_FAIL {exc}", flush=True)
            print("UI_QT_SELFTEST_OK", flush=True)
            window.close()
            app.quit()
        QTimer.singleShot(int(selftest * 1000), finish)
        print(f"UI_QT_MODE window={window.width()}x{window.height()}", flush=True)

    code = app.exec_()
    if server is not None and hasattr(server, "set_qt_activator"):
        server.set_qt_activator(None)
    return code
