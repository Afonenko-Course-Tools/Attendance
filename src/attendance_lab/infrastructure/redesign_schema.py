"""Additive V2 schema and structural validation manifest."""

SCHEMA_V2 = """
DROP INDEX single_open_lesson;
ALTER TABLE students ADD COLUMN record_book_number TEXT
 CHECK(record_book_number IS NULL OR (length(record_book_number)>0 AND record_book_number NOT GLOB '*[^0-9]*'));
CREATE UNIQUE INDEX students_record_book ON students(record_book_number);
CREATE TABLE academic_years (id INTEGER PRIMARY KEY, start_year INTEGER NOT NULL UNIQUE CHECK(start_year>0));
CREATE TABLE courses (id INTEGER PRIMARY KEY, number INTEGER NOT NULL UNIQUE CHECK(number>0));
CREATE TABLE specialties (id INTEGER PRIMARY KEY, name TEXT NOT NULL, normalized_key TEXT NOT NULL UNIQUE);
CREATE TABLE specialty_aliases (normalized_key TEXT PRIMARY KEY, specialty_id INTEGER NOT NULL REFERENCES specialties(id));
CREATE TABLE profiles (id INTEGER PRIMARY KEY, specialty_id INTEGER NOT NULL REFERENCES specialties(id),
 name TEXT NOT NULL, normalized_key TEXT NOT NULL, UNIQUE(specialty_id, normalized_key), UNIQUE(id,specialty_id));
CREATE TABLE profile_aliases (specialty_id INTEGER NOT NULL REFERENCES specialties(id), normalized_key TEXT NOT NULL,
 profile_id INTEGER NOT NULL, PRIMARY KEY(specialty_id,normalized_key),
 FOREIGN KEY(profile_id,specialty_id) REFERENCES profiles(id,specialty_id));
CREATE TABLE academic_groups (id INTEGER PRIMARY KEY, academic_year_id INTEGER NOT NULL REFERENCES academic_years(id),
 specialty_id INTEGER NOT NULL REFERENCES specialties(id), course_id INTEGER NOT NULL REFERENCES courses(id),
 group_number INTEGER NOT NULL CHECK(group_number>0), variant_key TEXT NOT NULL DEFAULT '',
 UNIQUE(academic_year_id,specialty_id,course_id,group_number,variant_key),
 UNIQUE(id,academic_year_id), UNIQUE(id,specialty_id));
CREATE TABLE annual_memberships (id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(id),
 academic_year_id INTEGER NOT NULL REFERENCES academic_years(id), group_id INTEGER NOT NULL,
 specialty_id INTEGER NOT NULL REFERENCES specialties(id), profile_id INTEGER,
 UNIQUE(student_id,academic_year_id),
 FOREIGN KEY(group_id,academic_year_id) REFERENCES academic_groups(id,academic_year_id),
 FOREIGN KEY(group_id,specialty_id) REFERENCES academic_groups(id,specialty_id),
 FOREIGN KEY(profile_id,specialty_id) REFERENCES profiles(id,specialty_id));
CREATE TABLE membership_changes (id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(id),
 academic_year_id INTEGER NOT NULL REFERENCES academic_years(id), previous_group_id INTEGER REFERENCES academic_groups(id),
 group_id INTEGER NOT NULL REFERENCES academic_groups(id), previous_profile_id INTEGER REFERENCES profiles(id),
 profile_id INTEGER REFERENCES profiles(id), changed_at TEXT NOT NULL);
CREATE TABLE import_batches (id INTEGER PRIMARY KEY, academic_year_id INTEGER NOT NULL REFERENCES academic_years(id),
 imported_at TEXT NOT NULL, file_hash TEXT NOT NULL, state TEXT NOT NULL CHECK(state='applied'));
CREATE TABLE import_rows (batch_id INTEGER NOT NULL REFERENCES import_batches(id), student_id TEXT NOT NULL REFERENCES students(id),
 row_number INTEGER NOT NULL, source_number INTEGER NOT NULL CHECK(source_number>0), PRIMARY KEY(batch_id,row_number));
CREATE TABLE discipline_groups (discipline_id INTEGER NOT NULL REFERENCES disciplines(id),
 group_id INTEGER NOT NULL REFERENCES academic_groups(id), PRIMARY KEY(discipline_id,group_id));
CREATE TABLE teaching_rosters (id INTEGER PRIMARY KEY, discipline_id INTEGER NOT NULL REFERENCES disciplines(id),
 academic_year_id INTEGER NOT NULL REFERENCES academic_years(id), semester INTEGER NOT NULL CHECK(semester IN (1,2)),
 name TEXT NOT NULL, kind TEXT NOT NULL, duration INTEGER NOT NULL CHECK(duration IN (1,2)),
 mode TEXT NOT NULL CHECK(mode IN ('groups','students')));
CREATE TABLE roster_groups (roster_id INTEGER NOT NULL REFERENCES teaching_rosters(id),
 group_id INTEGER NOT NULL REFERENCES academic_groups(id), PRIMARY KEY(roster_id,group_id));
CREATE TABLE roster_students (roster_id INTEGER NOT NULL REFERENCES teaching_rosters(id),
 student_id TEXT NOT NULL REFERENCES students(id), PRIMARY KEY(roster_id,student_id));
CREATE TABLE scheduled_blocks (id INTEGER PRIMARY KEY, roster_id INTEGER NOT NULL REFERENCES teaching_rosters(id),
 day TEXT NOT NULL, start_slot INTEGER NOT NULL CHECK(start_slot BETWEEN 1 AND 8),
 timezone TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', room TEXT NOT NULL DEFAULT '');
CREATE TABLE block_lessons (block_id INTEGER NOT NULL REFERENCES scheduled_blocks(id),
 lesson_id INTEGER NOT NULL UNIQUE REFERENCES lessons(id), slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 8),
 PRIMARY KEY(block_id,lesson_id));
CREATE TABLE lesson_context (lesson_id INTEGER NOT NULL REFERENCES lessons(id), student_id TEXT NOT NULL REFERENCES students(id),
 group_id INTEGER NOT NULL REFERENCES academic_groups(id), profile_id INTEGER REFERENCES profiles(id),
 full_name TEXT NOT NULL, record_book_number TEXT, group_label TEXT NOT NULL, specialty_name TEXT NOT NULL,
 academic_year_id INTEGER NOT NULL, group_number INTEGER NOT NULL, course INTEGER NOT NULL, profile_name TEXT NOT NULL, subgroup TEXT NOT NULL,
 PRIMARY KEY(lesson_id,student_id));
CREATE TABLE roster_adjustments (id INTEGER PRIMARY KEY, lesson_id INTEGER NOT NULL REFERENCES lessons(id),
 reason TEXT NOT NULL, changed_at TEXT NOT NULL, previous_context TEXT NOT NULL, new_context TEXT NOT NULL);
CREATE TABLE registration_sessions (id INTEGER PRIMARY KEY, block_id INTEGER NOT NULL REFERENCES scheduled_blocks(id),
 opened_at TEXT NOT NULL, closed_at TEXT, state TEXT NOT NULL CHECK(state IN ('open','closed')));
CREATE UNIQUE INDEX single_open_registration ON registration_sessions(state) WHERE state='open';
CREATE TABLE session_target_pairs (session_id INTEGER NOT NULL REFERENCES registration_sessions(id),
 pair_id INTEGER NOT NULL REFERENCES lessons(id), PRIMARY KEY(session_id,pair_id));
CREATE TRIGGER session_pair_block BEFORE INSERT ON session_target_pairs
 WHEN NOT EXISTS(SELECT 1 FROM registration_sessions s JOIN block_lessons b ON b.block_id=s.block_id
 WHERE s.id=NEW.session_id AND b.lesson_id=NEW.pair_id)
 BEGIN SELECT RAISE(ABORT,'pair outside session block'); END;
CREATE TABLE session_scope_changes (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES registration_sessions(id),
 changed_at TEXT NOT NULL, previous_targets TEXT NOT NULL, target_pairs TEXT NOT NULL);
CREATE TABLE card_evidence (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES registration_sessions(id),
 uid TEXT NOT NULL REFERENCES cards(uid), observed_at TEXT NOT NULL);
CREATE INDEX evidence_session_uid ON card_evidence(session_id,uid);
CREATE TABLE manual_batches (id INTEGER PRIMARY KEY, reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 confirmed_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE manual_evidence (id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES manual_batches(id),
 student_id TEXT NOT NULL REFERENCES students(id), pair_id INTEGER REFERENCES lessons(id),
 session_id INTEGER REFERENCES registration_sessions(id),
 CHECK((pair_id IS NULL)!=(session_id IS NULL)), UNIQUE(batch_id,student_id,pair_id), UNIQUE(batch_id,student_id,session_id));
"""


