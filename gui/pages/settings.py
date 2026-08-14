"""Settings — keys, business details, prices.

The first page a new operator will see, because without a Google key nothing
else does anything. So it is written as an explanation rather than a form: each
key says what it unlocks, what it costs, and where to get it, with a link.

Keys are masked and never written to a log. Saving writes config.json whole,
through a temporary file, at 0600.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel

import config
import generate
import prospect
from gui.pages import Page
from gui.widgets import Banner, Card, Field, button, muted, row
from gui.work import Job

PLACES_HELP = (
    "Finds the businesses. Costs about "
    f"${prospect.COST_PER_CALL:.3f} per search — a 10 km town scan is roughly "
    "$15. Get one at console.cloud.google.com: enable <b>Places API (New)</b>, "
    "not the older one, and set a daily quota cap so a mistake cannot run away."
)
COPY_HELP = (
    "Writes the words on each site — the one step that needs a second key. "
    "Everything else in this app works without it, and the copy is cached, so "
    "rebuilding a site is always free."
)
# Said once, next to the choice, because it is the thing that decides whether a
# free tier is worth it: the house rules were written against Claude, and a
# smaller model trips them more often.
PROVIDER_NOTES = {
    "anthropic": (
        "Around 3 cents per business, once. The best copy, and what this app's "
        "rules were tuned against. Key from <b>console.anthropic.com</b>."
    ),
    "gemini": (
        "Free, no card. Google sets the daily limits and changes them without "
        "notice. Key from <b>aistudio.google.com/apikey</b>."
    ),
    "groq": (
        "Free, no card. The fastest, and the plainest writing of the three. "
        "Key from <b>console.groq.com/keys</b>."
    ),
    "custom": (
        "Any service that speaks the OpenAI <code>/chat/completions</code> "
        "shape — OpenRouter, Cerebras, a local Ollama. Give it the base "
        "address (ending in <code>/v1</code>) and the exact model name."
    ),
}
FREE_TIER_CAVEAT = (
    "A free model writes acceptable copy, but trips this app's rules more "
    "often than Claude does. When a draft is rejected twice you get no site "
    "and a saved draft to edit by hand — not a wrong site."
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

        # A form is not a view of the database. Everything else in the app can
        # redraw itself from disk whenever the shell asks; this page holds work
        # in progress that exists nowhere else yet, so it tracks whether it has
        # any and refuses to overwrite it. `_loading` keeps filling the fields
        # from disk from registering as the operator typing.
        self._dirty = False
        self._loading = False

        self.banner = Banner("", "accent")
        self.banner.setVisible(False)
        self.body.addWidget(self.banner)

        keys = Card("API keys", "Each one unlocks a different part of the app.")
        self.google = Field("Google Places", "AIza…", PLACES_HELP, password=True)
        self.stripe = Field("Stripe (optional)", "sk_live_… or rk_live_…",
                            STRIPE_HELP, password=True)
        for field in (self.google, self.stripe):
            field.hint.setTextFormat(Qt.RichText)   # the help text has links
            keys.add(field)
        self.body.addWidget(keys)

        writer = Card("Copywriter", COPY_HELP)
        self.provider = QComboBox()
        for name in ("anthropic", "gemini", "groq", "custom"):
            self.provider.addItem(generate.PROVIDERS[name]["label"], name)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        writer.add(self.provider)

        self.provider_note = muted("")
        self.provider_note.setTextFormat(Qt.RichText)
        self.provider_note.setWordWrap(True)
        writer.add(self.provider_note)

        self.copy_key = Field("Key", "", "", password=True)
        self.copy_model = Field(
            "Model", "",
            "Leave blank for this service's default. Model names change — "
            "Check asks the service which ones your key can use today.")
        self.copy_url = Field(
            "Address", "https://…/v1",
            "The base URL of an OpenAI-compatible API, ending in /v1.")
        for field in (self.copy_key, self.copy_model, self.copy_url):
            writer.add(field)
        self.check_button = button("Check", "quiet", self.check_models)
        writer.box.addLayout(row(self.check_button, stretch_at=1))
        self.body.addWidget(writer)

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
            # `load`, not `refresh`: Reload is the operator asking for the
            # saved values back, so it is the one place discarding their
            # unsaved edits is what they meant.
            button("Reload", "quiet", self.load),
            stretch_at=2))
        self.body.addStretch(1)

        for field in (self.google, self.stripe, self.copy_key, self.copy_model,
                      self.copy_url, self.operator, self.legal, self.phone,
                      self.email, self.city, self.nmd_monthly,
                      self.standard_setup, self.standard_monthly):
            field.changed.connect(lambda _: self._mark_dirty())
        self.provider.currentIndexChanged.connect(lambda _: self._mark_dirty())

    def _mark_dirty(self) -> None:
        if not self._loading:
            self._dirty = True

    def _provider_changed(self) -> None:
        """Retune the fields around the chosen service."""
        name = self.provider.currentData() or generate.DEFAULT_PROVIDER
        spec = generate.PROVIDERS[name]
        note = PROVIDER_NOTES.get(name, "")
        if generate.provider_is_free(spec):
            note = f"{note}<br><br>{FREE_TIER_CAVEAT}"
        self.provider_note.setText(note)
        self.copy_key.input.setPlaceholderText(spec["key_hint"])
        self.copy_model.input.setPlaceholderText(spec["model"] or "model name")
        # Only the catch-all needs an address; the named services have one.
        self.copy_url.setVisible(name == "custom")

    def check_models(self) -> None:
        """Ask the service what this key can use, and say so.

        On a worker thread like everything else that touches the network — a
        settings page that freezes while you are still deciding whether to
        trust the tool is a bad first impression.
        """
        provider = self.provider.currentData() or generate.DEFAULT_PROVIDER
        spec = generate.resolve_provider({
            "provider": provider,
            "api_key": self.copy_key.text(),
            "model": self.copy_model.text(),
            # Same rule as save(): a stale address left in a hidden box must not
            # quietly redirect a named service somewhere else.
            "base_url": self.copy_url.text() if provider == "custom" else "",
        })
        if not spec.get("api_key"):
            self.copy_key.set_error("Paste the key first, then Check.")
            return

        def done(names: list[str]) -> None:
            if not names:
                self.provider_note.setText(
                    f"{spec['label']} accepted the key but listed no models.")
                return
            shown = "".join(f"<br>&nbsp;&nbsp;{name}" for name in names[:15])
            more = (f"<br>&nbsp;&nbsp;… and {len(names) - 15} more"
                    if len(names) > 15 else "")
            self.provider_note.setText(
                f"<b>{spec['label']} accepts this key.</b> Models you can use "
                f"today — put one in the Model box:{shown}{more}")

        self.window_ref.run_job(Job(generate.list_models, spec),
                                status="Asking the service…", on_done=done)

    def refresh(self) -> None:
        """The shell's hook: navigation, F5, and the end of every job.

        It must not throw away what the operator has typed. `run_job` refreshes
        the current page when a job finishes, and Check is a job started from
        this page — so reloading unconditionally meant that pressing Check to
        confirm a copywriter key silently blanked that key, put the service
        back to Anthropic, and took the Google Places key with it.
        """
        self.window_ref.cfg = config.load()
        if self._dirty:
            self._show_path_and_banner()
            return
        self.load()

    def load(self) -> None:
        """Fill every field from config.json, discarding anything unsaved."""
        self._loading = True
        try:
            self._load()
        finally:
            self._loading = False
        self._dirty = False

    def _load(self) -> None:
        cfg = config.load()
        self.window_ref.cfg = cfg
        business = cfg.get("business", {})
        pricing = cfg.get("pricing", {})

        copy = dict(cfg.get("copy") or {})
        legacy = (cfg.get("anthropic_api_key") or "").strip()
        if not copy.get("api_key") and legacy and not legacy.startswith("PASTE_"):
            # Written before there was a choice of service.
            copy.setdefault("provider", "anthropic")
            copy["api_key"] = legacy
        chosen = (copy.get("provider") or generate.DEFAULT_PROVIDER).lower()
        index = self.provider.findData(chosen)
        self.provider.setCurrentIndex(index if index >= 0 else 0)
        self._provider_changed()

        for field, value in (
                (self.google, cfg.get("google_api_key")),
                (self.copy_key, copy.get("api_key")),
                (self.copy_model, copy.get("model")),
                (self.copy_url, copy.get("base_url")),
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

        self._show_path_and_banner()

    def _show_path_and_banner(self) -> None:
        """Reads the form, not the file, so it is honest either way."""
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

        provider = self.provider.currentData() or generate.DEFAULT_PROVIDER
        # Whenever custom is chosen, not only when a key is already pasted:
        # without an address this setting cannot ever work, and the failure
        # would otherwise surface halfway through building a client's site.
        if provider == "custom" and not self.copy_url.text():
            self.copy_url.set_error(
                "This service needs an address. Paste one ending in /v1, or "
                "pick Gemini or Groq above — those have theirs built in.")
            return

        cfg = dict(config.load())
        cfg["google_api_key"] = self.google.text()
        cfg["stripe_secret_key"] = self.stripe.text()
        cfg["copy"] = {
            "provider": provider,
            "api_key": self.copy_key.text(),
            "model": self.copy_model.text(),
            "base_url": self.copy_url.text() if provider == "custom" else "",
        }
        # Migrated into the block above; leaving it would make the file look
        # like it still holds a key that nothing reads.
        cfg.pop("anthropic_api_key", None)
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
        # `load`, not `refresh`: the form now matches the file, and reading it
        # back is what clears the unsaved-edits flag.
        self.load()
