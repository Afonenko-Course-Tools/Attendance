# Attendance redesign shared API contract

Source: `../specs/2026-10-19-attendance-redesign.md`. Frozen coordination contract, 2026-10-19. The backend and Qt workers may proceed concurrently against this document. Changes to public names or types require a coordinator message and updating both consumers before integration.

## Ownership and compatibility ledger

| Owner | Exclusive write area | Consumes / produces |
| --- | --- | --- |
| Backend medium worker | `domain/redesign.py`, `application/redesign.py`, `application/redesign_ports.py`, `application/service.py`, `application/ports.py`, `application/files.py`, `infrastructure/redesign_sqlite.py`, `infrastructure/sqlite.py`, `infrastructure/csv_io.py`, backend tests | Produces every DTO and service API below; existing service, CSV, repository and constructor APIs remain supported |
| UI medium worker | `presentation/*`, `application/controller.py`, GUI/controller tests | Consumes `AttendanceService.redesign`; produces four-section private window, session capture and model/view interaction; no SQL or backend file edits |
| Root integrator | `bootstrap.py`, project/README/validation docs, integration tests, private migration tooling, PR | Owns conflicts, complete acceptance, real local migration, staging and publication |
| High coordinator | Plan/contract docs; review comments | Reviews storage/UI semantics and contract compatibility, does not edit worker product files |

Tasks 1 and 2 of the implementation plan share one backend owner and execute sequentially. Task 3 is independent against this frozen contract. Shared-file changes go to the owning worker by message. No worker silently edits another owner's file. Review findings are resolved by the owner; root may make explicit integration fixes after workers finish.

Legacy `Student`, `Lesson`, `AttendanceReport`, `CapturedScan` positional construction remains valid. Existing `AttendanceService` and `AttendanceController.open_lesson/close_lesson` retain their semantics. Keep `MainWindow.schedule`, `.students`, `.reports`, `.logs_window`, `.public_window` compatibility attributes. New methods are additive. Do not mirror new session evidence into legacy `scans`.

## DTOs: `attendance_lab.domain.redesign`

All DTOs are frozen, slotted dataclasses; collections are tuples, public datetimes are aware UTC. ID types remain `int` for database-generated entities and `str` for students. Import `date` and `datetime` from `datetime`; `from __future__ import annotations` avoids the field named `date` shadowing its annotation.

```python
@dataclass(frozen=True, slots=True)
class AcademicYear:
    id: int
    start_year: int


@dataclass(frozen=True, slots=True)
class Specialty:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Profile:
    id: int
    specialty_id: int
    name: str


@dataclass(frozen=True, slots=True)
class AcademicGroup:
    id: int
    academic_year_id: int
    specialty_id: int
    course: int
    number: int
    variant_key: str = ""
    specialty_name: str = ""


@dataclass(frozen=True, slots=True)
class StudentContext:
    student_id: str
    full_name: str
    record_book_number: str = ""
    academic_year_id: int | None = None
    group_id: int | None = None
    group_number: int | None = None
    course: int | None = None
    specialty: str = ""
    profile: str = ""
    subgroup: str = ""


@dataclass(frozen=True, slots=True)
class ImportRow:
    source_row: int
    ordinal: int
    full_name: str
    specialty: str
    profile: str
    course: int
    group_number: int
    record_book_number: str


@dataclass(frozen=True, slots=True)
class ImportPlan:
    rows: tuple[ImportRow, ...]
    errors: tuple[str, ...]
    changes: tuple[str, ...]
    revision: str
    academic_year_start: int
    file_hash: str
    specialty_aliases: tuple[tuple[str, int], ...] = ()
    profile_aliases: tuple[tuple[int, str, int], ...] = ()
    group_choices: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class TeachingRoster:
    id: int
    discipline_id: int
    academic_year_id: int
    semester: int
    name: str
    kind: str
    duration: int
    mode: str
    group_ids: tuple[int, ...]
    student_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduledBlock:
    id: int
    roster_id: int
    date: date
    start_slot: int
    pair_ids: tuple[int, ...]
    title: str = ""
    room: str = ""
    timezone: str = "Europe/Minsk"


@dataclass(frozen=True, slots=True)
class RegistrationSession:
    id: int
    block_id: int
    target_pair_ids: tuple[int, ...]
    opened_at: datetime
    closed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ManualBatch:
    id: int
    targets: tuple[tuple[str, int], ...]
    reason: str
    created_at: datetime
    revoked_at: datetime | None = None
    session_id: int | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str
    pair_id: int
    student_id: str | None
    at: datetime
    uid: str | None = None
    session_id: int | None = None
    batch_id: int | None = None
    active: bool = True
```

