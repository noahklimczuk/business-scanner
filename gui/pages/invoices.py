"""Invoices — what has been billed, what has been paid, what is late.

The page answers one question the Money page cannot: MRR is what clients are
worth, and this is what has actually been asked for and what has actually
arrived. A client can be worth $149 a month for a year and owe you four of
them, and only one of those two facts pays for anything.

Overdue is worked out against today every time this redraws rather than stored,
so nothing here can be stale by a day.
"""
from __future__ import annotations

import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem,
)

import db
import invoice as invoice_mod
from gui import theme
from gui.pages import Page
from gui.widgets import Banner, Card, Figure, button, muted, row


class InvoicesPage(Page):
    title = "Invoices"
    hint = ("Raised here, printed here, paid however you and the client "
            "already agreed. Nothing is emailed and nothing takes a cut.")

    def __init__(self, window, parent=None):
        super().__init__(window, parent)

        self.banner = Banner("", "bad")
        self.banner.setVisible(False)
        self.body.addWidget(self.banner)

        summary = Card("Owed")
        figures = QHBoxLayout()
        figures.setSpacing(38)
        self.outstanding = Figure("$0", "Outstanding")
        self.overdue = Figure("$0", "Overdue")
        self.open_count = Figure("0", "Unpaid invoices")
        self.collected = Figure("$0", "Collected this year")
        for figure in (self.outstanding, self.overdue, self.open_count,
                       self.collected):
            figures.addWidget(figure)
        figures.addStretch(1)
        summary.add_layout(figures)
        summary.add(muted(
            "Outstanding is what has been issued and not yet paid. It is not "
            "MRR — that is what they are worth, this is what they owe."))
        self.body.addWidget(summary)

        listing = Card("Every invoice")
        listing.add_layout(row(
            button("New invoice", "primary", self.new_invoice),
            button("Invoice everyone for this month", "", self.monthly_run,
                   tip="Raises this month's invoice for every active client. "
                       "Anyone already invoiced is skipped, so it is safe to "
                       "press twice."),
            stretch_at=2))
        # The second row acts on whichever invoice is selected. Buttons rather
        # than a right-click menu: everything this app can do is visible.
        listing.add_layout(row(
            button("Open", "quiet", self.open_selected,
                   tip="Reprint it as it stands now, payments included."),
            button("Record a payment", "quiet", self.pay_selected),
            button("Void", "quiet", self.void_selected,
                   tip="Cancel it. It keeps its number and says why."),
            button("Refresh", "quiet", self.refresh),
            stretch_at=4))

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Invoice", "Client", "Issued", "Due", "Total", "Owing", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(320)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for index in (0, 2, 3, 4, 5, 6):
            header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(lambda _: self.open_selected())
        listing.add(self.table)
        self.empty = muted(
            "No invoices yet. Raise one against a client, or bill everybody "
            "for the month in one press.")
        listing.add(self.empty)
        self.body.addWidget(listing)
        self.body.addStretch(1)

    # -- drawing -----------------------------------------------------------
    def refresh(self) -> None:
        today = datetime.date.today()
        rows = db.invoices(self.con, limit=500)
        lines = db.lines_by_invoice(self.con, [int(r["id"]) for r in rows])
        owed = invoice_mod.outstanding(self.con, today)

        self.outstanding.set_value(invoice_mod.money(owed.outstanding_cents))
        self.overdue.set_value(invoice_mod.money(owed.overdue_cents))
        self.open_count.set_value(str(owed.open_count))
        self.collected.set_value(invoice_mod.money(self._collected(today.year)))

        self.empty.setVisible(not rows)
        self.table.setRowCount(len(rows))
        for index, invoice_row in enumerate(rows):
            sums = invoice_mod.totals(invoice_row,
                                      lines.get(int(invoice_row["id"]), []))
            sums.paid_cents = int(invoice_row["paid_cents"] or 0)
            late = invoice_mod.is_overdue(invoice_row, sums.balance_cents, today)
            owing = (invoice_mod.money(sums.balance_cents)
                     if invoice_row["status"] == "sent" and sums.balance_cents > 0
                     else "—")

            values = [invoice_row["number"], invoice_row["bill_to_name"],
                      invoice_row["issued_at"] or "draft",
                      invoice_row["due_at"] or "—",
                      invoice_mod.money(sums.total_cents), owing,
                      self._status_word(invoice_row, late, today)]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, invoice_row["number"])
                if column in (4, 5):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                # Colour is not the only signal — the last column says the word
                # as well, because a red cell means nothing to somebody who
                # cannot see red. The red itself comes from the theme, which is
                # the one the contrast tests hold to 4.5:1 on this background.
                if late and column == 5:
                    item.setForeground(QColor(theme.CURRENT.bad))
                self.table.setItem(index, column, item)

        self._sync_banner(owed)

    @staticmethod
    def _status_word(invoice_row, late: bool, today: datetime.date) -> str:
        if invoice_row["status"] == "void":
            return "void"
        if invoice_row["status"] == "paid":
            return "paid"
        if late:
            days = invoice_mod.days_overdue(invoice_row, today)
            return f"{days} day{'' if days == 1 else 's'} late"
        return invoice_row["status"]

    def _collected(self, year: int) -> int:
        row = self.con.execute(
            """SELECT COALESCE(SUM(amount_cents), 0) AS paid
               FROM invoice_payments
               WHERE CAST(strftime('%Y', at) AS INTEGER) = ?""", (year,)
        ).fetchone()
        return int(row["paid"])

    def _sync_banner(self, owed) -> None:
        if not owed.overdue_count:
            self.banner.setVisible(False)
            return
        self.banner.set_message(
            f"{owed.overdue_count} invoice(s) past due — "
            f"{invoice_mod.money(owed.overdue_cents)} outstanding. "
            f"The oldest is {owed.oldest}.", "bad")
        self.banner.setVisible(True)

    # -- what the buttons do ------------------------------------------------
    def selected(self) -> str:
        index = self.table.currentRow()
        if index < 0:
            return ""
        item = self.table.item(index, 0)
        return item.text() if item else ""

    def new_invoice(self) -> None:
        from gui.actions import new_invoice
        new_invoice(self.window_ref)

    def monthly_run(self) -> None:
        from gui.actions import monthly_run
        monthly_run(self.window_ref)

    def open_selected(self) -> None:
        from gui.actions import open_invoice
        number = self.selected()
        if not number:
            self.window_ref.toast("Pick an invoice first.")
            return
        open_invoice(self.window_ref, number)

    def pay_selected(self) -> None:
        from gui.actions import record_payment
        number = self.selected()
        if not number:
            self.window_ref.toast("Pick an invoice first.")
            return
        record_payment(self.window_ref, number)

    def void_selected(self) -> None:
        from gui.actions import void_invoice
        number = self.selected()
        if not number:
            self.window_ref.toast("Pick an invoice first.")
            return
        void_invoice(self.window_ref, number)
