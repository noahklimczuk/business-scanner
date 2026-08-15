"""Money — what the clients are worth, against what the tool costs to run.

Both halves on one page on purpose. The whole premise is a marginal cost of
about a dollar per client per month, and a claim nobody checks is a claim that
quietly stops being true.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem,
)

import billing
import db
import invoice as invoice_mod
from gui.pages import Page
from gui.widgets import Banner, Card, Figure, button, muted, row


class MoneyPage(Page):
    title = "Money"
    hint = "Stripe collects it. This is the record of what you are owed, and what it cost to earn."

    def __init__(self, window, parent=None):
        super().__init__(window, parent)

        self.banner = Banner("", "bad")
        self.banner.setVisible(False)
        self.body.addWidget(self.banner)

        summary = Card("Recurring revenue")
        figures = QHBoxLayout()
        figures.setSpacing(38)
        self.mrr = Figure("$0", "MRR")
        self.arr = Figure("$0", "Annualised")
        self.clients = Figure("0", "Clients")
        self.average = Figure("$0", "Average each")
        self.spend = Figure("$0.00", "API cost, 30 days")
        self.margin = Figure("$0", "MRR less cost")
        # What they are worth is not what has arrived. A client can be worth
        # $149 a month and owe four of them, and only one of those two numbers
        # pays for anything — so the money owed sits next to the money earned.
        self.owed = Figure("$0", "Owed on invoices")
        for figure in (self.mrr, self.arr, self.clients, self.average,
                       self.spend, self.margin, self.owed):
            figures.addWidget(figure)
        figures.addStretch(1)
        summary.add_layout(figures)
        summary.add(muted(
            "Setup fees are counted separately and never folded into MRR — a "
            "$1,200 setup is not $100 a month of anything."))
        self.body.addWidget(summary)

        clients = Card("Clients")
        clients.add_layout(row(
            button("Check against Stripe", "primary", self.sync),
            button("Refresh", "quiet", self.refresh),
            stretch_at=2))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Client", "Plan", "Started", "Monthly", "Stripe"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(260)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for index in range(1, 5):
            header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        clients.add(self.table)
        self.empty = muted("No paying clients yet.")
        clients.add(self.empty)
        self.body.addWidget(clients)
        self.body.addStretch(1)

    def refresh(self) -> None:
        rows = db.subscriptions(self.con)
        money = billing.summarise(rows)
        costs = billing.unit_costs(self.con)
        total_cost = costs["places"] + costs["generation"]

        self.mrr.set_value(f"${money.mrr:,.0f}")
        self.arr.set_value(f"${money.arr:,.0f}")
        self.clients.set_value(str(money.clients))
        self.average.set_value(f"${money.average:,.0f}")
        self.spend.set_value(f"${total_cost:,.2f}")
        self.margin.set_value(f"${money.mrr - total_cost:,.0f}")
        owed = invoice_mod.outstanding(self.con)
        self.owed.set_value(invoice_mod.money(owed.outstanding_cents))

        active = [dict(r) for r in rows if r["status"] == "active"]
        self.empty.setVisible(not active)
        self.table.setRowCount(len(active))
        problems = 0
        for index, sub in enumerate(active):
            stripe_status = sub.get("stripe_status")
            if not sub.get("stripe_id"):
                stripe = "not linked"
            elif stripe_status and stripe_status != "active":
                stripe = stripe_status
                problems += 1
            else:
                stripe = stripe_status or "not checked"
            values = [sub.get("name") or sub["place_id"], sub.get("plan") or "",
                      (sub.get("started_at") or "")[:10],
                      f"${sub.get('monthly') or 0:,.0f}", stripe]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(index, column, item)

        if problems:
            self.banner.set_message(
                f"{problems} subscription(s) are not being collected. "
                f"Check against Stripe.", "bad")
            self.banner.setVisible(True)
        else:
            self.banner.setVisible(False)

    def sync(self) -> None:
        from gui.actions import sync_billing

        def done(result):
            if not result.problems:
                self.window_ref.toast(
                    f"Stripe agrees with all {result.matched} of them.")
            else:
                lines = [f"{p.name}: we say {p.ours}, Stripe says {p.theirs}\n"
                         f"    {p.detail}" for p in result.problems]
                self.window_ref.error(
                    f"{len(result.problems)} disagreement(s) with Stripe.",
                    "\n\n".join(lines))
            self.refresh()

        sync_billing(self.window_ref, on_done=done)
