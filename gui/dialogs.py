"""The moments the app needs a decision or has something to show."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

import db
import invoice as invoice_mod
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


class InvoiceDialog(QDialog):
    """What is being billed, to whom, and for how much.

    The lines are an editable table rather than one description and one amount,
    because the second invoice anybody raises has "and the extra page" on it.
    The total is recomputed on every keystroke and shown at the bottom: the
    operator is about to hand this to somebody, and finding out what it came to
    after it printed is too late.
    """

    COLUMNS = ["What it is for", "Qty", "Unit price"]

    def __init__(self, parent, con, cfg: dict, place_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle("New invoice")
        self.setMinimumWidth(620)
        self.con, self.cfg = con, cfg
        self.settings = invoice_mod.settings_from(cfg)
        self.tax_label, self.tax_rate, _number = invoice_mod.tax_from(self.settings)

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(12)

        title = QLabel("New invoice")
        title.setObjectName("DialogTitle")
        box.addWidget(title)

        label = QLabel("Client")
        label.setObjectName("FieldLabel")
        box.addWidget(label)
        self.clients = QComboBox()
        for row in self._billable(con):
            self.clients.addItem(row["label"], row["place_id"])
        box.addWidget(self.clients)
        if place_id:
            index = self.clients.findData(place_id)
            if index >= 0:
                self.clients.setCurrentIndex(index)
        if not self.clients.count():
            box.addWidget(muted(
                "Nobody to invoice yet. A lead becomes billable once it is sold "
                "— log the sale, or record what they pay on the Money page."))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(150)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(lambda _: self.retotal())
        box.addWidget(self.table)

        box.addLayout(row_of(
            button("Add a line", "quiet", self.add_line),
            button("Remove", "quiet", self.remove_line),
            button("Use their plan", "quiet", self.fill_from_plan),
        ))

        terms = QHBoxLayout()
        terms_label = QLabel("Due in")
        terms_label.setObjectName("FieldLabel")
        terms.addWidget(terms_label)
        self.terms = QSpinBox()
        self.terms.setRange(0, 120)
        self.terms.setValue(int(self.settings["terms_days"]))
        self.terms.setSuffix(" days")
        terms.addWidget(self.terms)
        terms.addStretch(1)
        self.issue = QCheckBox("Issue it now")
        self.issue.setChecked(True)
        self.issue.setToolTip("Leave this off to keep it as a draft you can "
                              "still change.")
        terms.addWidget(self.issue)
        box.addLayout(terms)

        note_label = QLabel("Note on the invoice (optional)")
        note_label.setObjectName("FieldLabel")
        box.addWidget(note_label)
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Thanks for the referral.")
        box.addWidget(self.note_input)

        self.total = QLabel("")
        self.total.setObjectName("Figure")
        box.addWidget(self.total)
        self.problem = QLabel("")
        self.problem.setObjectName("FieldError")
        self.problem.setWordWrap(True)
        self.problem.setVisible(False)
        box.addWidget(self.problem)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button("Cancel", "", self.reject))
        self.save_button = button("Create", "primary", self.save)
        self.save_button.setDefault(True)
        buttons.addWidget(self.save_button)
        box.addLayout(buttons)

        self.clients.currentIndexChanged.connect(lambda _: self.fill_from_plan())
        self.fill_from_plan()
        if not self.clients.count():
            self.save_button.setEnabled(False)

    @staticmethod
    def _billable(con) -> list[dict[str, Any]]:
        """Clients first, then anyone else who has bought something.

        A lead nobody has sold to has nothing to be invoiced for, and offering
        the whole scan here would mean picking a client out of four hundred
        strangers.
        """
        seen, out = set(), []
        for sub in db.subscriptions(con, status="active"):
            seen.add(sub["place_id"])
            name = (sub["name"] if "name" in sub.keys() else "") or sub["place_id"]
            out.append({"place_id": sub["place_id"],
                        "label": f"{name} — ${sub['monthly'] or 0:,.0f}/mo"})
        for stage in ("sold", "live"):
            for lead in db.leads(con, stage=stage, limit=500):
                if lead["place_id"] in seen:
                    continue
                seen.add(lead["place_id"])
                out.append({"place_id": lead["place_id"], "label": lead["name"]})
        return out

    # -- lines -------------------------------------------------------------
    def place_id(self) -> str:
        return self.clients.currentData() or ""

    def add_line(self, description: str = "", quantity: str = "1",
                 unit: str = "") -> None:
        index = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(index)
        for column, value in enumerate([description, quantity, unit]):
            self.table.setItem(index, column, QTableWidgetItem(value))
        self.table.blockSignals(False)
        self.retotal()

    def remove_line(self) -> None:
        index = self.table.currentRow()
        if index < 0:
            index = self.table.rowCount() - 1
        if index >= 0:
            self.table.removeRow(index)
            self.retotal()

    def fill_from_plan(self) -> None:
        """Start from what they already pay, which is most invoices."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        sub = db.subscription(self.con, self.place_id()) if self.place_id() else None
        if sub is None or sub["status"] != "active":
            self.add_line()
            return
        for line in invoice_mod.subscription_lines(
                self.con, sub, invoice_mod.period_of()):
            self.add_line(line["description"], "1",
                          f"{line['unit_cents'] / 100:.2f}")
        if not self.table.rowCount():
            self.add_line()

    def lines(self) -> list[dict[str, Any]]:
        """The table as lines, or an InvoiceError naming the row that is wrong."""
        out = []
        for index in range(self.table.rowCount()):
            cells = [self.table.item(index, column) for column in range(3)]
            description = (cells[0].text().strip() if cells[0] else "")
            quantity_text = (cells[1].text().strip() if cells[1] else "") or "1"
            unit_text = (cells[2].text().strip() if cells[2] else "")
            if not description and not unit_text:
                continue                      # an empty row is not an error
            if not description:
                raise invoice_mod.InvoiceError(
                    f"Line {index + 1} has a price but nothing to say what it "
                    f"is for. The client reads that line.")
            try:
                quantity = float(quantity_text)
            except ValueError:
                raise invoice_mod.InvoiceError(
                    f"Line {index + 1}: '{quantity_text}' is not a quantity."
                ) from None
            if quantity <= 0:
                raise invoice_mod.InvoiceError(
                    f"Line {index + 1}: a quantity of {quantity_text} would "
                    f"bill nothing.")
            out.append({"description": description,
                        "unit_cents": invoice_mod.to_cents(unit_text),
                        "quantity": quantity, "taxable": True})
        if not out:
            raise invoice_mod.InvoiceError(
                "An invoice with no lines on it says the client owes nothing.")
        return out

    def retotal(self) -> None:
        try:
            lines = self.lines()
        except invoice_mod.InvoiceError:
            self.total.setText("—")
            return
        subtotal = sum(invoice_mod.line_cents(line["quantity"], line["unit_cents"])
                       for line in lines)
        tax = invoice_mod.tax_on(subtotal, self.tax_rate)
        text = invoice_mod.money(subtotal + tax)
        if tax:
            text += f"  ({invoice_mod.money(subtotal)} + {self.tax_label})"
        self.total.setText(text)

    # -- what the caller reads ---------------------------------------------
    def save(self) -> None:
        try:
            self._lines = self.lines()
        except invoice_mod.InvoiceError as exc:
            self.problem.setText(str(exc))
            self.problem.setVisible(True)
            return
        self.accept()

    def result_lines(self) -> list[dict[str, Any]]:
        return getattr(self, "_lines", [])

    def note(self) -> str:
        return self.note_input.text().strip()

    def terms_days(self) -> int:
        return int(self.terms.value())

    def issue_now(self) -> bool:
        return self.issue.isChecked()


