"""Settings — keys, business details, prices.

The first page a new operator will see, because without a Google key nothing
else does anything. So it is written as an explanation rather than a form: each
key says what it unlocks, what it costs, and where to get it, with a link.

Keys are masked and never written to a log. Saving writes config.json whole,
through a temporary file, at 0600.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel

import config
import prospect
from gui.pages import Page
from gui.widgets import Banner, Card, Field, button, muted, row

PLACES_HELP = (
    "Finds the businesses. Costs about "
    f"${prospect.COST_PER_CALL:.3f} per search — a 10 km town scan is roughly "
    "$15. Get one at console.cloud.google.com: enable <b>Places API (New)</b>, "
    "not the older one, and set a daily quota cap so a mistake cannot run away."
)
ANTHROPIC_HELP = (
    "Writes the words on each site. Roughly 2–4 cents per business, once — "
    "the copy is cached, so rebuilding is free. Everything else in this app "
    "works without it. Get one at console.anthropic.com."
)
STRIPE_HELP = (
    "Optional, and read-only. Lets the Money page tell you when a client's "
    "card has quietly stopped working. A restricted key with read access to "
    "subscriptions is all it needs — nothing here writes to Stripe."
)


class SettingsPage(Page):
    title = "Settings"
    hint = "Keys, your details, and what you charge. Stored on this machine only."

    def __init__(self, window, parent=None):
        super().__init__(window, parent)

        self.banner = Banner("", "accent")
        self.banner.setVisible(False)
        self.body.addWidget(self.banner)

        keys = Card("API keys", "Each one unlocks a different part of the app.")
        self.google = Field("Google Places", "AIza…", PLACES_HELP, password=True)
        self.anthropic = Field("Anthropic", "sk-ant-…", ANTHROPIC_HELP, password=True)
        self.stripe = Field("Stripe (optional)", "sk_live_… or rk_live_…",
                            STRIPE_HELP, password=True)
        for field in (self.google, self.anthropic, self.stripe):
            field.hint.setTextFormat(Qt.RichText)   # the help text has links
            keys.add(field)
        self.body.addWidget(keys)

        details = Card("Your details",
                       "These appear on the leave-behind you hand to clients.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        self.operator = Field("Your name", "Noah Klimczuk")
        self.legal = Field("Business name", "Noah Klimczuk o/a Leadsmith")
        self.phone = Field("Phone", "+1 905 555 0123")
        self.email = Field("Email", "you@example.ca")
        self.city = Field("Home town", "Newmarket, ON",
                          "Used as the default search area and in the copy.")
        grid.addWidget(self.operator, 0, 0)
        grid.addWidget(self.legal, 0, 1)
        grid.addWidget(self.phone, 1, 0)
        grid.addWidget(self.email, 1, 1)
        grid.addWidget(self.city, 2, 0)
        details.add_layout(grid)
        self.body.addWidget(details)

        prices = Card("What you charge",
                      "Shown on the leave-behind. The no-money-down offer leads, "
                      "because the objection is almost never that a website is "
                      "not worth it — it is not having the money this month.")
        price_grid = QGridLayout()
        price_grid.setHorizontalSpacing(18)
        self.nmd_monthly = Field("No money down — monthly", "149")
        self.standard_setup = Field("Up front — setup", "1200")
        self.standard_monthly = Field("Up front — monthly", "60")
        price_grid.addWidget(self.nmd_monthly, 0, 0)
        price_grid.addWidget(self.standard_setup, 0, 1)
        price_grid.addWidget(self.standard_monthly, 0, 2)
        prices.add_layout(price_grid)
        self.body.addWidget(prices)

        self.path_note = muted("")
        self.body.addWidget(self.path_note)
        self.body.addLayout(row(
            button("Save", "primary", self.save),
            button("Reload", "quiet", self.refresh),
            stretch_at=2))
        self.body.addStretch(1)

    def refresh(self) -> None:
        cfg = config.load()
        self.window_ref.cfg = cfg
        business = cfg.get("business", {})
        pricing = cfg.get("pricing", {})

        for field, value in (
                (self.google, cfg.get("google_api_key")),
                (self.anthropic, cfg.get("anthropic_api_key")),
                (self.stripe, cfg.get("stripe_secret_key")),
                (self.operator, business.get("operator_name")),
                (self.legal, business.get("legal_name")),
                (self.phone, business.get("phone")),
                (self.email, business.get("email")),
                (self.city, business.get("home_city")),
                (self.nmd_monthly, pricing.get("nmd_monthly")),
                (self.standard_setup, pricing.get("standard_setup")),
                (self.standard_monthly, pricing.get("standard_monthly"))):
            text = "" if value is None else str(value)
            # config.example.json ships placeholders; showing them as if they
            # were real keys would be a lie the operator only catches later.
            field.set_text("" if text.startswith("PASTE_") else text)

        self.path_note.setText(f"Saved to {config.CONFIG_PATH}")
        if not self.google.text():
            self.banner.set_message(
                "Start here: paste a Google Places key and you can scan your "
                "town. Everything else can wait.", "accent")
            self.banner.setVisible(True)
        else:
            self.banner.setVisible(False)

    def save(self) -> None:
        if not self.google.text():
            self.google.set_error("Without this, nothing can be searched.")
            return
        for field in (self.nmd_monthly, self.standard_setup,
                      self.standard_monthly):
            if field.text() and not field.text().replace(".", "", 1).isdigit():
                field.set_error("Numbers only — no dollar sign.")
                return

        cfg = dict(config.load())
        cfg["google_api_key"] = self.google.text()
        cfg["anthropic_api_key"] = self.anthropic.text()
        cfg["stripe_secret_key"] = self.stripe.text()
        cfg["business"] = {
            **cfg.get("business", {}),
            "operator_name": self.operator.text(),
            "legal_name": self.legal.text(),
            "phone": self.phone.text(),
            "email": self.email.text(),
            "home_city": self.city.text(),
        }
        pricing = dict(cfg.get("pricing", {}))
        for key, field in (("nmd_monthly", self.nmd_monthly),
                           ("standard_setup", self.standard_setup),
                           ("standard_monthly", self.standard_monthly)):
            if field.text():
                pricing[key] = float(field.text())
        if pricing:
            cfg["pricing"] = pricing

        try:
            path = config.save(cfg)
        except OSError as exc:
            self.window_ref.error(f"Could not save settings: {exc}")
            return

        self.window_ref.cfg = config.load()
        self.window_ref.toast(f"Saved to {path}")
        self.refresh()
