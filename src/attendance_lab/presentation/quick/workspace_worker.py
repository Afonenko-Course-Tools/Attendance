"""FIFO QObject executor. Its factory creates storage on this thread, never the GUI."""

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from attendance_lab.application.operations import Operation, canonical_json
from attendance_lab.application.quick_backend import QuickBackend, ViewContext


@dataclass(frozen=True, slots=True)
class Request:
    operation: Operation
    context: ViewContext
    read_generation: int = 0


@dataclass(frozen=True, slots=True)
class Response:
    request: Request | None
    context: ViewContext
    snapshot_json: str = "{}"
    result_json: str = "{}"
    error: str = ""
    refresh_error: str = ""
    public_results: tuple = ()


class WorkspaceWorker(QObject):
    completed = Signal(object)
    stopped = Signal(bool, str)

    def __init__(self, factory: Callable[[], QuickBackend]):
        super().__init__()
        self.factory = factory
        self.backend: QuickBackend | None = None
        self.context = ViewContext()
        self.timer: QTimer | None = None

    def _assert_owner(self):
        assert QThread.currentThread() == self.thread()

    @Slot()
    def initialize(self):
        self._assert_owner()
        try:
            self.backend = self.factory()
            snapshot = canonical_json(self.backend.snapshot(self.context))
            self.completed.emit(Response(None, self.context, snapshot_json=snapshot))
            self.timer = QTimer(self)
            self.timer.setInterval(80)
            self.timer.timeout.connect(self.poll)
            self.timer.start()
        except Exception as exc:
            self.completed.emit(Response(None, self.context, error=str(exc)))

    @Slot(object)
    def execute(self, request: Request):
        self._assert_owner()
        backend = self.backend
        if backend is None:
            self.completed.emit(Response(request, request.context, error="Хранилище не открыто."))
            return
        if request.read_generation and request.context.revision < self.context.revision:
            # A delayed read may otherwise overwrite another view's transient
            # preview/search data. Mutations always execute their captured intent.
            self.completed.emit(
                Response(request, request.context, public_results=backend.public_results())
            )
            return
        if request.context.revision >= self.context.revision:
            self.context = request.context
        error = ""
        result = {}
        result_json = "{}"
        try:
            if request.operation.kind != "view.refresh":
                result = backend.command(request.operation)
            backend.poll()
            result_json = canonical_json(result)
        except Exception as exc:
            error = str(exc)
        refresh_error = ""
        snapshot = "{}"
        try:
            snapshot = canonical_json(backend.snapshot(request.context))
        except Exception as exc:
            refresh_error = str(exc)
        self.completed.emit(
            Response(
                request,
                request.context,
                snapshot,
                result_json,
                error,
                refresh_error,
                backend.public_results(),
            )
        )

    @Slot()
    def poll(self):
        self._assert_owner()
        if self.backend is None:
            return
        public = ()
        try:
            dirty = self.backend.poll()
            public = self.backend.public_results()
            if dirty or public:
                snapshot = canonical_json(self.backend.snapshot(self.context)) if dirty else "{}"
                self.completed.emit(
                    Response(None, self.context, snapshot_json=snapshot, public_results=public)
                )
        except Exception as exc:
            self.completed.emit(
                Response(None, self.context, refresh_error=str(exc), public_results=public)
            )

    @Slot()
    def shutdown(self):
        self._assert_owner()
        if self.timer is not None:
            self.timer.stop()
        try:
            if self.backend is not None:
                self.backend.close()
                self.backend = None
        except Exception as exc:
            if self.timer is not None:
                self.timer.start()
            # shutdown may be the first operation to discover a failed FIFO
            # head. Publish its retained context before reporting refusal to exit.
            snapshot, refresh_error, public = "{}", "", ()
            try:
                if self.backend is not None:
                    self.backend.poll()
                    public = self.backend.public_results()
                    snapshot = canonical_json(self.backend.snapshot(self.context))
            except Exception as refresh:
                refresh_error = str(refresh)
            self.completed.emit(
                Response(
                    None,
                    self.context,
                    snapshot_json=snapshot,
                    refresh_error=refresh_error,
                    public_results=public,
                )
            )
            self.stopped.emit(False, str(exc))
        else:
            self.stopped.emit(True, "")