`kind`: roster `lecture`, `lab`, `seminar`, `other`; evidence `legacy_card`, `card`, `manual`. Roster `mode`: `groups` or `students`. `duration`: 1 or 2; a lab uses 2. `semester`: 1 or 2. `subgroup` is a display label derived from the discipline's student-mode roster(s) for the selected semester; never a global student field. `StudentContext.record_book_number == ''` denotes unresolved legacy data only; imports require nonempty ASCII digits. Empty subgroup displays as `—`.

`ImportPlan.specialty_aliases` stores `(input_name, specialty_id)`; profile aliases store `(specialty_id, input_name, profile_id)`; group choices store `(source_row, group_id)`. `changes` is readable preview text describing new catalogs/groups/students, renames, membership changes and unchanged counts. The opaque `revision` fingerprints relevant catalog state; apply rechecks both revision and the entire resolved plan inside one transaction. `file_hash` is SHA-256 of source bytes when supplied; otherwise hash the canonical parsed content and identify that scope in local batch metadata.

Manual batches with `session_id=None` have immutable exact targets. Quick session marks have `session_id` set; their effective targets follow that session's explicit scope. Revocation never deletes evidence. Fixed batches do not duplicate an already active manual ground for the same student/pair; a card ground does not prevent adding an independent manual ground. Quick marks deduplicate by (session_id, student_id), preserving the session linkage even when another fixed/card ground currently covers the same pair, so later scope expansion remains meaningful. A returned no-op batch may have empty `targets`; its revocation cannot revoke grounds owned by older batches.

## `AttendanceService.redesign`

`AttendanceService.redesign` is a stable property returning one `RedesignService`. Construct it lazily to avoid circular imports. `RedesignService` receives the existing service (for legacy lookups) and uses its repository through `RedesignStore`; it never imports concrete infrastructure or Qt.

```python
@property
def redesign(self) -> RedesignService: ...


class RedesignService:
    def __init__(self, service: AttendanceService) -> None: ...
    def list_years(self) -> list[AcademicYear]: ...
    def save_year(self, start_year: int) -> AcademicYear: ...
    def list_specialties(self) -> list[Specialty]: ...
    def list_profiles(self, specialty_id: int | None = None) -> list[Profile]: ...
    def list_groups(self, academic_year_id: int | None = None) -> list[AcademicGroup]: ...
    def list_student_contexts(
        self,
        academic_year_id: int | None = None,
        discipline_id: int | None = None,
        semester: int | None = None,
    ) -> list[StudentContext]: ...
    def preview_import(
        self,
        headers: list[str],
        rows: list[list[str]],
        academic_year_start: int,
        *,
        specialty_aliases: dict[str, int] | None = None,
        profile_aliases: dict[tuple[int, str], int] | None = None,
        group_choices: dict[int, int] | None = None,
        file_hash: str = "",
    ) -> ImportPlan: ...
    def apply_import(self, plan: ImportPlan) -> None: ...
    def save_student_context(
        self,
        student_id: str,
        full_name: str,
        record_book_number: str,
        academic_year_id: int | None = None,
        group_id: int | None = None,
        profile_id: int | None = None,
    ) -> StudentContext: ...
    def add_specialty_alias(self, specialty_id: int, name: str) -> None: ...
    def add_profile_alias(self, profile_id: int, name: str) -> None: ...
    def rename_specialty(self, specialty_id: int, name: str) -> Specialty: ...
    def rename_profile(self, profile_id: int, name: str) -> Profile: ...
    def save_group(
        self,
        academic_year_id: int,
        specialty_id: int,
        course: int,
        number: int,
        variant_key: str = "",
        group_id: int | None = None,
    ) -> AcademicGroup: ...
    def set_discipline_groups(
        self,
        discipline_id: int,
        academic_year_id: int,
        group_ids: set[int],
    ) -> None: ...
    def get_discipline_groups(self, discipline_id: int, academic_year_id: int) -> set[int]: ...
    def list_rosters(
        self,
        discipline_id: int | None = None,
        academic_year_id: int | None = None,
        semester: int | None = None,
    ) -> list[TeachingRoster]: ...
    def save_roster(
        self,
        discipline_id: int,
        academic_year_id: int,
        semester: int,
        name: str,
        kind: str,
        duration: int,
        mode: str,
        group_ids: set[int],
        student_ids: set[str],
        roster_id: int | None = None,
    ) -> TeachingRoster: ...
    def copy_roster(self, roster_id: int, semester: int) -> TeachingRoster: ...
    def schedule_block(
        self,
        roster_id: int,
        on_date: date,
        start_slot: int,
        *,
        timezone: str = "Europe/Minsk",
        title: str = "",
        room: str = "",
        block_id: int | None = None,
    ) -> ScheduledBlock: ...
    def list_blocks(
        self,
        on_date: date | None = None,
        discipline_id: int | None = None,
    ) -> list[ScheduledBlock]: ...
    def get_block(self, block_id: int) -> ScheduledBlock: ...
    def get_pair_contexts(self, pair_id: int) -> list[StudentContext]: ...
    def set_pair_roster(
        self,
        pair_id: int,
        student_ids: set[str],
        reason: str,
    ) -> None: ...
    def open_session(self, block_id: int, target_pair_ids: set[int]) -> RegistrationSession: ...
    def get_session(self, session_id: int) -> RegistrationSession: ...
    def close_session(self, session_id: int) -> None: ...
    def expand_session(self, session_id: int) -> RegistrationSession: ...
    def record_session_scan(self, session_id: int, uid: str, at: datetime) -> PublicResult: ...
    def session_manual_mark(self, session_id: int, student_id: str, reason: str) -> ManualBatch: ...
    def create_manual_batch(self, targets: set[tuple[str, int]], reason: str) -> ManualBatch: ...
    def revoke_manual_batch(self, batch_id: int) -> None: ...
    def list_manual_batches(self) -> list[ManualBatch]: ...
    def evidence(self, student_id: str, pair_id: int) -> list[Evidence]: ...
    def pair_evidence(self, pair_id: int) -> list[Evidence]: ...
```

