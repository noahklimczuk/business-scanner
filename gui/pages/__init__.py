"""Pages.

Each one is a title, a hint, and a scrollable body of cards. The base class
carries that so a page's own code is a description of what is on it.
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)


class Page(QWidget):
    title = ""
    hint = ""

    def __init__(self, window: Any, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.window_ref = window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        head = QVBoxLayout(header)
        head.setContentsMargins(32, 28, 32, 12)
        head.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(self.title)
        heading.setObjectName("PageTitle")
        top.addWidget(heading)
        top.addStretch(1)
        self.header_actions = top
        head.addLayout(top)

        if self.hint:
            hint = QLabel(self.hint)
            hint.setObjectName("PageHint")
            hint.setWordWrap(True)
            head.addWidget(hint)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(32, 8, 32, 28)
        self.body.setSpacing(16)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

    # Subclasses override these; the shell calls them.
    def refresh(self) -> None:            # pragma: no cover - trivial
        pass

    def set_busy(self, busy: bool) -> None:   # pragma: no cover - trivial
        pass

    @property
    def con(self) -> Any:
        return self.window_ref.con

    @property
    def cfg(self) -> dict:
        return self.window_ref.cfg