V2_TABLE_COLUMNS: dict[str, set[str]] = {
    "academic_years": set("id start_year".split()),
    "courses": set("id number".split()),
    "specialties": set("id name normalized_key".split()),
    "specialty_aliases": set("normalized_key specialty_id".split()),
    "profiles": set("id specialty_id name normalized_key".split()),
    "profile_aliases": set("specialty_id normalized_key profile_id".split()),
    "academic_groups": set(
        "id academic_year_id specialty_id course_id group_number variant_key".split()
    ),
    "annual_memberships": set(
        "id student_id academic_year_id group_id specialty_id profile_id".split()
    ),
    "membership_changes": set(
        "id student_id academic_year_id previous_group_id group_id previous_profile_id profile_id changed_at".split()
    ),
    "import_batches": set("id academic_year_id imported_at file_hash state".split()),
    "import_rows": set("batch_id student_id row_number source_number".split()),
    "discipline_groups": set("discipline_id group_id".split()),
    "teaching_rosters": set(
        "id discipline_id academic_year_id semester name kind duration mode".split()
    ),
    "roster_groups": set("roster_id group_id".split()),
    "roster_students": set("roster_id student_id".split()),
    "scheduled_blocks": set("id roster_id day start_slot timezone title room".split()),
    "block_lessons": set("block_id lesson_id slot".split()),
    "lesson_context": set(
        "lesson_id student_id group_id profile_id full_name record_book_number group_label specialty_name academic_year_id group_number course profile_name subgroup".split()
    ),
    "roster_adjustments": set(
        "id lesson_id reason changed_at previous_context new_context".split()
    ),
    "registration_sessions": set("id block_id opened_at closed_at state".split()),
    "session_target_pairs": set("session_id pair_id".split()),
    "session_scope_changes": set("id session_id changed_at previous_targets target_pairs".split()),
    "card_evidence": set("id session_id uid observed_at".split()),
    "manual_batches": set("id reason confirmed_at revoked_at".split()),
    "manual_evidence": set("id batch_id student_id pair_id session_id".split()),
}
