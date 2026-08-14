"""Running the slow, expensive things without freezing the window.

A scan is four hundred HTTP calls and a couple of minutes. Copy generation is
an API round trip. A deploy shells out to wrangler. Every one of those on the
UI thread would give the operator a white unresponsive rectangle and a "not
responding" title bar, which is how a tool teaches someone to distrust it.

So all of it runs on a worker thread and reports back by signal. Three rules
hold the design together:

  - the core modules know nothing about Qt. `prospect.run()` takes a plain
    progress callback and always did; the GUI passes one that emits a signal.
    Nothing in the tool's logic imports this file.
  - a job that raises does not take the window with it. The traceback comes
    back as a `failed` signal with a message written for a person.
  - work is not started twice. The UI disables its own buttons, but the queue
    is the thing that actually guarantees it.
"""
from __future__ import annotations

import inspect
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class Signals(QObject):
    progress = Signal(int, int, str)      # done, total, message
    note = Signal(str)                    # a line for the activity log
    finished = Signal(object)             # whatever the job returned
    failed = Signal(str, str)             # short message, detail


class Job(QRunnable):
    """One unit of slow work.

    `fn` is called with a `report` keyword only if it asks for one, so ordinary
    functions can be handed straight in without wrapping.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = Signals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def report(self, done: int, total: int, message: str = "") -> bool:
        """Progress callback handed to the core modules.

        Returns False once cancelled, so a long loop can stop cleanly at its
        next checkpoint rather than being killed mid-write.
        """
        self.signals.progress.emit(done, total, message)
        return not self._cancelled

    def _inject(self) -> dict[str, Any]:
        """Hand the job to work that asked for it.

        Every action in the app is written `def work(job)` and started as
        `Job(work)` — the job cannot be passed at construction time because it
        does not exist yet. Without this the call raises TypeError the moment
        the thread starts, which is what happened to Scan, Build site, Publish
        and Check Stripe: all four were unreachable, and nothing caught it
        because the tests built the pages without pressing the buttons.
        """
        kwargs = dict(self.kwargs)
        try:
            params = inspect.signature(self.fn).parameters
        except (TypeError, ValueError):      # some builtins have no signature
            return kwargs
        if "job" in params and "job" not in kwargs:
            kwargs["job"] = self
        if "report" in params and "report" not in kwargs:
            kwargs["report"] = self.report
        return kwargs

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self._inject())
        except Exception as exc:                                  # noqa: BLE001
            # The operator gets the sentence; the detail goes to the activity
            # log where it can be copied into a bug report.
            self.signals.failed.emit(_human(exc), traceback.format_exc())
            return
        self.signals.finished.emit(result)


def _human(exc: BaseException) -> str:
    """A message for someone standing in a car park, not a stack trace.

    Our own errors are already written that way — ContentError, DeployError and
    ConfigError all carry text meant to be read. Everything else gets its type
    name attached, because "'NoneType' object is not subscriptable" on its own
    tells the operator nothing about what to do next.
    """
    message = str(exc).strip()
    named = type(exc).__name__
    if named in ("ContentError", "DeployError", "ConfigError", "BillingError",
                 "QRUnavailable"):
        return message or named
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)) and message:
        return f"Could not reach the network: {message}"
    return f"{named}: {message}" if message else named


# Jobs running outside the Runner, held here until they finish.
#
# Nothing else refers to a detached job: the thread pool owns the C++ side, and
# the Python object it was created from is free to be collected the moment the
# caller returns — taking `job.signals` with it, so the job dies on its last
# line with "Signal source has been deleted" and the result is never delivered.
# The Runner never had this problem because `_current` is a reference.
_DETACHED: set = set()


def run_detached(job: "Job", *, on_done: Optional[Callable[[Any], None]] = None,
                 on_fail: Optional[Callable[[str, str], None]] = None) -> None:
    """Start a job nobody is waiting on, off the one-at-a-time queue.

    The Runner exists to stop two jobs that spend money or write the same files
    from overlapping, and it refuses a second job while one is running. Work
    that costs nothing and that the operator did not ask for — the update check
    — must not sit in that queue, or it would block their first scan at launch
    and be blocked by a long one.
    """
    if on_done:
        job.signals.finished.connect(on_done)
    job.signals.failed.connect(on_fail or (lambda message, detail: None))
    _DETACHED.add(job)
    job.signals.finished.connect(lambda *_: _DETACHED.discard(job))
    job.signals.failed.connect(lambda *_: _DETACHED.discard(job))
    QThreadPool.globalInstance().start(job)


def wait_for_detached(ms: int = 3000) -> bool:
    """Let detached jobs finish before the app is torn down around them.

    Nothing is lost if one is still in flight at closing time — an update check
    that does not finish is an update check — but a job that returns into a
    half-destroyed window emits into objects that are already gone, which Qt
    reports on the way out as "Signal source has been deleted". Waiting the
    couple of hundred milliseconds it actually needs is cheaper than explaining
    that message to somebody.
    """
    return QThreadPool.globalInstance().waitForDone(ms)


class Runner(QObject):
    """Owns the thread pool and refuses to run two jobs at once.

    One at a time on purpose. Two concurrent scans would double-spend against
    the Places API, and two builds of the same lead would race on the same
    content.json. The UI reflects this by disabling its buttons; this is the
    part that makes it true.
    """

    busy_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self._current: Optional[Job] = None

    @property
    def busy(self) -> bool:
        return self._current is not None

    def start(self, job: Job, *, on_done: Optional[Callable[[Any], None]] = None,
              on_fail: Optional[Callable[[str, str], None]] = None) -> bool:
        if self.busy:
            return False
        self._current = job

        def done(result: Any) -> None:
            self._current = None
            self.busy_changed.emit(False)
            if on_done:
                on_done(result)

        def fail(message: str, detail: str) -> None:
            self._current = None
            self.busy_changed.emit(False)
            if on_fail:
                on_fail(message, detail)

        job.signals.finished.connect(done)
        job.signals.failed.connect(fail)
        self.busy_changed.emit(True)
        self.pool.start(job)
        return True

    def cancel(self) -> None:
        if self._current:
            self._current.cancel()

    def wait(self, ms: int = 30000) -> bool:
        """Used on window close, so a half-written site does not outlive the app."""
        return self.pool.waitForDone(ms)
