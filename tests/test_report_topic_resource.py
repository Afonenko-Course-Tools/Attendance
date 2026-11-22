"""The PDF's numbering irregularities must not omit or merge report topics."""

import json
from importlib.resources import files


def test_cybersecurity_catalog_preserves_all_topics_and_source_numbering():
    catalog = json.loads(
        files("attendance_lab.resources")
        .joinpath("cybersecurity_report_topics.json")
        .read_text(encoding="utf-8")
    )
    topics = catalog["topics"]
    assert catalog["course_name"] == "Основы кибербезопасности"
    assert len(topics) == 41
    assert [topic["ordinal"] for topic in topics] == list(range(1, 42))
    assert [topic["source_number"] for topic in topics] == [
        *range(1, 26),
        *range(27, 41),
        40,
        41,
    ]
    assert len({topic["title"] for topic in topics}) == 41
    sections = {section["key"] for section in catalog["sections"]}
    assert len(sections) == 10
    assert all(topic["section"] in sections for topic in topics)
    assert all("\n" not in topic["title"] for topic in topics)
    assert topics[15]["title"].endswith("объектов информационной инфраструктуры")
    assert topics[36]["title"].endswith("и угрозы V2X")
    assert topics[-1]["title"].endswith("model inversion), RAG-безопасность")
    repeated = [topic for topic in topics if topic["source_number"] == 40]
    assert len(repeated) == 2 and repeated[0]["title"] != repeated[1]["title"]
