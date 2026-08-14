"""The three moments the app needs a decision or has something to show."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
)

import db
from gui.widgets import button, muted


class OutcomeDialog(QDialog):
    """What happened at the door.

    Two fields, because the operator is standing in the street with a phone in
    one hand. The channel defaults to walk-in since that is the whole sales
    motion; the note is optional and usually empty.
    """

    def __init__(self, parent, name: str, outcome: str):
        super().__init__(parent)
        self.setWindowTitle("Log what happened")
        self.setMinimumWidth(420)

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(14)

        title = QLabel(f"{outcome.replace('-', ' ').capitalize()} — {name}")
        title.setObjectName("DialogTitle")
        title.setWordWrap(True)
        box.addWidget(title)
        box.addWidget(muted(db.OUTCOMES.get(outcome, "")))

        if outcome == "no-answer":
            box.addWidget(muted(
                f"Three attempts with nobody home closes the lead "
                f"automatically. Your afternoon is the scarce thing, not leads."))

        label = QLabel("How")
        label.setObjectName("FieldLabel")
        box.addWidget(label)
        self.channels = QComboBox()
        for channel in db.CHANNELS:
            if channel == "preview":
                continue          # recorded by publishing, not chosen by hand
            self.channels.addItem(channel, channel)
        box.addWidget(self.channels)

        note_label = QLabel("Note (optional)")
        note_label.setObjectName("FieldLabel")
        box.addWidget(note_label)
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Back Tuesday, ask for Dave")
        box.addWidget(self.note_input)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button("Cancel", "", self.reject))
        save = button("Save", "primary", self.accept)
        save.setDefault(True)
        buttons.addWidget(save)
        box.addLayout(buttons)

    def channel(self) -> str:
        return self.channels.currentData()

    def note(self) -> str:
        return self.note_input.text().strip()


class PreviewDialog(QDialog):
    """The URL and a QR code, immediately after publishing.

    The QR is the point: the operator is about to walk into a shop and needs
    the page on the phone in their hand. Typing a pages.dev subdomain at a
    doorway is how the pitch starts badly.
    """

    def __init__(self, parent, name: str, url: str):
        super().__init__(parent)
        self.setWindowTitle("Preview published")
        self.url = url

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(14)

        title = QLabel(f"{name} is online")
        title.setObjectName("DialogTitle")
        box.addWidget(title)
        box.addWidget(muted(
            "Not indexed by Google — but anyone with the link can open it."))

        code = self._qr(url)
        if code is not None:
            holder = QLabel()
            holder.setPixmap(code)
            holder.setAlignment(Qt.AlignCenter)
            holder.setStyleSheet("background: #ffffff; border-radius: 10px;"
                                 "padding: 14px;")
            box.addWidget(holder)
            box.addWidget(muted("Point your phone camera at the square."))

        link = QLabel(f'<a href="{url}" style="color:#2f6feb">{url}</a>')
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        box.addWidget(link)

        buttons = QHBoxLayout()
        buttons.addWidget(button("Copy link", "", self.copy))
        buttons.addStretch(1)
        close = button("Done", "primary", self.accept)
        close.setDefault(True)
        buttons.addWidget(close)
        box.addLayout(buttons)

    def _qr(self, url: str) -> Optional[QPixmap]:
        """Rendered from the same encoder the printed leave-behind uses."""
        try:
            import io

            import segno
            buffer = io.BytesIO()
            segno.make(url, error="m").save(buffer, kind="png", scale=6, border=2)
        except Exception:                                          # noqa: BLE001
            return None
        image = QImage.fromData(buffer.getvalue(), "PNG")
        return QPixmap.fromImage(image) if not image.isNull() else None

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.url)


class SpendDialog(QDialog):
    """Not used directly — the window's `confirm` covers it — but kept as the
    one place the wording of a spend prompt is defined, so scanning and copy
    generation ask the same way."""

    @staticmethod
    def text(what: str, estimate: float, spent_30d: float) -> str:
        return (f"{what}\n\n"
                f"Estimated cost: ${estimate:,.2f} USD\n"
                f"Spent in the last 30 days: ${spent_30d:,.2f}")
