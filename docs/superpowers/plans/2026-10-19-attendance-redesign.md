# Attendance Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implement the approved local Attendance redesign with relational imports, semester rosters, fixed-slot blocks, session-scoped registration, reversible manual attendance and a keyboard/mouse Qt workflow, migrate the user's current local database privately, and create a GitHub PR containing code and synthetic fixtures only.

**Architecture:** Preserve existing domain/application/infrastructure/presentation boundaries and legacy interfaces. Add a typed `RedesignService` behind `AttendanceService.redesign`, a `RedesignStore` Protocol and additive SQLite schema migration; project attendance as the union of independent evidence. UI model/view components consume the frozen API and carry stable student/pair IDs through selection and sorting.

**Tech Stack:** Existing Python >=3.14.7, PySide6, SQLite, pytest, ruff and pyright; no new network services or runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-10-19-attendance-redesign.md`

## Global Constraints

- Local Python/Qt desktop application with SQLite, one private main window and a separate public screen; no new accounts/services or student-data transmission.
- Use only synthetic student data in development, tests, demos and repository. Publish no database, including demonstration databases and backups.
- Preserve immutable `students.id`, card ownership and all old lessons/scans; record-book number is a unique TEXT matching key and leading zeros are significant.
- Import seven columns in the spec's order; explicitly select academic year. UTF-8, Windows-1251 and explicit delimiter selection remain supported; preview before one atomic apply.
- NFC, collapsed Unicode whitespace and casefold resolve exact catalog keys. Do not auto-merge fuzzy names, punctuation, transliteration or `ё/е`.
- Ordinary group `variant_key TEXT NOT NULL DEFAULT ''`; storage enforces membership year, profile specialty, aliases and uniqueness invariants.
- Rosters are discipline/year/semester specific, have one course/year, and use whole groups or selected students from exactly one group; pair snapshots preserve historical academic context.
- Eight local slots: 09:00–10:20, 10:30–11:50, 12:00–13:20, 13:50–15:10, 15:20–16:40, 17:00–18:20, 18:30–19:50, 20:00–21:20; default timezone Europe/Minsk. A two-pair block cannot start at slot 8.
- Session coverage is explicit one/both block pairs; capture session ID at event receipt. Only that session expands; preserve actual scan times and unknown cards.
- Manual historical/block batches persist exact `(student_id, pair_id)` targets; revocation removes only grounds created by that batch. Session quick marks follow explicitly shown session coverage.
- Keyboard and mouse both support all commands; private shortcuts never execute in public focus or under a modal dialog. Incoming scans preserve the operator's draft, focus, selection and scroll.
- Private BSU theme: Arial, white surfaces, #0A3470, #144E9D, #3076AF, #7BB8BF, #D9CBA0; public window keeps strict allowlisted data/messages.
- Target search p95 <=50 ms on 300 synthetic students and saved-check display <=150 ms after UID receipt; measure before claiming.
- User has explicitly approved implementation, medium code/test/visual workers, high coordination/review, current local database migration and PR creation. Do not add another approval pause for these authorized steps.

## Review Focus

- A stale import preview after a concurrent catalog edit must abort atomically instead of silently applying a different mapping (Task 1).
- A version-1 backup must remain restorable after upgrading, without mutating the original backup or mixing real/demo mode (Tasks 1 and 4).
- One old unknown scan that is bound after session expansion must affect exactly that session's targets, including when neighboring sessions have different coverage (Task 2).
- Sorting numerically identical group labels across specialties and filtering a selected student must never change or hide the recipients of a bulk action (Task 3).
- A reader callback arriving during modal manual correction/switching must retain captured session identity and must not steal focus or publish a false success on commit failure (Tasks 3 and 4).

---

## Execution and file ownership

The frozen DTOs, exact public signatures and disjoint ownership ledger are in `docs/superpowers/plans/attendance-api-contract.md`; every worker reads it before implementation. One backend worker executes Tasks 1 then 2; one UI worker executes Task 3 concurrently against the contract. Root handles Task 4 and integration. The high coordinator reviews both workers. Existing API/test behavior is a compatibility requirement, not a substitute for new acceptance coverage. No broad refactor or dependency upgrade is part of this work.

### Task 1: Relational catalog, atomic import and safe schema migration

**Files:**
- Create: `src/attendance_lab/domain/redesign.py`, `src/attendance_lab/application/redesign.py`, `src/attendance_lab/application/redesign_ports.py`, `src/attendance_lab/infrastructure/redesign_sqlite.py`.
- Modify: `src/attendance_lab/application/service.py`, `src/attendance_lab/application/ports.py`, `src/attendance_lab/application/files.py`, `src/attendance_lab/infrastructure/sqlite.py`, `src/attendance_lab/infrastructure/csv_io.py`.
- Test: `tests/test_redesign_catalog.py`, `tests/test_redesign_migration.py`.

**Interfaces:**
- Consumes existing `AttendanceRepository.transaction`, student/card/lesson/scan ports; existing CSV transport remains unchanged.
- Produces all catalog/import DTOs and catalog methods of `AttendanceService.redesign` in the shared contract, including `preview_import(headers, rows, academic_year_start, *, specialty_aliases=None, profile_aliases=None, group_choices=None, file_hash='') -> ImportPlan` and `apply_import(plan: ImportPlan) -> None`.
- Produces `RedesignStore(Protocol)` capabilities implemented entirely by infrastructure; UI does not depend on private storage method names.

- [x] **Step 1: Add failing synthetic migration/storage tests.** Build a version-1 fixture in a pytest temporary path using the original V1 schema, with arbitrary student IDs, cards, scans and rosters. Assert upgrade retains every old ID and scan, writes a safety copy before DDL, increases user_version only after success, and repeated reopen creates no duplicates. Inject a migration failure and assert the old file remains usable. Direct SQL inserts must reject mismatched membership/group year, mismatched profile specialty, alias target specialty and duplicate ordinary groups.
- [x] **Step 2: Run the migration tests before implementation.** Run `pytest tests/test_redesign_migration.py -q`; expect failures for missing migration/catalog invariants.
- [x] **Step 3: Implement sequential schema migration.** Fresh databases create V1 then apply V2; existing V1 files validate application/mode before backup/DDL. Keep old tables and nullable book numbers for unresolved legacy students; do not infer books/year/specialty from old free text. Use `BEGIN IMMEDIATE`, rollback on any failure and set version last. Register canonical catalog names in the same alias resolver. Add composite FKs/triggers for cross-table invariants and membership audit. Avoid executescript's implicit pre-commit inside an existing transaction. Validate latest required tables, integrity and foreign keys.
- [x] **Step 4: Add failing catalog/import tests.** Assert `00009012` and `9012` remain distinct; same-name people remain distinct; importing the same file twice reuses entities; differing course/specialty/year yields separate same-number groups; mixed profiles in one group are valid; empty/duplicate books and malformed widths reject the whole import with source row numbers. Assert explicit year rollover preserves old memberships and snapshots; aliases resolve only exact normalized names; stale preview aborts; mapping ambiguity requires group choice.
- [x] **Step 5: Run the new catalog tests.** Run `pytest tests/test_redesign_catalog.py -q`; expect failures for unimplemented public methods.
- [x] **Step 6: Implement the catalog API.** Define all DTOs now so Task 3 can import them, expose the stable service property, parse seven fields without integer-coercing books, generate UUID only for new people and preserve existing IDs. Preview captures immutable rows, choices, hash and catalog revision; apply revalidates everything in one transaction and records import rows/batch. Import does not delete absent students, create attendance or enroll past lessons. Student edits preserve ownership; alias collision blocks rename/update. Implement group variants and explicit annual transfers with audit.
- [x] **Step 7: Preserve old-backup restore.** Validate a V1 backup read-only, copy it to a private temporary database, migrate and validate that copy, then perform the existing pre-restore safety backup and replace current contents. Never modify the user's source backup. Reject wrong mode/corruption before replacing live data. Add tests for V1 restore, wrong mode and failure leaving current data intact.
- [x] **Step 8: Verify and commit only owned source/tests.** Run `pytest tests/test_redesign_catalog.py tests/test_redesign_migration.py tests/test_service.py tests/test_csv.py tests/test_dialogs.py tests/test_architecture.py -q`; require PASS. Run configured ruff/pyright on affected files. Stage explicit paths, inspect `git diff --cached --stat`, and commit `feat: add relational catalog imports and safe migration` with no database artifacts.

### Task 2: Semester rosters, scheduled pairs, sessions and reversible manual evidence

**Files:**
- Modify backend files from Task 1, especially `domain/redesign.py`, `application/redesign.py`, `application/redesign_ports.py`, `infrastructure/redesign_sqlite.py`, `application/service.py`; no controller/presentation edits.
- Test: `tests/test_redesign_attendance.py`.

**Interfaces:**
- Consumes Task 1 groups, memberships, students and typed storage transactions.
- Produces the contract's roster, block, session, manual and evidence methods; `schedule_block(roster_id, on_date, start_slot, *, timezone='Europe/Minsk', title='', room='', block_id=None) -> ScheduledBlock`; `open_session(block_id, target_pair_ids: set[int]) -> RegistrationSession`; `record_session_scan(session_id, uid, at) -> PublicResult`; `create_manual_batch(targets: set[tuple[str, int]], reason: str) -> ManualBatch`.
- Existing `report(...) -> AttendanceReport`, `lesson_log`, `lesson_counts`, `list_cards` and `recover_interrupted` include new evidence while retaining legacy semantics.

- [x] **Step 1: Add failing roster/block tests.** Assert whole-group rosters support multiple specialties at one course/year, student-mode rosters accept exactly one group, discipline enrollment is respected, semester copy has independent membership, two slots create separate pair IDs/times, and slot 8 rejects duration 2. Edit future rosters then freeze/open one pair and assert past context remains unchanged.
- [x] **Step 2: Verify these fail.** Run `pytest tests/test_redesign_attendance.py -q`; require a meaningful missing-behavior failure.
- [x] **Step 3: Implement roster/block persistence and APIs.** Save discipline-group links, semester rosters and snapshots. Convert fixed local slot times to aware UTC while retaining timezone. Parent blocks contain one/two legacy lessons as pairs. Do not rewrite a used block or history through ordinary editing. An explicit historical snapshot adjustment must be audited. Expose frozen `get_pair_contexts(pair_id)` and current contexts for unfixed pairs.
- [x] **Step 4: Add session/manual/report tests.** Create two sessions for one laboratory block with distinct target coverage; an unknown scan in one session is later bound after expanding that session. Assert only its scope changes and scan timestamp is unchanged. Test several cards/manual grounds yield one presence, fixed historical targets stay fixed, manual confirmation is labeled separately from arrival, duplicate manual batch adds no redundant grounds, and revocation preserves other batch/card grounds. Commit failure must never return success; restart recovery closes sessions without resuming.
- [x] **Step 5: Implement sessions and evidence.** Store original UID/time/session in `card_evidence`, explicit targets and scope-change audit. Never mirror into legacy scans. Add one-active-session storage constraint; reconcile the old single-open-lesson index so a block can target two pairs. Session expansion projects earlier/later session evidence and quick manual marks through new scope; neighboring sessions and fixed manual batches are untouched. PublicResult is returned only after durable commit. Closing/switching/recovery preserve evidence.
- [x] **Step 6: Implement manual batches and projections.** Validate every exact target and reason before one transaction; audit batch creation/revocation. Union active legacy/card/manual grounds for reports/logs/counts without double counting students. Late card binding uses current card ownership consistently with old behavior and rebuilds affected projections. Keep outside-roster presence visible and unknown cards available to private queue.
- [x] **Step 7: Verify and commit.** Run `pytest tests/test_redesign_attendance.py tests/test_redesign_catalog.py tests/test_redesign_migration.py tests/test_service.py tests/test_controller.py tests/test_architecture.py -q`; require PASS; run ruff/pyright for owned code. Stage explicit source/tests and commit `feat: add semester blocks and session attendance evidence`.

### Task 3: Session controller and Qt workflow with stable selection

**Files:**
- Modify: `src/attendance_lab/application/controller.py`, `src/attendance_lab/presentation/admin.py`, `schedule.py`, `students.py`, `reports.py`, `dialogs.py`, `common.py`, `theme.py`, `logs.py` where needed.
- Create focused view/model modules under `src/attendance_lab/presentation/` (for example `attendance_models.py`, `lesson_workspace.py`, `preparation.py`, `commands.py`) when they keep responsibilities small.
- Test: `tests/test_redesign_controller.py`, `tests/test_redesign_gui.py`; preserve existing controller/GUI/theme/dialog tests.

**Interfaces:**
- Consumes frozen public DTOs and `AttendanceService.redesign` from the shared contract; no infrastructure imports or SQL.
- Produces additive `AttendanceController.active_session_id`, `open_session(block_id, target_pair_ids)`, `close_session()`, `expand_session()` and a defaulted `CapturedScan.session_id`; keeps legacy constructor/open_lesson/close_lesson behavior.
- Keeps existing MainWindow compatibility attributes while presenting the spec's four sections and separate public window.

- [x] **Step 1: Add failing controller tests.** Capture a scan, switch sessions, drain, and assert the original session owns it. Close flushes accepted scans. Expansion flushes first and changes only explicitly active session. Commit failure creates public error only; no success or unknown-card queue item is emitted before storage commit. Old lesson tests still pass.
- [x] **Step 2: Run controller tests.** Run `pytest tests/test_redesign_controller.py tests/test_controller.py -q`; expect new tests to fail initially.
- [x] **Step 3: Implement additive session capture.** Preserve lock ordering and do not join readers while holding callback locks. Capture lesson/pair and session IDs together, dispatch using captured session ID, flush before transitions and close whichever registration mode is active at shutdown. Browsing only changes viewed IDs.
- [x] **Step 4: Add failing model/selection/search tests.** Model sorts group 2 before 10, then name/book ties; multi-field direction works; proxy sorting preserves exact student/pair selections; period/discipline/filter change clears targets; invalid metadata/future matrix cells cannot be silently bulk credited. Test block row selection expands only to its own one/two pairs. Test search on 300 synthetic students with cache load count unchanged during typing, word reordering/parts/typos/`ё/е`, and explicit selection before binding.
- [x] **Step 5: Implement data models and commands.** Use QAbstractTableModel/QSortFilterProxyModel with stable identity roles. Shared QActions drive keyboard, menus and buttons. Current block uses row selection; matrix uses exact cells with rectangular Shift/mouse drag selection and block-grouped headers. Snapshot recipients by IDs before a preview listing dates/pairs/student count/mark count/reason; apply only visible valid IDs. Show evidence and batch revoke in a side panel preserving selection.
- [x] **Step 6: Implement Preparation.** Add seven-column preview import with explicit academic year, encoding/delimiter, alias and ambiguous-group choices; apply only a valid reviewed plan. Provide catalog/group/student editing, discipline group enrollment, semester roster modes/copy, and block dialog with visible date/slot/duration. Legacy editors may remain accessible as compatibility paths; normal scheduling uses fixed slots. All dialogs have visible primary/cancel buttons and mouse access.
- [x] **Step 7: Implement Lesson workspace and four sections.** Show today's schedule, current block roster, pinned unknown-card queue, matching search, session quick manual mark and embedded log. Keep persistent active block/pairs/roster/coverage/device banner and adjacent open/close actions. Viewing another block never switches registration. Known-card scans update affected cells/counters only. Preserve an unresolved current UID until bound/deferred; deduplicate pending unknown UID; manual search draft restores matching query/focus. New scans must not call a global refresh.
- [x] **Step 8: Add shortcut/focus/public tests.** Exercise F1/F2/F3/F6/F9/Shift+F9/Ctrl+Enter/Esc via Qt; ensure modal/public focus blocks private shortcuts. Mouse click runs the same actions. During new known/unknown scans assert query text, cursor, selected IDs, scroll and keyboard focus remain stable. Existing public allowlist tests must still pass.
- [x] **Step 9: Apply theme and validate windows.** Use the BSU palette/Arial, visible keyboard focus and state text/icons in addition to color. Reuse an actual supplied logo only with correct proportions/clear space; do not invent one. Check table/header/dialog layouts, empty state, long Cyrillic names and 980x720 minimum window; ensure all actions remain reachable with keyboard/mouse.
- [x] **Step 10: Verify and commit owned files.** Run `pytest tests/test_redesign_controller.py tests/test_redesign_gui.py tests/test_controller.py tests/test_gui.py tests/test_dialogs.py tests/test_theme.py tests/test_architecture.py -q`; require PASS. Run ruff/pyright; stage explicit owned source/tests and commit `feat: add keyboard and mouse attendance workspace`.

### Task 4: Independent acceptance, private current-data migration and PR

**Files:**
- Root owns `src/attendance_lab/bootstrap.py` integration, `README.md`, `docs/VALIDATION.md`, necessary focused acceptance tests/tooling and explicit packaging configuration changes only.
- Private migration backups and reports stay outside Git and are never attached to the PR/chat.

**Interfaces:**
- Consumes all previous tasks and existing CLI `--demo`, `--db`, `--timezone`, `--smoke-test`; default new scheduling is Europe/Minsk.
- Produces verified application, migrated current local DB and a GitHub PR attached to the chat. Do not push until the staged/committed artifact audit passes.

- [x] **Step 1: Review complete branch.** High coordinator checks exact contract implementation, all spec sections, storage bypass invariants, cross-session coverage, historical snapshot integrity, current-vs-viewed distinction, model identity and public privacy. Resolve actionable findings with owning workers. Root reviews plan and integration; no redundant user approval pause.
- [x] **Step 2: Run complete configured checks.** Run pytest, ruff and pyright using project configuration and the installed Python 3.14 environment. Run the offscreen CLI demo smoke test against a generated temporary synthetic DB. Record exact commands/result counts and limitations in `docs/VALIDATION.md`.
- [x] **Step 3: Run acceptance and visual pass on synthetic data.** Walk import twice, year rollover, aliases/conflicts, roster copy, two-pair block, one-pair registration, whole-block expansion, late unknown binding, quick manual mark, fixed matrix batch/revoke, sorting/selection and mouse/keyboard. Verify focus under incoming scans and modal/public shortcut isolation. Capture local synthetic screenshots only. Measure cached search p95 and UID-to-display latency; state measurement environment.
- [x] **Step 4: Rehearse migration privately.** Determine configured current DB path without printing student data. Ensure application/registration is stopped and hold the same database lock; make a private consistent safety backup (SQLite backup API includes WAL). Migrate a private copy first and compare only aggregate row/ID/card/scan invariants, integrity/foreign-key checks and schema version; verify old backup restore in a separate private scratch database. Log no names, books, UID lists or database paths into committed fixtures.
- [x] **Step 5: Migrate authorized current local DB.** Recheck no running writer/active registration, preserve the safety backup, run proven migration and verify aggregate invariants, foreign keys and quick_check. On failure retain original/restorable safety copy and report accurately; do not claim completion until actual migration succeeds. Never open/publish real contents in screenshots or commits.
- [x] **Step 6: Audit publication contents.** Inspect tracked/untracked files and staged/commit diff, Git ignore rules and all binary files. Exclude every `.db`, `.sqlite*`, WAL/SHM, backup, real CSV, logs and screenshots of real data. Inspect fixture strings for synthetic-only identity. Stage explicit paths and commit remaining integration/docs.
- [ ] **Step 7: Create and attach PR.** Push `feat/attendance-redesign`, create a PR with concrete resulting behavior, migration compatibility and exact validation. Use a file for multiline PR body. Attach the created PR with `mcp__codex_app__attach_artifact`. Report PR link, checks and successful private migration without student data. If tooling or permission blocks a step, state the exact remaining limitation instead of claiming success.

## Completion evidence

All required behavior is implemented and reviewed; legacy and redesign tests pass; measured/visual outcomes are documented; the actual local database migration has succeeded with a private safety backup; Git audit confirms no database/student data is published; the created PR is attached. Plan checkboxes track actual work only, never presumed success.


## Verification record — 2026-10-19

Root final frozen-tree checks: 125 tests passed; Ruff check/format, Pyright and diff checks clean; wheel/sdist and installed-wheel offscreen smoke passed. High review has no remaining actionable P1/P2. Synthetic Qt windows inspected at 980×720 and 1366×768. The authorized local schema1→2 migration preserved all812 legacy rows in9 tables, with a durable pre-migration backup, quick_check=ok and no FK violations. Private data and backups remain outside Git. Root alone performs the final commit and PR publication.
