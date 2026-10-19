"""Pure validation shared by application use cases."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from attendance_lab.domain.models import AttendanceError


def normalize_uid(uid: str) -> str:
    """Preserve all bytes and leading zeroes; accept common byte separators."""
    value = re.sub(r"[\s:-]", "", uid).upper()
    if not value or len(value) % 2 or not re.fullmatch(r"[0-9A-F]+", value):
        raise AttendanceError("UID должен содержать полные шестнадцатеричные байты.")
    return value


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AttendanceError("Дата и время должны содержать часовой пояс.")
    return value.astimezone(UTC)


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise AttendanceError(f"Неизвестный часовой пояс: {name}") from exc
    return name


def required_text(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise AttendanceError(f"Заполните поле «{label}».")
    if "\x00" in result:
        raise AttendanceError(f"Поле «{label}» содержит недопустимый символ.")
    return result
