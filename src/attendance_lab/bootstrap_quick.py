"""Explicit production Qt Quick entry point during staged rollout."""

import sys

from attendance_lab.bootstrap import main as application_main


def main(argv: list[str] | None = None) -> int:
    return application_main(["--qml", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
