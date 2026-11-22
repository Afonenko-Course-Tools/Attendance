"""Reader, archive and file maintenance scenarios for the Quick shell."""

from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from attendance_lab.application.quick_common import plain
from attendance_lab.domain.models import AttendanceError

MUTATING_COMMANDS = frozenset(
    {"system.save_legacy", "system.set_legacy_roster", "system.discipline"}
)


def local_path(value) -> Path:
    text = str(value)
    parsed = urlparse(text)
    if (parsed.scheme and parsed.scheme != "file") or parsed.netloc not in ("", "localhost"):
        raise AttendanceError("Выберите локальный файл.")
    if not text.strip() or (parsed.scheme == "file" and not parsed.path):
        raise AttendanceError("Выберите файл.")
    return Path(unquote(parsed.path) if parsed.scheme else text).expanduser()


class SystemActions:
    def __init__(self, service, controller, files, *, demo=False):
        self.service, self.controller, self.files = service, controller, files
        self.demo = demo
        self.readers = []
        self.status = "Считыватель не выбран"
        self.archive_lesson_id = None
        self.recovered = 0

    def snapshot(self) -> dict:
        service = self.service
        legacy = service.list_source_lessons()
        selected = next((item for item in legacy if item.id == self.archive_lesson_id), None)
        return {
            "readers": self.readers,
            "reader_status": self.status,
            "demo": self.demo,
            "disciplines": plain(service.list_disciplines()),
            "archive": plain(legacy),
            "archive_lesson_id": self.archive_lesson_id,
            "archive_log": plain(service.lesson_log(selected.id)) if selected else [],
            "archive_roster": sorted(service.get_lesson_roster(selected.id)) if selected else [],
            "archive_students": plain(service.list_students()),
            "recovered": self.recovered,
        }

    def execute(self, name: str, payload: dict) -> dict:
        service = self.service
        if name == "system.readers":
            self.readers = self.controller.available_readers()
        elif name == "system.connect_reader":
            reader = str(payload["name"])
            self.controller.connect_reader(reader)
            self.status = f"Подключён: {reader}"
        elif name == "system.simulate":
            self.controller.simulate(str(payload["uid"]))
        elif name == "system.backup":
            path = local_path(payload["path"])
            service.backup(path)
            return {"saved": str(path)}
        elif name == "system.restore":
            if getattr(self.controller, "pending_count", 0):
                raise AttendanceError("Сначала обработайте принятые предъявления.")
            if self.controller.active_lesson_id is not None:
                raise AttendanceError("Сначала закройте регистрацию.")
            service.restore(local_path(payload["path"]))
            self.archive_lesson_id = None
            return {"restored": True}
        elif name == "system.archive":
            lesson_id = int(payload["lesson_id"])
            self._require_legacy(lesson_id)
            self.archive_lesson_id = lesson_id
        elif name == "system.discipline":
            item = service.save_discipline(
                str(payload["name"]), str(payload.get("description", ""))
            )
            return {"discipline_id": item.id}
        elif name == "system.save_legacy":
            if payload.get("lesson_id") is not None:
                self._require_legacy(int(payload["lesson_id"]))
            item = service.save_lesson(
                int(payload["discipline_id"]),
                datetime.fromisoformat(payload["starts_at"]),
                datetime.fromisoformat(payload["ends_at"]),
                str(payload["timezone"]),
                str(payload.get("title", "")),
                str(payload.get("room", "")),
                int(payload["lesson_id"]) if payload.get("lesson_id") else None,
            )
            return {"lesson_id": item.id}
        elif name == "system.set_legacy_roster":
            self._require_legacy(int(payload["lesson_id"]))
            service.set_lesson_roster(int(payload["lesson_id"]), set(payload["student_ids"]))
        elif name == "system.export_log":
            lesson_id = int(payload["lesson_id"])
            self.files.export_log(
                local_path(payload["path"]),
                service.get_lesson(lesson_id),
                service.lesson_log(lesson_id),
                delimiter=payload.get("delimiter", ";"),
                bom=payload.get("bom", True),
            )
        elif name == "system.export_csv":
            report = service.report(
                int(payload["discipline_id"]),
                date_from=date.fromisoformat(payload["date_from"])
                if payload.get("date_from")
                else None,
                date_to=date.fromisoformat(payload["date_to"]) if payload.get("date_to") else None,
                group=str(payload.get("group", "")),
                student_id=payload.get("student_id"),
            )
            self.files.export_report(
                local_path(payload["path"]),
                report,
                kind=payload.get("kind", "matrix"),
                delimiter=payload.get("delimiter", ";"),
                bom=payload.get("bom", True),
            )
        elif name == "system.export_students":
            self.files.write_csv(
                local_path(payload["path"]),
                ("ID", "ФИО", "Группа"),
                [(s.id, s.full_name, s.group) for s in service.list_students()],
            )
        else:
            raise AttendanceError("Неизвестная служебная команда.")
        return {}

    def _require_legacy(self, lesson_id: int) -> None:
        if lesson_id not in {lesson.id for lesson in self.service.list_source_lessons()}:
            raise AttendanceError("Выбранное занятие не относится к архиву вне курсов.")
