"""The pieces every page is built from.

Small and boring on purpose. A page should read as a description of what is on
it, not as three hundred lines of layout code, and every page here fits on a
screen because these carry the repetition.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)


def _restyle(widget: QWidget) -> None:
    """Qt does not re-read a stylesheet when a property changes; it has to be
    told. Every setProperty call in this file is followed by one of these."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Card(QFrame):
    """A titled panel. The unit every page is assembled from."""

    def __init__(self, title: str = "", subtitle: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(20, 18, 20, 18)
        self.box.setSpacing(12)

        if title:
            head = QVBoxLayout()
            head.setSpacing(2)
            label = QLabel(title)
            label.setObjectName("CardTitle")
            head.addWidget(label)
            if subtitle:
                sub = QLabel(subtitle)
                sub.setObjectName("CardSub")
                sub.setWordWrap(True)
                head.addWidget(sub)
            self.box.addLayout(head)

    def add(self, widget: QWidget) -> QWidget:
        self.box.addWidget(widget)
        return widget

    def add_layout(self, layout: Any) -> Any:
        self.box.addLayout(layout)
        return layout


class Figure(QWidget):
    """A number and what it means. Money, counts, spend."""

    def __init__(self, value: str, label: str, tone: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        self.value = QLabel(value)
        self.value.setObjectName("Figure")
        if tone:
            self.value.setProperty("class", tone)
        # Tabular numerals, so a column of figures lines up and a changing
        # number does not make the label beside it twitch.
        font = self.value.font()
        font.setStyleHint(QFont.SansSerif)
        self.value.setFont(font)
        self.caption = QLabel(label)
        self.caption.setObjectName("FigureLabel")
        box.addWidget(self.value)
        box.addWidget(self.caption)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


class Pill(QLabel):
    """A small status chip. Tone is a property so the stylesheet owns colour."""

    def __init__(self, text: str, tone: str = "", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setObjectName("Pill")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

    def set_tone(self, tone: str, text: Optional[str] = None) -> None:
        if text is not None:
            self.setText(text)
        self.setProperty("tone", tone)
        _restyle(self)


class Field(QWidget):
    """A labelled input that can show its own error.

    The error lives with the field rather than in a message box, because a
    dialog that says "something is wrong" makes you hunt for which box it meant.
    """

    changed = Signal(str)

    def __init__(self, label: str, placeholder: str = "", hint: str = "",
                 password: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(5)

        caption = QLabel(label)
        caption.setObjectName("FieldLabel")
        box.addWidget(caption)

        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        if password:
            self.input.setEchoMode(QLineEdit.Password)
        self.input.textChanged.connect(self.changed.emit)
        self.input.textChanged.connect(lambda _: self.clear_error())
        box.addWidget(self.input)

        self.hint = QLabel(hint)
        self.hint.setObjectName("CardSub")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        box.addWidget(self.hint)

        self.error = QLabel("")
        self.error.setObjectName("FieldError")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        box.addWidget(self.error)

    def text(self) -> str:
        return self.input.text().strip()

    def set_text(self, value: str) -> None:
        self.input.setText(value or "")

    def set_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(bool(message))
        self.input.setProperty("invalid", "true" if message else "false")
        _restyle(self.input)

    def clear_error(self) -> None:
        if self.error.isVisible():
            self.set_error("")


def button(text: str, kind: str = "", on_click: Optional[Callable[[], None]] = None,
           tip: str = "") -> QPushButton:
    widget = QPushButton(text)
    if kind:
        widget.setProperty("kind", kind)
    if tip:
        widget.setToolTip(tip)
    if on_click:
        widget.clicked.connect(lambda: on_click())
    widget.setCursor(Qt.PointingHandCursor)
    return widget


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line


def row(*widgets: QWidget, spacing: int = 8, stretch_at: Optional[int] = None
        ) -> QHBoxLayout:
    """A horizontal run of widgets, with a stretch at `stretch_at`.

    A stretch index past the end means "stretch after everything", which is the
    common case — buttons on the left, empty space on the right. Without that,
    an index of 2 with two buttons silently added no stretch at all and the
    buttons expanded to fill the card.
    """
    box = QHBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(spacing)
    for index, widget in enumerate(widgets):
        if stretch_at is not None and index == stretch_at:
            box.addStretch(1)
        box.addWidget(widget)
    if stretch_at is not None and stretch_at >= len(widgets):
        box.addStretch(1)
    return box


def muted(text: str, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(wrap)
    return label


class Banner(QFrame):
    """A line across the top of a page that needs saying before anything else —
    no API key, a site down, money about to be spent."""

    def __init__(self, text: str = "", tone: str = "warn",
                 action: str = "", on_action: Optional[Callable[[], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setProperty("tone", tone)
        box = QHBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(12)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        box.addWidget(self.label, 1)
        if action and on_action:
            box.addWidget(button(action, "primary", on_action))

    def set_message(self, text: str, tone: str = "warn") -> None:
        self.label.setText(text)
        self.setProperty("tone", tone)
        _restyle(self)