The old `AttendanceService.report(...) -> AttendanceReport` projects the union of legacy scans, session evidence and active manual grounds. `AttendanceCell.first_seen` remains the earliest card time when a card exists, otherwise manual confirmation time. Extend `AttendanceCell` only with defaulted metadata if needed to label manual time correctly; do not describe manual confirmation as arrival. `lesson_log/list_cards/lesson_counts` include new session evidence, without duplicating legacy records. `set_pair_roster` is the explicit audited historical adjustment; require a reason, preserve all evidence and record before/after context. Pair contexts return a frozen snapshot after the pair has been fixed; unfixed future pairs resolve the current roster. The report continues to show real outside-roster presence.

Closing/switching sessions preserves their evidence and coverage. Expanding one session changes only that session, including its prior unknown-card evidence and session manual marks. Historical/manual block batches keep fixed targets. `open_session` closes any prior active session transactionally, fixes snapshots, and opens only the explicitly selected targets. `recover_interrupted` closes both legacy open lessons and new active sessions and never resumes automatically. Reopening a block creates a distinct new session; expanding a closed session is permitted as an explicit historical correction and audited.

## Typed storage boundary

`application/redesign_ports.py` defines `RedesignStore(Protocol)` and aggregate persistence records as necessary. Use descriptive, typed aggregate methods (not SQL strings, opaque operations, a generic CRUD dictionary, or public connection access). Its required capabilities are:

- Transaction context, existing student/lesson/card/scans ports, catalog revision read/bump.
- Lists and upserts for years, specialties, profiles and groups; canonical/alias resolution; student book lookup and update; annual membership read/write with audit; import batch/row insertion.
- Discipline-group links; teaching roster upsert/list; block upsert/list; pair links and immutable context snapshots.
- Session insertion/get/update/active lookup; explicit target read/write and scope audit; evidence insertion/listing.
- Manual batch/item insertion/get/list/revocation; grounds queries for report, counts and logs.

The backend owner may choose private record/method names within these capabilities because no UI code consumes them. All names and signatures in the public service block above are frozen. `infrastructure/redesign_sqlite.py` implements the protocol through a focused mixin/adapter; SQL never crosses into application or presentation.

## Controller additions and UI identity

```python
# Append to the existing CapturedScan dataclass; preserve its first 3 fields.
session_id: int | None = None

# AttendanceController fields/methods, additive:
active_session_id: int | None


def open_session(self, block_id: int, target_pair_ids: set[int]) -> None: ...
def close_session(self) -> None: ...
def expand_session(self) -> None: ...
```

Capture both lesson/pair ID and session ID while holding the existing lock. `_flush` routes a session capture to `record_session_scan(captured.session_id, ...)`, never to the currently active session. Flush accepted events before close/switch/expand; publish success only after durable commit. `active_lesson_id` remains the first targeted pair for legacy display compatibility; viewing another block never mutates either active field. Legacy open/close and new open/close flush and close the other mode consistently. `shutdown` closes whichever mode is active.

QAbstractTableModel and QSortFilterProxyModel use stable student/pair IDs as roles. Report cells are addressed by `(student_id, pair_id)`, never source/proxy row numbers. Sorting preserves the exact selected IDs and anchors; filters/period/discipline changes clear selection and update counts. Mouse and keyboard share QActions. Scan updates emit targeted `dataChanged`/queue/count signals and preserve text, focus, scroll and sorting. The UI caches search data and does no SQL/service directory load per keystroke.

Visible commands and keys: F1 commands; F2 session manual mark; F3 screen search; F6/Shift+F6 focus region; F9 open; Shift+F9 close; Ctrl+Enter context save/batch; Esc cancel. All commands have mouse access; private shortcuts are inactive under modal dialogs and when the public window has focus.
