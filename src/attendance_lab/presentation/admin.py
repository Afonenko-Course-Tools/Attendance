"""Private main window coordinating independent views and queued reader events."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from attendance_lab.application.controller import AttendanceController
from attendance_lab.application.files import CsvPort
from attendance_lab.application.service import AttendanceService
from attendance_lab.presentation.commands import Commands
from attendance_lab.presentation.common import (
    STYLE,
    actions,
    guarded,
    lesson_label,
    plain_label,
    private_message,
)
from attendance_lab.presentation.history import HistoryPane
from attendance_lab.presentation.lesson_workspace import LessonWorkspace
from attendance_lab.presentation.logs import LogsWindow
from attendance_lab.presentation.preparation import PreparationPane
from attendance_lab.presentation.public import PublicWindow
from attendance_lab.presentation.reports import ReportsPane
from attendance_lab.presentation.schedule import SchedulePane
from attendance_lab.presentation.students import StudentsPane


class MainWindow(QMainWindow):
    def __init__(
        self,
        service: AttendanceService,
        controller: AttendanceController,
        *,
        files: CsvPort,
        demo: bool,
        database_path: Path,
        default_timezone: str,
    ) -> None:
        super().__init__()
        self.service = service
        self.controller = controller
        self.demo = demo
        self.database_path = database_path
        self.default_timezone = default_timezone
        self.reader_status = "Не подключён"
        self.log_lesson_id: int | None = None
        self._closing = False
        self._session_ui_active = False
        self.setWindowTitle(
            "Учёт посещаемости — "
            + ("ИМИТАТОР · тестовая база" if demo else "Приватное управление")
        )
        self.resize(1320, 920)
        self.setMinimumSize(980, 720)
        self.setStyleSheet(STYLE)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(7)
        heading = plain_label("Учёт посещаемости")
        heading.setObjectName("Heading")
        title_row = QHBoxLayout()
        logo_path = Path(__file__).parent / "assets" / "bsu-wordmark.png"
        if logo_path.exists():
            logo = plain_label()
            logo.setPixmap(
                QPixmap(str(logo_path)).scaled(
                    180,
                    58,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            title_row.addWidget(logo)
        title_row.addWidget(heading)
        title_row.addStretch()
        layout.addLayout(title_row)
        subtitle = plain_label(
            "Локальный журнал занятий · "
            + ("ИМИТАТОР — отдельная тестовая база" if demo else "PC/SC · приватное управление")
        )
        subtitle.setObjectName("Muted")
        subtitle.hide()
        self.active_banner = plain_label(wrap=True)
        self.active_banner.setObjectName("ActiveBanner")
        layout.addWidget(self.active_banner)
        self.stop_button = QPushButton("Завершить регистрацию")
        self.stop_button.clicked.connect(self.close_registration)
        active_actions = QHBoxLayout()
        self.public_button = QPushButton("Публичное окно")
        self.public_button.setObjectName("Primary")
        self.public_button.clicked.connect(self.show_public)
        active_actions.addWidget(self.public_button)
        logs_button = QPushButton("Приватный журнал активного занятия")
        logs_button.clicked.connect(self.show_active_log)
        active_actions.addWidget(logs_button)
        self.open_button = QPushButton("Открыть регистрацию · F9")
        self.open_button.clicked.connect(lambda: self.workspace.open_selected())
        active_actions.addWidget(self.open_button)
        active_actions.addWidget(self.stop_button)
        active_actions.addStretch()
        layout.addLayout(active_actions)
        self.tabs = QTabWidget()
        self.schedule = SchedulePane(
            service, self.refresh, self.open_registration, self.show_log, default_timezone, self
        )
        self.students = StudentsPane(service, files, self.refresh, self)
        self.reports = ReportsPane(service, files, self)
        self.workspace = LessonWorkspace(
            service,
            controller,
            self.registration_changed,
            self,
            open_lesson=self.open_registration,
            edit_card=self.students.bind_uid,
        )
        self.preparation = PreparationPane(
            service, self.refresh, self.schedule, self.students, self
        )
        self.history = HistoryPane(service, self)
        self.workspace.evidence_changed = self.refresh_evidence
        self.students.binding_changed = self.card_binding_changed
        self.history.evidence_changed = self.refresh_evidence
        self.tabs.addTab(self.workspace, "Занятие")
        self.tabs.addTab(self.preparation, "Подготовка")
        self.tabs.addTab(self.history, "История")
        settings = self._settings()
        settings_layout = settings.layout()
        assert isinstance(settings_layout, QVBoxLayout)
        settings_layout.insertWidget(0, self.reports)
        self.reports.hide()
        export_button = QPushButton("Экспорт CSV и дополнительные фильтры…")
        export_button.clicked.connect(self.show_reports)
        self.history.layout().addWidget(export_button)  # type: ignore[union-attr]
        self.tabs.addTab(settings, "Настройки")
        self.commands = Commands(self)
        layout.addWidget(self.tabs, 1)
        self.device_status = plain_label("Считыватель: не подключён", wrap=True)
        self.device_status.setObjectName("Muted")
        layout.addWidget(self.device_status)
        self.setCentralWidget(central)
        self.public_window = PublicWindow()
        self.logs_window = LogsWindow(service, files, self.students.bind_uid)
        self.logs_window.setStyleSheet(STYLE)
        self.embedded_log = LogsWindow(service, files, self.students.bind_uid)
        self.embedded_log.setParent(self.workspace)
        self.embedded_log.setWindowFlags(Qt.WindowType.Widget)
        self.embedded_log.setMaximumHeight(235)
        workspace_layout = self.workspace.layout()
        assert workspace_layout is not None
        workspace_layout.addWidget(self.embedded_log)
        self.embedded_log.hide()
        self.embedded_log_button = QPushButton("Журнал событий ▸")
        self.embedded_log_button.setCheckable(True)
        self.embedded_log_button.toggled.connect(self.embedded_log.setVisible)
        self.embedded_log_button.toggled.connect(
            lambda visible: self._refresh_context() if visible else None
        )
        workspace_layout.insertWidget(
            workspace_layout.count() - 1, self.embedded_log_button
        ) if isinstance(workspace_layout, QVBoxLayout) else None
        self.event_timer = QTimer(self)
        self.event_timer.setInterval(30)
        self.event_timer.timeout.connect(self.process_events)
        self.event_timer.start()
        self.refresh()
        self.refresh_readers()

    def _settings(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(18, 18, 18, 18)
        reader_group = QGroupBox("Считыватель")
        reader_layout = QVBoxLayout(reader_group)
        self.readers = QComboBox()
        self.readers.setMinimumWidth(380)
        reader_layout.addWidget(self.readers)
        reader_layout.addLayout(
            actions(
                ("Обновить список", self.refresh_readers),
                ("Подключить / восстановить связь", self.connect_reader),
            )
        )
        reader_layout.addWidget(
            plain_label(
                "Выберите интерфейс явно. Для исходного ACS события наблюдались на окончании имени «00 01»; имя зависит от устройства.",
                wrap=True,
            )
        )
        if self.demo:
            demo_line = QHBoxLayout()
            self.demo_uid = QLineEdit()
            self.demo_uid.setPlaceholderText("Тестовый UID, например 00 11 22 33 (только приватно)")
            self.demo_uid.setObjectName("PrivateDemoUID")
            demo_line.addWidget(self.demo_uid, 1)
            button = QPushButton("Имитировать предъявление")
            button.clicked.connect(self.simulate)
            self.demo_uid.returnPressed.connect(self.simulate)
            demo_line.addWidget(button)
            reader_layout.addLayout(demo_line)
        layout.addWidget(reader_group)
        public_group = QGroupBox("Публичное окно")
        public_layout = QFormLayout(public_group)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 60)
        self.timeout.setValue(3)
        self.timeout.setSuffix(" с")
        self.timeout.valueChanged.connect(
            lambda value: self.public_window.set_timeout_seconds(value)
        )
        public_layout.addRow("Скрывать результат через:", self.timeout)
        self.screens = QComboBox()
        for index, screen in enumerate(QApplication.screens()):
            self.screens.addItem(
                f"{index + 1}. {screen.name()} · {screen.size().width()} × {screen.size().height()}",
                index,
            )
        public_layout.addRow("Монитор:", self.screens)
        public_layout.addRow(
            actions(
                ("Переместить на монитор", self.move_public),
                ("Полный экран / окно", self.toggle_fullscreen),
            )
        )
        public_layout.addRow(
            plain_label(
                "Показывайте студентам только публичное окно. ФИО скрывается после таймера и при остановке регистрации. Esc выходит из полного экрана.",
                wrap=True,
            )
        )
        layout.addWidget(public_group)
        database_group = QGroupBox("Данные и резервная копия")
        database_layout = QVBoxLayout(database_group)
        database_layout.addWidget(plain_label(f"База: {self.database_path}", wrap=True))
        database_layout.addWidget(
            plain_label(
                "Восстановление выполняется при закрытой регистрации. Перед заменой текущих данных приложение создаёт страховочную копию.",
                wrap=True,
            )
        )
        database_layout.addLayout(
            actions(
                ("Создать резервную копию…", self.backup), ("Восстановить из копии…", self.restore)
            )
        )
        layout.addWidget(database_group)
        layout.addStretch()
        widget.setMinimumHeight(700)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)
        return outer

    def _active_label(self) -> str:
        lesson_id = self.controller.active_lesson_id
        if lesson_id is None:
            return ""
        lesson = self.service.get_lesson(lesson_id)
        names = {d.id: d.name for d in self.service.list_disciplines()}
        return lesson_label(lesson, names.get(lesson.discipline_id, ""))

    def _refresh_context(self) -> None:
        active_label = self._active_label()
        self.active_banner.setText(
            "Активная регистрация: "
            + (active_label or "закрыта — выберите занятие и откройте регистрацию")
        )
        if self.controller.active_session_id is not None:
            session = self.service.redesign.get_session(self.controller.active_session_id)
            block = self.service.redesign.get_block(session.block_id)
            self.active_banner.setText(
                f"● Регистрация открыта · сеанс #{session.id} · блок #{block.id} · пары {session.target_pair_ids} · {active_label} · устройство: {self.reader_status}"
            )
        self.stop_button.setEnabled(self.controller.active_lesson_id is not None)
        self.public_window.set_lesson(
            active_label, registration_open=self.controller.active_lesson_id is not None
        )
        lesson = (
            self.service.get_lesson(self.log_lesson_id) if self.log_lesson_id is not None else None
        )
        names = {d.id: d.name for d in self.service.list_disciplines()}
        log_label = lesson_label(lesson, names.get(lesson.discipline_id, "")) if lesson else ""
        self.logs_window.set_context(lesson, log_label, active_label, self.reader_status)
        self.embedded_log.set_context(lesson, log_label, active_label, self.reader_status)

    def refresh(self) -> None:
        self.schedule.refresh()
        self.students.refresh()
        self.reports.refresh()
        self.workspace.refresh()
        self.preparation.refresh()
        self.history.refresh()
        self._refresh_context()

    def registration_changed(self) -> None:
        if self.controller.active_lesson_id is not None:
            self.log_lesson_id = self.controller.active_lesson_id
        self.process_events()
        self._refresh_context()
        self.workspace.update_counts()
        self.history.update_evidence()

    def refresh_evidence(self) -> None:
        self.workspace.update_counts()
        self.history.update_evidence()
        self.schedule.update_counts()

    def card_binding_changed(self, uid: str) -> None:
        self.students.refresh()
        self.workspace.card_binding_changed(uid)
        self.refresh_evidence()
        self.reports.refresh()
        self._refresh_context()
        self.public_window.show_idle()

    def process_events(self) -> None:
        if self._closing:
            return
        try:
            events = self.controller.drain()
            self.workspace.sync_registration_context()
            if self.controller.active_session_id is not None:
                self._session_ui_active = True
            if any(event.kind == "changed" and event.scan is None for event in events):
                if not self._session_ui_active:
                    self.refresh()
                else:
                    self._refresh_context()
            for event in events:
                if event.kind == "changed" and event.scan is not None and event.result is not None:
                    if event.scan.session_id is None or self.logs_window.isVisible():
                        self.logs_window.show_scan(
                            event.scan.lesson_id, event.scan.uid, event.result
                        )
                    if self.embedded_log.isVisible():
                        self.embedded_log.show_scan(
                            event.scan.lesson_id, event.scan.uid, event.result
                        )
                    if event.scan.session_id is not None:
                        session = self.service.redesign.get_session(event.scan.session_id)
                        self.history.update_evidence(set(session.target_pair_ids))
                    else:
                        self.history.update_evidence({event.scan.lesson_id})
                    self.workspace.scan(event.scan, event.result)
                    self.schedule.update_counts(event.scan.lesson_id)
                elif event.kind == "failed" and event.lesson_id is not None:
                    if self.workspace.is_active_context(event.lesson_id, event.session_id):
                        self.workspace.scan_failed()
                elif event.kind == "public" and event.result is not None:
                    result = event.result
                    if result.at is not None and self.controller.active_lesson_id is not None:
                        lesson = self.service.get_lesson(self.controller.active_lesson_id)
                        result = replace(result, at=result.at.astimezone(ZoneInfo(lesson.timezone)))
                    self.public_window.display(result)
                elif event.kind == "status":
                    self.reader_status = event.text
                    self.device_status.setText("Считыватель: " + event.text)
                    self.logs_window.reader_state.setText("Считыватель: " + event.text)
        except Exception as exc:
            # Never open a technical dialog over the public display and never leak details.
            self.reader_status = f"Ошибка обновления: {exc}"
            self.device_status.setText(self.reader_status)
            self.workspace.display_update_error()
            self.public_window.display_update_error()

    def refresh_readers(self) -> None:
        selected = self.readers.currentData()
        names = self.controller.available_readers()
        self.readers.clear()
        self.readers.addItem("Выберите интерфейс считывателя…", None)
        for name in names:
            self.readers.addItem(name, name)
        index = self.readers.findData(selected)
        self.readers.setCurrentIndex(max(index, 0))
        if not names:
            self.reader_status = "Считыватели не найдены; подключите устройство и обновите список"
            if hasattr(self, "device_status"):
                self.device_status.setText(self.reader_status)

    def connect_reader(self) -> None:
        name = self.readers.currentData()
        if name is None:
            private_message(
                self,
                "Выберите интерфейс",
                "Укажите нужный PC/SC-интерфейс по имени, затем подключитесь.",
            )
            return

        def connect() -> None:
            self.controller.connect_reader(str(name))
            self.reader_status = "Подключение: " + str(name)
            self.device_status.setText(self.reader_status)
            self.process_events()

        guarded(self, connect)

    def open_registration(self, lesson_id: int) -> None:
        self.workspace.select_lesson(lesson_id)
        self.tabs.setCurrentWidget(self.workspace)
        if self.workspace.viewed_block_id is not None:
            self.workspace.open_selected()
            return

        def start() -> None:
            lesson = self.service.get_lesson(lesson_id)
            if self.controller.active_lesson_id not in (None, lesson_id):
                if not private_message(
                    self,
                    "Переключить активное занятие?",
                    "Регистрация на текущем занятии будет завершена. Сохранённые отметки останутся в его истории.\n\nОткрыть регистрацию на выбранном занятии?",
                    question=True,
                ):
                    return
            now = datetime.now(UTC)
            if now < lesson.starts_at or now > lesson.ends_at:
                if not private_message(
                    self,
                    "Время вне расписания",
                    "Сейчас время вне интервала выбранного занятия. Будет записываться фактическое время предъявления.\n\nОткрыть регистрацию?",
                    question=True,
                ):
                    return
            self._session_ui_active = False
            self.controller.open_lesson(lesson_id)
            self.log_lesson_id = lesson_id
            self.process_events()
            self.refresh()

        guarded(self, start)

    def close_registration(self) -> None:
        def close() -> None:
            self.controller.close_lesson()
            self.registration_changed()

        guarded(self, close)

    def simulate(self) -> None:
        if not self.demo:
            return

        def present() -> None:
            self.controller.simulate(self.demo_uid.text())
            self.process_events()

        guarded(self, present)

    def show_reports(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Экспорт посещаемости CSV")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self.reports)
        self.reports.show()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self.reports.refresh()
        dialog.exec()
        self.reports.hide()
        self.reports.setParent(self)

    def show_public(self) -> None:
        self._refresh_context()
        self.public_window.show()
        self.public_window.raise_()

    def show_active_log(self) -> None:
        lesson_id = self.controller.active_lesson_id
        if lesson_id is None:
            lesson_id = self.schedule.selected_lesson_id()
        if lesson_id is not None:
            self.show_log(lesson_id)
        else:
            private_message(
                self, "Журнал занятия", "Сначала выберите занятие или откройте регистрацию."
            )

    def show_log(self, lesson_id: int) -> None:
        self.log_lesson_id = lesson_id
        self._refresh_context()
        self.logs_window.show()
        self.logs_window.raise_()

    def move_public(self) -> None:
        screens = QApplication.screens()
        index = self.screens.currentData()
        if index is not None and 0 <= index < len(screens):
            self.public_window.move_to_screen(screens[index])

    def toggle_fullscreen(self) -> None:
        if self.public_window.isFullScreen():
            self.public_window.showNormal()
        else:
            self.move_public()
            self.public_window.showFullScreen()

    def backup(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "Резервная копия базы", "attendance-backup.sqlite3", "SQLite (*.sqlite3 *.db)"
        )
        if filename:

            def save() -> None:
                self.process_events()
                self.service.backup(Path(filename))
                private_message(self, "Копия создана", "Резервная копия сохранена.")

            guarded(self, save)

    def restore(self) -> None:
        if self.controller.active_lesson_id is not None:
            private_message(
                self,
                "Сначала завершите регистрацию",
                "Перед восстановлением закройте активную регистрацию отдельной кнопкой.",
            )
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Восстановление базы", "", "SQLite (*.sqlite3 *.db)"
        )
        if not filename:
            return
        if not private_message(
            self,
            "Восстановить базу?",
            "Текущие данные будут заменены содержимым выбранной копии. Перед заменой будет сохранена страховочная копия текущей базы.\n\nПродолжить?",
            question=True,
        ):
            return

        def load() -> None:
            self.service.restore(Path(filename))
            self.log_lesson_id = None
            self.refresh()
            private_message(
                self,
                "База восстановлена",
                "Данные восстановлены. Страховочная копия создана рядом с текущей базой (*.before-restore.*.sqlite3).",
            )

        guarded(self, load)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        self.event_timer.stop()
        try:
            self.controller.shutdown()
        except Exception as exc:
            private_message(self, "Не удалось завершить работу", str(exc))
            self.event_timer.start()
            event.ignore()
            return
        self._closing = True
        self.public_window.close()
        self.logs_window.close()
        self.embedded_log.close()
        event.accept()
