"""Student directory, reviewed import, catalogues and card ownership actions."""

from pathlib import Path
from uuid import uuid4

from attendance_lab.application.quick_academic_common import (
    SearchIndex,
    _digest,
    integer,
    json_safe,
    local_path,
    optional_id,
)
from attendance_lab.domain.models import AttendanceError
from attendance_lab.domain.rules import normalize_uid

MUTATING_COMMANDS = frozenset(
    [
        "students.apply_import",
        "students.bind",
        "students.create",
        "students.profile_alias",
        "students.rename_profile",
        "students.rename_specialty",
        "students.save_context",
        "students.save_group",
        "students.save_year",
        "students.specialty_alias",
        "students.unbind",
    ]
)


class StudentActions:
    def __init__(self, service, files=None):
        self.service, self.files = service, files
        self.student_filter = {"query": "", "study_course": None, "academic_year_id": None}
        self._import_plans = {}
        self._import_preview = None
        self._student_history = None
        self.search = SearchIndex()

    def snapshot(self, catalogs=None):
        catalogs = self.catalogs() if catalogs is None else catalogs
        contexts = self.service.redesign.list_student_contexts(
            self.student_filter["academic_year_id"]
        )
        cards = self.service.list_cards()
        revisions = {}
        for change in self.service.repository.list_binding_changes():
            revisions.setdefault(change.uid, change.id)
        card_rows = [
            dict(json_safe(card), binding_revision=revisions.get(card.uid, 0)) for card in cards
        ]
        groups = {group["id"]: group for group in catalogs["groups"]}
        rows = []
        for student in contexts:
            own_cards = [card for card in card_rows if card["student_id"] == student.student_id]
            rows.append(
                dict(
                    json_safe(student),
                    id=student.student_id,
                    name=student.full_name,
                    cards=own_cards,
                    card_uids=[card["uid"] for card in own_cards],
                    card_count=len(own_cards),
                    profile_id=next(
                        (
                            profile["id"]
                            for profile in catalogs["profiles"]
                            if profile["name"] == student.profile
                            and profile["specialty_id"]
                            == groups.get(student.group_id, {}).get("specialty_id")
                        ),
                        None,
                    ),
                )
            )
        rows = self.search.find(rows, self.student_filter["query"])
        if self.student_filter["study_course"] is not None:
            rows = [row for row in rows if row["course"] == self.student_filter["study_course"]]
        return {
            "students": rows,
            "student_filter": dict(self.student_filter),
            "cards": card_rows,
            "import_preview": self._import_preview,
            "student_history": self._student_history,
        }

    def execute(self, name, payload):
        handlers = {
            "students.preview_import": "_preview_import",
            "students.apply_import": "_apply_import",
            "students.cancel_import": "_cancel_import",
            "students.save_context": "_save_context",
            "students.save_year": "_save_year",
            "students.save_group": "_save_group",
            "students.rename_specialty": "_rename_specialty",
            "students.rename_profile": "_rename_profile",
            "students.specialty_alias": "_specialty_alias",
            "students.profile_alias": "_profile_alias",
            "students.unbind": "_unbind",
            "students.bind": "_bind",
            "students.history": "_card_history",
            "students.export": "_export_students",
            "students.filter": "_filter_students",
            "students.create": "_create_student",
        }
        if name not in handlers:
            raise AttendanceError("Неизвестная команда раздела.")
        return json_safe(getattr(self, handlers[name])(payload) or {})

    def catalogs(self):
        api = self.service.redesign
        years = api.list_years()
        year_names = {year.id: f"{year.start_year}/{year.start_year + 1}" for year in years}
        specialties = api.list_specialties()
        specialty_names = {item.id: item.name for item in specialties}
        return {
            "years": [
                dict(json_safe(year), name=f"{year.start_year}/{year.start_year + 1}")
                for year in years
            ],
            "specialties": json_safe(specialties),
            "profiles": [
                dict(
                    json_safe(profile),
                    label=f"{profile.name} · {specialty_names[profile.specialty_id]}",
                )
                for profile in api.list_profiles()
            ],
            "groups": [
                dict(
                    json_safe(group),
                    name=f"{year_names[group.academic_year_id]} · {group.specialty_name} · {group.course} курс · группа {group.number}"
                    + (f" · {group.variant_key}" if group.variant_key else ""),
                )
                for group in api.list_groups()
            ],
            "disciplines": json_safe(self.service.list_disciplines()),
        }

    def _filter_students(self, payload):
        self.student_filter = {
            "query": str(payload.get("query", self.student_filter["query"])),
            "study_course": optional_id(payload, "study_course")
            if "study_course" in payload
            else self.student_filter["study_course"],
            "academic_year_id": optional_id(payload, "academic_year_id")
            if "academic_year_id" in payload
            else self.student_filter["academic_year_id"],
        }
        return {}

    def _preview_import(self, payload):
        previous = self._import_plans.get(str(payload.get("token", "")))
        if payload.get("path"):
            if self.files is None:
                raise AttendanceError("Файловый адаптер не подключён.")
            source = {
                "path": str(local_path(payload["path"])),
                "encoding": str(payload.get("encoding", "utf-8-sig")),
                "delimiter": str(payload.get("delimiter", ";")),
            }
            headers, rows = self.files.read_csv(
                Path(source["path"]), source["encoding"], source["delimiter"]
            )
        elif previous:
            headers, rows, source = previous[1:]
        else:
            source = None
            headers, rows = payload.get("headers", []), payload.get("rows", [])
        if not headers or not rows:
            raise AttendanceError("CSV не содержит заголовков или строк студентов.")
        aliases = {
            str(item["name"]): integer(item, "id") for item in payload.get("specialty_aliases", [])
        }
        profiles = {
            (integer(item, "specialty_id"), str(item["name"])): integer(item, "profile_id")
            for item in payload.get("profile_aliases", [])
        }
        groups = {
            integer(item, "source_row"): integer(item, "group_id")
            for item in payload.get("group_choices", [])
        }
        year = integer(
            payload, "academic_year_start", previous[0].academic_year_start if previous else None
        )
        plan = self.service.redesign.preview_import(
            headers,
            rows,
            year,
            specialty_aliases=aliases,
            profile_aliases=profiles,
            group_choices=groups,
            file_hash=_digest(headers, rows),
        )
        token = str(uuid4())
        if len(self._import_plans) >= 16:
            self._import_plans.pop(next(iter(self._import_plans)))
        self._import_plans[token] = (plan, headers, rows, source)
        self._import_preview = dict(
            json_safe(plan),
            token=token,
            can_apply=not plan.errors,
            source=source,
            view_revision=integer(payload, "view_revision", 0),
        )
        return {
            "token": token,
            "can_apply": not plan.errors,
            "view_revision": integer(payload, "view_revision", 0),
        }

    def _apply_import(self, payload):
        token = str(payload.get("token", ""))
        saved = self._import_plans.get(token)
        if saved is None:
            raise AttendanceError("Предпросмотр устарел. Прочитайте CSV повторно.")
        plan, headers, rows, source = saved
        if source:
            if self.files is None:
                raise AttendanceError("Файловый адаптер не подключён.")
            headers, rows = self.files.read_csv(
                Path(source["path"]), source["encoding"], source["delimiter"]
            )
            if _digest(headers, rows) != plan.file_hash:
                raise AttendanceError(
                    "CSV изменился после предпросмотра. Прочитайте файл повторно."
                )
        self.service.redesign.apply_import(plan)
        self._import_preview = None
        return {"imported": len(plan.rows)}

    def _cancel_import(self, payload):
        self._import_plans.pop(str(payload.get("token", "")), None)
        self._import_preview = None
        return {}

    def _save_context(self, payload):
        result = self.service.redesign.save_student_context(
            str(payload.get("student_id", "")),
            str(payload.get("full_name", "")),
            str(payload.get("record_book_number", "")),
            optional_id(payload, "academic_year_id"),
            optional_id(payload, "group_id"),
            optional_id(payload, "profile_id"),
        )
        return {"student_id": result.student_id}

    def _create_student(self, payload):
        with self.service.repository.transaction():
            student = self.service.create_student(str(payload.get("full_name", "")))
            self._save_context(dict(payload, student_id=student.id))
            return {"student_id": student.id}

    def _save_year(self, payload):
        return {
            "academic_year_id": self.service.redesign.save_year(integer(payload, "start_year")).id
        }

    def _save_group(self, payload):
        result = self.service.redesign.save_group(
            integer(payload, "academic_year_id"),
            integer(payload, "specialty_id"),
            integer(payload, "study_course"),
            integer(payload, "number"),
            str(payload.get("variant_key", "")),
            optional_id(payload, "group_id"),
        )
        return {"group_id": result.id}

    def _rename_specialty(self, payload):
        return {
            "specialty_id": self.service.redesign.rename_specialty(
                integer(payload, "specialty_id"), str(payload.get("name", ""))
            ).id
        }

    def _rename_profile(self, payload):
        return {
            "profile_id": self.service.redesign.rename_profile(
                integer(payload, "profile_id"), str(payload.get("name", ""))
            ).id
        }

    def _specialty_alias(self, payload):
        self.service.redesign.add_specialty_alias(
            integer(payload, "specialty_id"), str(payload.get("name", ""))
        )
        return {}

    def _profile_alias(self, payload):
        self.service.redesign.add_profile_alias(
            integer(payload, "profile_id"), str(payload.get("name", ""))
        )
        return {}

    def _unbind(self, payload):
        sid = str(payload.get("student_id", ""))
        with self.service.repository.transaction():
            if "uid" in payload:
                uid = normalize_uid(str(payload["uid"]))
                self._check_card_revision(uid, payload, sid)
                self.service.unbind_card(uid, expected_student_id=sid)
            else:
                expected = payload.get("expected_cards", [])
                expected_uids = {normalize_uid(str(card["uid"])) for card in expected}
                current_uids = {
                    card.uid for card in self.service.list_cards() if card.student_id == sid
                }
                if current_uids != expected_uids or len(expected_uids) != len(expected):
                    raise AttendanceError("Набор карт студента изменился. Обновите список карт.")
                for card in expected:
                    self._check_card_revision(normalize_uid(str(card["uid"])), card, sid)
                self.service.unbind_student_cards(sid, expected_uids=expected_uids)
        return {"student_id": sid}

    def _check_card_revision(self, uid, payload, expected_owner):
        current = self.service.repository.get_card(uid)
        if (current.student_id if current else None) != expected_owner:
            raise AttendanceError("Привязка карты изменилась. Выберите карту повторно.")
        history = self.service.repository.list_binding_changes(uid)
        revision = history[0].id if history else 0
        if integer(payload, "binding_revision", 0) != revision:
            raise AttendanceError("История привязки изменилась. Выберите карту повторно.")

    def _bind(self, payload):
        uid = normalize_uid(str(payload.get("uid", "")))
        with self.service.repository.transaction():
            self._check_card_revision(uid, payload, payload.get("expected_student_id"))
            self.service.bind_card(
                uid,
                str(payload.get("student_id", "")),
                correction=bool(payload.get("correction", False)),
                expected_student_id=payload.get("expected_student_id"),
            )
        return {}

    def _card_history(self, payload):
        sid = str(payload.get("student_id", ""))
        self.service._student(sid)
        uids = {card.uid for card in self.service.list_cards() if card.student_id == sid}
        changes = [
            json_safe(change)
            for change in self.service.repository.list_binding_changes()
            if change.student_id == sid or change.previous_student_id == sid
        ]
        uids.update(change["uid"] for change in changes)
        events = []
        for lesson in self.service.list_source_lessons():
            for row in self.service.lesson_log(lesson.id):
                if row.student_id == sid or row.uid in uids:
                    events.append(
                        dict(json_safe(row), lesson_id=lesson.id, lesson_name=lesson.title)
                    )
        self._student_history = {
            "student_id": sid,
            "events": events,
            "bindings": changes,
        }
        return self._student_history

    def _export_students(self, payload):
        if self.files is None:
            raise AttendanceError("Файловый адаптер не подключён.")
        students = self.snapshot()["students"]
        headers = ["ФИО", "№ зачётки", "Группа", "Курс", "Специальность", "Профилизация"]
        self.files.write_csv(
            local_path(payload.get("path", "")),
            headers,
            [
                [
                    row.get(key, "")
                    for key in (
                        "full_name",
                        "record_book_number",
                        "group_number",
                        "course",
                        "specialty",
                        "profile",
                    )
                ]
                for row in students
            ],
        )
        return {}
