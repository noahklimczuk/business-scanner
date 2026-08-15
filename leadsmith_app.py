#!/usr/bin/env python3
"""Entry point for the desktop app.

A separate file from `cli.py` on purpose. PyInstaller freezes a script, and
handing it something that also defines a Typer application means dragging the
whole CLI, its argument parsing and rich's terminal machinery into a windowed
executable that has no terminal to write to.

Run from source with:   python leadsmith_app.py
"""
from __future__ import annotations

import os
import sys


def _frozen_paths() -> None:
    """Keep the database and config beside the executable, not inside it.

    A PyInstaller one-file build unpacks itself to a temporary directory that
    is deleted on exit. Writing `leads.db` there would lose every lead the
    moment the app closed — so when frozen, state lives next to the .exe where
    the operator can see it, back it up, and copy it to a new machine.
    """
    if not getattr(sys, "frozen", False):
        return
    beside = os.path.dirname(sys.executable)
    os.environ.setdefault("LEADSMITH_DB", os.path.join(beside, "leads.db"))
    os.environ.setdefault("LEADSMITH_CONFIG", os.path.join(beside, "config.json"))
    os.environ.setdefault("LEADSMITH_SITES", os.path.join(beside, "sites"))
    # Invoices are the same story as the database and for higher stakes: a
    # written invoice inside the unpacked build would be deleted when the app
    # closed, and the operator would find out when a client asked for a copy.
    os.environ.setdefault("LEADSMITH_INVOICES", os.path.join(beside, "invoices"))


def selftest() -> int:
    """Build the whole window once, then exit. Used by CI on the packaged exe.

    This is the check that matters for a frozen build. PyInstaller's failure
    mode is not a broken build — it is a build that produces a perfectly good
    executable which dies on launch because a module was imported by name and
    the static analysis missed it. That failure looks like success right up
    until the operator double-clicks it.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from PySide6.QtWidgets import QApplication

    from gui import theme
    from gui.app import Window

    app = QApplication.instance() or QApplication([])
    theme.apply(app, dark=True)
    window = Window()
    for index in range(window.stack.count()):
        window.go(index)
        app.processEvents()
    window.con.close()
    print(f"selftest ok — {window.stack.count()} pages built")
    return 0


def main() -> int:
    _frozen_paths()
    if "--selftest" in sys.argv:
        return selftest()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    # The build an update replaced, if there was one. It could not be deleted
    # at the time — it was the file that process was running from — so it is
    # cleared here, where it is nothing but a stale 57MB beside the exe.
    import update
    update.clean_up()

    import startup
    # The longest single step, and the one nothing else can report on: by the
    # time this returns, Qt is loaded and the app can speak for itself.
    startup.note("Loading…")
    from gui.app import main as run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
