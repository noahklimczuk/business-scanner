"""Find businesses — the only page that spends real money on purpose.

Every design decision here is about that. The cost is computed live as the
form changes, so it is never a surprise; the estimate is shown in the button
itself; and the confirmation dialog spells out the number again. The operator
should never be able to say "I didn't know that would cost $38".

Cell size is the setting people get wrong, so it is a named choice — "busy
main street", "suburban", "rural" — rather than a number in metres, with the
number shown beside it for anyone who wants it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout,
)

import config
import db
import prospect
from gui.pages import Page
from gui.widgets import Banner, Card, Field, Figure, button, muted

# Metres between cell centres, by what the place actually looks like. The
# number matters — searchNearby truncates silently at 20 results — but nobody
# thinks in metres, so the words lead and the metres follow.
DENSITY = [
    ("Busy main street or plaza", 900),
    ("Ordinary suburban town", 1500),
    ("Spread out / rural", 2500),
]


class FindPage(Page):
    title = "Find businesses"
    hint = ("Searches Google for businesses near a town and keeps the ones "
            "with no website. This is the one thing here that costs money, so "
            "the price is worked out before anything is sent.")

    def __init__(self, window, parent=None):
        super().__init__(window, parent)

        self.banner = Banner("", "warn")
        self.banner.setVisible(False)
        self.body.addWidget(self.banner)

        form = Card("Where to look")
        self.town = Field("Town or address", "Newmarket, ON",
                          "The centre of the search.")
        self.town.changed.connect(lambda _: self.recost())
        form.add(self.town)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)

        grid.addWidget(self._label("How far out"), 0, 0)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0.5, 50.0)      # Nearby Search caps out at 50km
        self.radius.setSingleStep(0.5)
        self.radius.setDecimals(1)
        self.radius.setSuffix(" km")
        self.radius.setValue(10.0)
        self.radius.setMinimumWidth(130)
        self.radius.valueChanged.connect(lambda _: self.recost())
        grid.addWidget(self.radius, 1, 0)

        grid.addWidget(self._label("What the area is like"), 0, 1)
        self.density = QComboBox()
        for text, metres in DENSITY:
            self.density.addItem(f"{text}  ·  {metres}m cells", metres)
        self.density.setCurrentIndex(1)
        self.density.currentIndexChanged.connect(lambda _: self.recost())
        grid.addWidget(self.density, 1, 1)
        grid.setColumnStretch(1, 1)
        form.add_layout(grid)

        form.add(muted(
            "Smaller cells find more businesses and cost more: Google returns "
            "at most 20 results per search and does not say when it truncated."))
        self.body.addWidget(form)

        estimate = Card("What this will cost")
        figures = QHBoxLayout()
        figures.setSpacing(36)
        self.cost = Figure("$0.00", "Estimate")
        self.calls = Figure("0", "Searches")
        self.cells = Figure("0", "Map cells")
        self.spent = Figure("$0.00", "Spent in 30 days")
        for figure in (self.cost, self.calls, self.cells, self.spent):
            figures.addWidget(figure)
        figures.addStretch(1)
        estimate.add_layout(figures)

        self.rate_note = muted("")
        estimate.add(self.rate_note)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.scan_button = button("Scan", "primary", self.start_scan)
        actions.addWidget(self.scan_button)
        actions.addWidget(button("Check without spending", "", self.dry_run,
                                 "Prices the scan and sends nothing"))
        actions.addStretch(1)
        estimate.add_layout(actions)
        self.body.addWidget(estimate)

        self.result = Card("Last scan")
        self.result.setVisible(False)
        self.body.addWidget(self.result)

        self.body.addStretch(1)
        self.recost()

        # Prefill from the operator's own town: the first scan is nearly always
        # where they live, and typing it again is a step for no reason.
        home = (window.cfg.get("business") or {}).get("home_city", "")
        if home:
            self.town.set_text(home)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    # -- costing -----------------------------------------------------------
    def recost(self) -> None:
        """Recompute from the form on every keystroke.

        Uses the same `prospect.grid` the scan uses rather than an
        approximation, so the number on screen is the number that will be
        charged. The centre is nominal — cell count barely moves with latitude
        in southern Ontario — and the real one is used once the town geocodes.
        """
        cells = prospect.grid(44.0, -79.5, self.radius.value() * 1000,
                              self.density.currentData())
        batches = -(-len(prospect.DEFAULT_TYPES) // prospect.TYPE_BATCH_SIZE)
        calls = len(cells) * batches
        cost = calls * prospect.COST_PER_CALL

        self.cells.set_value(f"{len(cells):,}")
        self.calls.set_value(f"{calls:,}")
        self.cost.set_value(f"${cost:,.2f}")
        self.scan_button.setText(f"Scan  ·  ${cost:,.2f}")
        self.rate_note.setText(
            f"At ${prospect.COST_PER_CALL:.3f} per search — "
            f"{len(prospect.DEFAULT_TYPES)} business types in {batches} passes "
            f"per cell. Check your Google bill after the first scan and correct "
            f"the rate if it differs.")
        self._cost = cost
        self._calls = calls

    def refresh(self) -> None:
        self.spent.set_value(f"${db.spend_since(self.con, 30):,.2f}")
        try:
            config.api_key(self.cfg)
            self.banner.setVisible(False)
            self.scan_button.setEnabled(True)
        except Exception:                                          # noqa: BLE001
            self.banner.set_message(
                "No Google Places key yet, so nothing can be searched. "
                "Add one in Settings.", "warn")
            self.banner.setVisible(True)
            self.scan_button.setEnabled(False)

    def set_busy(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy)

    # -- running -----------------------------------------------------------
    def dry_run(self) -> None:
        from gui.actions import scan
        scan(self.window_ref, self.town.text(), self.radius.value(),
             self.density.currentData(), dry_run=True, page=self)

    def start_scan(self) -> None:
        if not self.town.text():
            self.town.set_error("Which town? For example: Newmarket, ON")
            return
        from gui.actions import scan
        scan(self.window_ref, self.town.text(), self.radius.value(),
             self.density.currentData(), dry_run=False, page=self)

    def show_result(self, lines: list[tuple[str, str]], title: str = "Last scan") -> None:
        while self.result.box.count():
            item = self.result.box.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        heading = QLabel(title)
        heading.setObjectName("CardTitle")
        self.result.box.addWidget(heading)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        for r, (label, value) in enumerate(lines):
            left = QLabel(label)
            left.setObjectName("Muted")
            grid.addWidget(left, r, 0, Qt.AlignLeft)
            right = QLabel(value)
            grid.addWidget(right, r, 1, Qt.AlignLeft)
        grid.setColumnStretch(1, 1)
        self.result.box.addLayout(grid)
        self.result.setVisible(True)
