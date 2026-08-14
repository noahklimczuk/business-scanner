"""Today — the page the operator opens first and works down.

This is the whole product from the operator's side: a short list of people
worth ringing, each with the reason to ring them and every action that lead
could need, in one place. If this page is good the rest of the app is
optional; if it is bad, nothing else rescues it.

So: no table. A table makes you read a row, decide, then hunt for the button.
Each lead is a card with its name, the reason, the phone number in a size you
can read across a desk, and the four things you might do next.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout

import board
import db
from gui.pages import Page
from gui.widgets import Banner, Card, Pill, button, divider, muted, row


class LeadCard(QFrame):
    """One business, and everything you might do about it."""

    def __init__(self, page: "TodayPage", lead: dict[str, Any]):
        super().__init__()
        self.setObjectName("Card")
        self.page, self.lead = page, lead

        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        name = QLabel(lead["name"])
        name.setObjectName("CardTitle")
        name.setWordWrap(True)
        top.addWidget(name, 1)

        if lead.get("score") is not None:
            top.addWidget(Pill(f"score {lead['score']}", _tone(lead["score"])))
        stage = lead.get("stage") or "new"
        if stage != "new":
            top.addWidget(Pill(stage, "accent"))
        box.addLayout(top)

        why = " · ".join(lead.get("why") or [])
        if why:
            box.addWidget(muted(why))

        contact = QHBoxLayout()
        contact.setSpacing(14)
        phone = QLabel(lead.get("phone") or "No phone listed — walk in")
        phone.setStyleSheet("font-size: 19px; font-weight: 600;")
        phone.setTextInteractionFlags(Qt.TextSelectableByMouse)
        contact.addWidget(phone)
        if lead.get("phone"):
            contact.addWidget(button("Copy", "quiet", self.copy_phone,
                                     "Copy the number to the clipboard"))
        contact.addStretch(1)
        box.addLayout(contact)

        if lead.get("address"):
            box.addWidget(muted(lead["address"]))

        box.addWidget(divider())

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.buttons = []

        has_site = bool(lead.get("site_dir"))
        self._add(actions, "Build site" if not has_site else "Rebuild site",
                  "primary" if not has_site else "",
                  lambda: self.page.build_site(self.lead),
                  "Writes the copy and renders the page")
        if has_site:
            self._add(actions, "Open site", "", lambda: self.page.open_site(self.lead))
        self._add(actions, "Preview online", "",
                  lambda: self.page.preview(self.lead),
                  "Publish to a private URL and show a QR code")
        self._add(actions, "Leave-behind", "",
                  lambda: self.page.leave_behind(self.lead),
                  "The one-page sheet you print and hand over")
        actions.addStretch(1)

        outcome = QComboBox()
        outcome.addItem("Log outcome…", "")
        for key, description in db.OUTCOMES.items():
            outcome.addItem(key.replace("-", " "), key)
        outcome.setToolTip("Record what happened, and move the lead")
        outcome.currentIndexChanged.connect(
            lambda _: self.page.log_outcome(self.lead, outcome))
        actions.addWidget(outcome)
        self.buttons.append(outcome)
        box.addLayout(actions)

    def _add(self, layout, text, kind, handler, tip=""):
        widget = button(text, kind, handler, tip)
        layout.addWidget(widget)
        self.buttons.append(widget)
        return widget

    def copy_phone(self) -> None:
        QGuiApplication.clipboard().setText(self.lead.get("phone") or "")
        self.page.window_ref.toast(f"Copied {self.lead.get('phone')}")

    def set_busy(self, busy: bool) -> None:
        for widget in self.buttons:
            widget.setEnabled(not busy)


def _tone(score: int) -> str:
    if score >= 70:
        return "good"
    if score >= 40:
        return "warn"
    return ""


class TodayPage(Page):
    title = "Today"
    hint = ("The leads worth a visit, best first. Nobody here has been "
            "contacted in the last week, and nobody here is already a client.")

    def __init__(self, window, parent=None):
        super().__init__(window, parent)
        self.banner = Banner("", "warn")
        self.banner.setVisible(False)
        self.body.addWidget(self.banner)
        self.cards = []
        self.body.addStretch(1)

    def refresh(self) -> None:
        for card in self.cards:
            card.setParent(None)
        self.cards = []

        leads = board.call_list(self.con)
        counts = db.counts_by_stage(self.con)
        total = sum(counts.values())

        if total == 0:
            self.banner.set_message(
                "No leads yet. Go to Find businesses and scan your town — "
                "the first scan is where everything else comes from.", "accent")
            self.banner.setVisible(True)
        elif not leads:
            self.banner.set_message(
                "Nobody to call today. Either everyone has been rung this "
                "week, or the workable leads are used up — scan a wider "
                "radius.", "accent")
            self.banner.setVisible(True)
        else:
            self.banner.setVisible(False)

        for lead in leads:
            card = LeadCard(self, lead)
            self.cards.append(card)
            self.body.insertWidget(self.body.count() - 1, card)

    def set_busy(self, busy: bool) -> None:
        for card in self.cards:
            card.set_busy(busy)

    # -- actions -----------------------------------------------------------
    def build_site(self, lead: dict[str, Any]) -> None:
        from gui.actions import build_site
        build_site(self.window_ref, lead)

    def open_site(self, lead: dict[str, Any]) -> None:
        from gui.actions import open_site
        open_site(self.window_ref, lead)

    def preview(self, lead: dict[str, Any]) -> None:
        from gui.actions import preview_site
        preview_site(self.window_ref, lead)

    def leave_behind(self, lead: dict[str, Any]) -> None:
        from gui.actions import leave_behind
        leave_behind(self.window_ref, lead)

    def log_outcome(self, lead: dict[str, Any], combo: QComboBox) -> None:
        outcome = combo.currentData()
        if not outcome:
            return
        combo.setCurrentIndex(0)
        from gui.actions import log_outcome
        log_outcome(self.window_ref, lead, outcome)