class PaymentDialog(QDialog):
    """Money arrived. How much, and how.

    Prefilled with the whole balance, because that is what usually turns up;
    typing over it is how a part payment goes in.
    """

    def __init__(self, parent, number: str, balance_cents: int, name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Record a payment")
        self.setMinimumWidth(420)

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(12)

        title = QLabel(f"Payment against {number}")
        title.setObjectName("DialogTitle")
        box.addWidget(title)
        box.addWidget(muted(
            f"{name} owes {invoice_mod.money(balance_cents)}. Part payments are "
            f"fine — the invoice settles itself when the balance reaches zero."))

        amount_label = QLabel("Amount")
        amount_label.setObjectName("FieldLabel")
        box.addWidget(amount_label)
        self.amount_input = QLineEdit(f"{max(0, balance_cents) / 100:.2f}")
        box.addWidget(self.amount_input)

        how_label = QLabel("How")
        how_label.setObjectName("FieldLabel")
        box.addWidget(how_label)
        self.methods = QComboBox()
        for method in invoice_mod.METHODS:
            self.methods.addItem(method, method)
        box.addWidget(self.methods)

        reference_label = QLabel("Reference (optional)")
        reference_label.setObjectName("FieldLabel")
        box.addWidget(reference_label)
        self.reference_input = QLineEdit()
        self.reference_input.setPlaceholderText("Cheque 0142, or the e-transfer id")
        box.addWidget(self.reference_input)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button("Cancel", "", self.reject))
        save = button("Record it", "primary", self.accept)
        save.setDefault(True)
        buttons.addWidget(save)
        box.addLayout(buttons)

    def amount(self) -> str:
        return self.amount_input.text().strip()

    def method(self) -> str:
        return self.methods.currentData()

    def reference(self) -> str:
        return self.reference_input.text().strip()


def row_of(*widgets) -> QHBoxLayout:
    box = QHBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(8)
    for widget in widgets:
        box.addWidget(widget)
    box.addStretch(1)
    return box


class SpendDialog(QDialog):
    """Not used directly — the window's `confirm` covers it — but kept as the
    one place the wording of a spend prompt is defined, so scanning and copy
    generation ask the same way."""

    @staticmethod
    def text(what: str, estimate: float, spent_30d: float) -> str:
        return (f"{what}\n\n"
                f"Estimated cost: ${estimate:,.2f} USD\n"
                f"Spent in the last 30 days: ${spent_30d:,.2f}")
