"""Assessment tables, installed by the shared transactional schema migration."""

GRADING_SCHEMA = """
CREATE TABLE course_works (id INTEGER PRIMARY KEY,
 course_id INTEGER NOT NULL REFERENCES semester_courses(id), title TEXT NOT NULL,
 maximum REAL NOT NULL CHECK(maximum>0), weight REAL NOT NULL CHECK(weight>0));
CREATE TABLE course_scores (work_id INTEGER NOT NULL REFERENCES course_works(id),
 student_id TEXT NOT NULL REFERENCES students(id), score REAL NOT NULL CHECK(score>=0),
 PRIMARY KEY(work_id,student_id));
CREATE TABLE course_reports (course_id INTEGER NOT NULL REFERENCES semester_courses(id),
 student_id TEXT NOT NULL REFERENCES students(id), lesson_id INTEGER NOT NULL REFERENCES course_lessons(id),
 group_id INTEGER NOT NULL REFERENCES academic_groups(id), topic TEXT NOT NULL, topic_key TEXT NOT NULL,
 written INTEGER NOT NULL CHECK(written IN (0,1)), levels TEXT NOT NULL,
 PRIMARY KEY(course_id,student_id), UNIQUE(course_id,group_id,topic_key));
CREATE TABLE course_calculations (course_id INTEGER NOT NULL REFERENCES semester_courses(id),
 version INTEGER NOT NULL CHECK(version>0), parameters TEXT NOT NULL,
 applied_at TEXT NOT NULL, PRIMARY KEY(course_id,version));
CREATE TABLE calculation_templates (name TEXT PRIMARY KEY, parameters TEXT NOT NULL);
CREATE TRIGGER report_course_lesson_insert BEFORE INSERT ON course_reports
 WHEN NOT EXISTS(SELECT 1 FROM course_lessons l WHERE l.id=NEW.lesson_id AND l.course_id=NEW.course_id)
 BEGIN SELECT RAISE(ABORT,'report lesson outside course'); END;
CREATE TRIGGER report_course_lesson_update BEFORE UPDATE ON course_reports
 WHEN NOT EXISTS(SELECT 1 FROM course_lessons l WHERE l.id=NEW.lesson_id AND l.course_id=NEW.course_id)
 BEGIN SELECT RAISE(ABORT,'report lesson outside course'); END;
"""
GRADING_TABLE_COLUMNS: dict[str, set[str]] = {
    "course_works": set("id course_id title maximum weight".split()),
    "course_scores": set("work_id student_id score".split()),
    "course_reports": set(
        "course_id student_id lesson_id group_id topic topic_key written levels".split()
    ),
    "course_calculations": set("course_id version parameters applied_at".split()),
    "calculation_templates": set("name parameters".split()),
}
