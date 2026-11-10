"""Load the packaged reference list without reading or updating a database."""

import json
from importlib.resources import files

from attendance_lab.application.report_topics import ReportTopic, ReportTopicCatalog


def load_report_topic_catalogs() -> tuple[ReportTopicCatalog, ...]:
    data = json.loads(
        files("attendance_lab.resources")
        .joinpath("cybersecurity_report_topics.json")
        .read_text(encoding="utf-8")
    )
    sections = {section["key"]: section["title"] for section in data["sections"]}
    return (
        ReportTopicCatalog(
            ("Кибербезопасность", data["course_name"]),
            tuple(
                ReportTopic(topic["title"], sections[topic["section"]], topic["source_number"])
                for topic in data["topics"]
            ),
        ),
    )
