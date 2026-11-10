"""Read-only report topic catalogues, independent of the course programme."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReportTopic:
    title: str
    section: str = ""
    source_number: int | None = None


@dataclass(frozen=True, slots=True)
class ReportTopicCatalog:
    discipline_names: tuple[str, ...]
    topics: tuple[ReportTopic, ...]
