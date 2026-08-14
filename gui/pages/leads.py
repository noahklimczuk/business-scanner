"""All leads — the table you go to when you want to find one specific business.

Today is the working page; this is the reference. Search filters as you type
because a scan produces hundreds of rows and scrolling is not finding.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHeaderView, QLineEdit, QTableWidget,
    QTableWidgetItem,
)

import db
from gui.pages import Page
from gui.widgets import Card, button, row

COLUMNS = ["Business", "Category", "Phone", "Website", "Score", "Stage"]
KIND_LABEL = {"none": "none", "social": "social only",
              "defunct": "dead builder page", "real": "has a site"}


class LeadsPage(Page):
    title = "All leads"
    hint = "Everything found so far. Double-click a row to work on it."

    def __init__(self, window, parent=None):
        super().__init__(window, parent)

        card = Card()
        controls = row()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, category or address…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.fill())
        controls.addWidget(self.search, 1)

        self.stage = QComboBox()
        self.stage.addItem("Every stage", "")
        for stage in db.STAGES:
            self.stage.addItem(stage, stage)
        self.stage.currentIndexChanged.connect(lambda _: self.fill())
        controls.addWidget(self.stage)
        controls.addWidget(button("Refresh", "quiet", self.refresh))
        card.add_layout(controls)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setSortingEnabled(True)
        self.table.setMinimumHeight(420)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for index in range(1, len(COLUMNS)):
            header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self.open_selected)
        card.add(self.table)

        self.count = card.add(QLineEdit())
        self.count.setReadOnly(True)
        self.count.setFrame(False)
        self.count.setStyleSheet("background: transparent; border: none;")
        self.body.addWidget(card)
        self.rows = []

    def refresh(self) -> None:
        self.rows = [dict(r) for r in db.leads(self.con, limit=2000)]
        self.fill()

    def fill(self) -> None:
        term = self.search.text().strip().lower()
        stage = self.stage.currentData()
        shown = [r for r in self.rows
                 if (not stage or r["stage"] == stage)
                 and (not term or term in " ".join(
                     str(r.get(k) or "") for k in ("name", "category", "address")
                 ).lower())]

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(shown))
        for index, lead in enumerate(shown):
            values = [
                lead["name"],
                lead.get("category") or "",
                lead.get("phone") or "",
                KIND_LABEL.get(lead.get("website_kind"), ""),
                lead.get("score") or 0,
                lead.get("stage") or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                if column == 4:
                    # Set as a number, so sorting by score sorts numerically
                    # rather than putting 9 after 80.
                    item.setData(Qt.DisplayRole, int(value or 0))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setText(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, lead["place_id"])
                self.table.setItem(index, column, item)
        self.table.setSortingEnabled(True)
        self.count.setText(f"{len(shown):,} of {len(self.rows):,} leads")

    def open_selected(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        place_id = self.table.item(items[0].row(), 0).data(Qt.UserRole)
        lead = db.get(self.con, place_id)
        if not lead:
            return
        from gui.actions import build_site
        if self.window_ref.confirm(
                lead["name"],
                f"Build the website for {lead['name']}?", "Build"):
            build_site(self.window_ref, dict(lead))
