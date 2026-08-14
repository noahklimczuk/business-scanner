"""The window.

A sidebar, a page, and an activity strip along the bottom. That is the whole
shell, and it is deliberately the whole shell: the operator is one person doing
one thing at a time, so tabs, docks, floating panels and a menu bar full of
things nobody clicks would all be furniture.

The order of the sidebar is the order of the work — find them, call them, build
it, show it, get paid — so moving down the list is moving through the day.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                    # noqa: E402
import db                                                        # noqa: E402
from gui import theme                                            # noqa: E402
from gui.work import Runner                                      # noqa: E402

APP_NAME = "Leadsmith"
WINDOW_MIN = (1080, 720)


def app_icon() -> QIcon:
    """Drawn rather than shipped, so there is no binary asset to keep in sync.

    A rounded square with the wordmark's initial. Small, sharp at every size
    Windows asks for, and it means the packaged exe has no external file it can
    fail to find.
    """
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(Qt.NoBrush)
        painter.fillRect(0, 0, size, size, Qt.transparent)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.SolidPattern)
        painter.setBrush(QColor(theme.ACCENT))
        radius = size * 0.22
        painter.drawRoundedRect(0, 0, size, size, radius, radius)
        painter.setPen(QColor("#ffffff"))
        font = painter.font()
        font.setPixelSize(int(size * 0.62))
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "L")
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class Sidebar(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(214)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(0)

        wordmark = QLabel(APP_NAME)
        wordmark.setObjectName("Wordmark")
        self.box.addWidget(wordmark)
        sub = QLabel("find · build · sell")
        sub.setObjectName("WordmarkSub")
        self.box.addWidget(sub)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.foot = QLabel("")
        self.foot.setObjectName("SidebarFoot")
        self.foot.setWordWrap(True)

    def add_section(self, title: str) -> None:
        label = QLabel(title)
        label.setObjectName("NavBadge")
        self.box.addWidget(label)

    def add_nav(self, text: str, index: int) -> QPushButton:
        item = QPushButton(text)
        item.setObjectName("NavButton")
        item.setCheckable(True)
        item.setCursor(Qt.PointingHandCursor)
        self.group.addButton(item, index)
        self.box.addWidget(item)
        return item

    def finish(self) -> None:
        self.box.addStretch(1)
        self.box.addWidget(self.foot)


class Window(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(*WINDOW_MIN)

        self.runner = Runner()
        self.con = db.connect()
        self.cfg = config.load()

        root = QWidget()
        root.setObjectName("Root")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar()
        outer.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        right.addWidget(self._activity_strip())
        outer.addLayout(right, 1)
        self.setCentralWidget(root)

        self._build_pages()
        self.sidebar.group.idClicked.connect(self.go)
        self.runner.busy_changed.connect(self._busy_changed)
        self._shortcuts()
        self.go(0)
        QTimer.singleShot(80, self._first_run_check)

    # -- chrome ------------------------------------------------------------
    def _activity_strip(self) -> QWidget:
        strip = QFrame()
        strip.setObjectName("ActivityStrip")
        strip.setFixedHeight(38)
        box = QHBoxLayout(strip)
        box.setContentsMargins(18, 0, 18, 0)
        box.setSpacing(12)

        self.status = QLabel("Ready")
        self.status.setObjectName("Muted")
        box.addWidget(self.status, 1)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setVisible(False)
        box.addWidget(self.progress)

        self.cancel_button = QPushButton("Stop")
        self.cancel_button.setProperty("kind", "quiet")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.runner.cancel)
        box.addWidget(self.cancel_button)
        return strip

    def _build_pages(self) -> None:
        from gui.pages.find import FindPage
        from gui.pages.leads import LeadsPage
        from gui.pages.money import MoneyPage
        from gui.pages.settings import SettingsPage
        from gui.pages.sites import SitesPage
        from gui.pages.today import TodayPage

        self.sidebar.add_section("Work")
        self.pages = []
        for index, (label, factory) in enumerate([
            ("  Today", TodayPage),
            ("  Find businesses", FindPage),
            ("  All leads", LeadsPage),
        ]):
            self.sidebar.add_nav(label, index)
            page = factory(self)
            self.pages.append(page)
            self.stack.addWidget(page)

        self.sidebar.add_section("Clients")
        for offset, (label, factory) in enumerate([
            ("  Sites", SitesPage),
            ("  Money", MoneyPage),
            ("  Settings", SettingsPage),
        ]):
            index = 3 + offset
            self.sidebar.add_nav(label, index)
            page = factory(self)
            self.pages.append(page)
            self.stack.addWidget(page)

        self.sidebar.finish()

    def _shortcuts(self) -> None:
        for key, index in (("Ctrl+1", 0), ("Ctrl+2", 1), ("Ctrl+3", 2),
                           ("Ctrl+4", 3), ("Ctrl+5", 4), ("Ctrl+6", 5)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda _=False, i=index: self.go(i))
            self.addAction(action)

        refresh = QAction(self)
        refresh.setShortcut(QKeySequence("F5"))
        refresh.triggered.connect(self.refresh)
        self.addAction(refresh)

    # -- navigation --------------------------------------------------------
    def go(self, index: int) -> None:
        button = self.sidebar.group.button(index)
        if button:
            button.setChecked(True)
        self.stack.setCurrentIndex(index)
        page = self.stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    def refresh(self) -> None:
        page = self.stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()
        self.update_sidebar_foot()

    def update_sidebar_foot(self) -> None:
        import billing
        money = billing.summarise(db.subscriptions(self.con))
        counts = db.counts_by_stage(self.con)
        workable = sum(counts.get(s, 0) for s in ("new", "queued", "contacted",
                                                  "interested"))
        self.sidebar.foot.setText(
            f"{workable} leads to work\n"
            f"{money.clients} clients · ${money.mrr:,.0f}/mo")

    # -- work --------------------------------------------------------------
    def _busy_changed(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        if not busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        for page in self.pages:
            if hasattr(page, "set_busy"):
                page.set_busy(busy)

    def run_job(self, job, *, status: str = "Working…", on_done=None,
                on_fail=None) -> bool:
        """Start a job and wire it to the activity strip.

        Refusing a second job rather than queueing it is deliberate: two scans
        at once would double-spend, and the operator would have no way to tell
        which one the progress bar belonged to.
        """
        if self.runner.busy:
            self.toast("Something is already running — let it finish first.")
            return False

        self.status.setText(status)
        self.progress.setRange(0, 0)                      # indeterminate
        job.signals.progress.connect(self._on_progress)
        job.signals.note.connect(self.status.setText)

        def done(result):
            self.status.setText("Ready")
            self.refresh()
            self.update_sidebar_foot()
            if on_done:
                on_done(result)

        def fail(message, detail):
            self.status.setText("Ready")
            if on_fail:
                on_fail(message, detail)
            else:
                self.error(message, detail)

        return self.runner.start(job, on_done=done, on_fail=fail)

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        if message:
            self.status.setText(message)

    # -- talking to the operator -------------------------------------------
    def toast(self, message: str) -> None:
        self.status.setText(message)
        QTimer.singleShot(4000, lambda: self.status.setText("Ready"))

    def error(self, message: str, detail: str = "") -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("That did not work")
        box.setText(message)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def confirm(self, title: str, message: str, ok_text: str = "Yes") -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(message)
        yes = box.addButton(ok_text, QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _first_run_check(self) -> None:
        """No key, no scan. Say so once, on the page that fixes it."""
        try:
            config.api_key(self.cfg)
        except Exception:                                          # noqa: BLE001
            self.go(5)
            self.toast("Add your Google Places key to start finding businesses.")

    def closeEvent(self, event) -> None:                           # noqa: N802
        if self.runner.busy:
            if not self.confirm(
                    "Something is still running",
                    "A job is still going. Closing now could leave a site "
                    "half-written or a scan half-saved.\n\nClose anyway?",
                    "Close anyway"):
                event.ignore()
                return
            self.runner.cancel()
            self.runner.wait(5000)
        self.con.close()
        event.accept()


def main() -> int:
    import startup

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName("Leadsmith")
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())

    theme.apply(app, _system_is_dark(app))

    startup.note("Opening your leads…")
    window = Window()
    window.resize(1240, 800)
    window.show()
    # After show(), not before: closing on the line above would leave a blank
    # rectangle in the gap while Qt paints the first frame, which looks worse
    # than the splash it replaced.
    app.processEvents()
    startup.done()
    return app.exec()


def _system_is_dark(app: QApplication) -> bool:
    """Follow Windows rather than picking for them.

    Qt 6.5+ reports the OS colour scheme directly; older builds get the
    lightness of the default window colour, which is right in practice.
    """
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import Qt as QtNS
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == QtNS.ColorScheme.Dark:
            return True
        if scheme == QtNS.ColorScheme.Light:
            return False
    except Exception:                                              # noqa: BLE001
        pass
    return app.palette().window().color().lightness() < 128


if __name__ == "__main__":
    raise SystemExit(main())
