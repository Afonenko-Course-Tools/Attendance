"""Load packaged SCXML charts and reject invalid control definitions before use."""

from importlib.resources import files

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject
from PySide6.QtScxml import QScxmlStateMachine


def load_chart(
    parent: QObject, resource_name: str, source: bytes | None = None
) -> QScxmlStateMachine:
    if source is None:
        source = files(__package__).joinpath(resource_name).read_bytes()
    buffer = QBuffer(parent)
    buffer.setData(QByteArray(source))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    machine = QScxmlStateMachine.fromData(buffer, resource_name)
    machine.setParent(parent)
    try:
        errors = machine.parseErrors()
        if errors:
            raise ValueError(
                f"Invalid SCXML chart {resource_name}: "
                + "; ".join(error.toString() for error in errors)
            )
        if not machine.init() or not machine.isInitialized():
            raise ValueError(f"Could not initialize SCXML chart {resource_name}")
        return machine
    except BaseException:
        machine.deleteLater()
        raise
    finally:
        buffer.close()
        buffer.deleteLater()
