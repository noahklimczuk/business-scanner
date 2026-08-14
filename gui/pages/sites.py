"""Sites — what has been built, what is published, what is live.

Three states, and the difference matters: built is on this laptop, previewed is
on the public internet under our name, live is the client's own domain. The
middle one is the one that needs watching, because every stale preview is a
page carrying a stranger's business name that nobody is going to buy.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

import db
import generate
from gui.pages import Page
from gui.widgets import Card, button, muted, row


class SitesPage(Page):
    title = "Sites"
    hint = "Built on this machine, published for a pitch, or live on the client's domain."

    def __init__(self, window, parent=None):
        super().__init__(window, parent)

        card = Card("Built and published")
        card.add_layout(row(button("Refresh", "quiet", self.refresh), stretch_at=0))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Business", "State", "Where", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(360)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        card.add(self.table)
        self.empty = muted("Nothing built yet. Build a site from Today.")
        card.add(self.empty)
        self.body.addWidget(card)
        self.body.addStretch(1)

    def refresh(self) -> None:
        rows = [dict(r) for r in db.leads(self.con, limit=2000)]
        has_preview = "preview_url" in (rows[0].keys() if rows else {})
        sites = [r for r in rows
                 if r.get("site_dir") or r.get("preview_url") or r.get("live_url")]
        self.empty.setVisible(not sites)
        self.table.setRowCount(len(sites))

        for index, lead in enumerate(sites):
            if lead.get("live_url"):
                state, where = "live", lead.get("domain") or lead["live_url"]
            elif lead.get("preview_url"):
                state, where = "preview", lead["preview_url"]
            else:
                state, where = "built", generate.site_dir(lead["place_id"])

            for column, value in enumerate([lead["name"], state, where]):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, lead["place_id"])
                self.table.setItem(index, column, item)

            action = button("Open", "quiet",
                            lambda l=lead: self.open_one(l))
            self.table.setCellWidget(index, 3, action)

    def open_one(self, lead: dict) -> None:
        from gui.actions import open_path
        target = lead.get("live_url") or lead.get("preview_url")
        if target:
            import webbrowser
            webbrowser.open(target)
            return
        path = os.path.join(generate.site_dir(lead["place_id"]), "index.html")
        if os.path.exists(path):
            open_path(path)
        else:
            self.window_ref.error("That site has not been built yet.")
