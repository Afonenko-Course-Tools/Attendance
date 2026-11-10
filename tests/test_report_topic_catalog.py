"""Report suggestions come from their own catalogue and course's saved reports."""

from test_grading import graded_course  # noqa: F401

from attendance_lab.domain.grading import ReportCard
from attendance_lab.infrastructure.report_topic_catalog import load_report_topic_catalogs


def test_catalogue_is_discipline_scoped_and_does_not_include_lesson_programme(
    graded_course,  # noqa: F811
):
    service, course, first, _ = graded_course
    service.report_topic_catalogs = load_report_topic_catalogs()
    assert service.grading.report_topics(course.id) == []
    service.save_discipline("Кибербезопасность", discipline_id=course.discipline_id)
    before = service.repository._connection.total_changes
    topics = service.grading.report_topic_catalog(course.id)
    assert len(topics) == 41
    assert topics[0].title.startswith("Защита персональных данных")
    assert topics[0].section == "Регулирование и стандарты"
    assert "Программа" not in service.grading.report_topics(course.id)
    assert service.repository._connection.total_changes == before
    student = service.grading.students(course.id)[0]
    service.grading.save_report(
        ReportCard(course.id, student.student_id, first.id, topics[0].title)
    )
    assert len(service.grading.report_topic_catalog(course.id)) == 41


def test_saved_custom_report_topic_remains_available_only_in_its_course(
    graded_course,  # noqa: F811
):
    service, course, first, _ = graded_course
    student = service.grading.students(course.id)[0]
    service.grading.save_report(
        ReportCard(course.id, student.student_id, first.id, "Самостоятельно предложенная тема")
    )
    assert service.grading.report_topics(course.id) == ["Самостоятельно предложенная тема"]
    copied = service.courses.copy_course(course.id, course.academic_year_id, 2, "Другой семестр")
    assert service.grading.report_topics(copied.id) == []
