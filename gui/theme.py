"""The look.

One palette, one stylesheet, applied to the whole application. Qt's default
widgets on Windows are serviceable and dated; almost everything here is about
spacing and restraint rather than decoration — generous padding, one accent
colour, tabular numerals for anything that is money, and a sidebar that tells
you where you are without needing a title.

Light and dark both exist and follow Windows. The operator has this open all
day next to a terminal, and forcing a light window onto a dark desktop is the
kind of small rudeness that makes a tool feel foreign.
"""
from __future__ import annotations

from dataclasses import dataclass

# Windows 11 accent blue, near enough. Deliberately not the generated per-client
# accent from design.py: that colour belongs to a client's website, and having
# the operator's own tool change colour depending on which lead is selected
# would be chaos.
ACCENT = "#2f6feb"


@dataclass(frozen=True)
class Palette:
    bg: str            # window
    surface: str       # cards, panels
    surface_alt: str   # table stripes, hover
    edge: str
    ink: str
    muted: str
    accent: str
    accent_ink: str    # text on accent
    good: str
    warn: str
    bad: str
    sidebar: str
    sidebar_ink: str
    sidebar_muted: str


DARK = Palette(
    bg="#17171b", surface="#1f1f25", surface_alt="#26262d", edge="#32323b",
    ink="#f2f2f5", muted="#9c9ca6", accent=ACCENT, accent_ink="#ffffff",
    good="#3fb950", warn="#d29922", bad="#f85149",
    sidebar="#121215", sidebar_ink="#c9c9d1", sidebar_muted="#8b8b96",
)

LIGHT = Palette(
    bg="#f4f4f7", surface="#ffffff", surface_alt="#f7f7fa", edge="#e0e0e6",
    ink="#1b1b1f", muted="#5f5f68", accent=ACCENT, accent_ink="#ffffff",
    good="#1a7f37", warn="#8a6100", bad="#b3261e",
    sidebar="#20202a", sidebar_ink="#c9c9d1", sidebar_muted="#9a9aa6",
)


def palette_for(dark: bool) -> Palette:
    return DARK if dark else LIGHT


# The palette currently on screen. Set by `apply()`, so the rare widget that
# has to name a colour in Python — a table cell Qt paints itself, where a
# stylesheet cannot reach — takes it from the same two palettes everything
# else does, and inherits the contrast the tests hold those to.
CURRENT: Palette = LIGHT


def stylesheet(p: Palette) -> str:
    """The whole application's styling, as one Qt stylesheet.

    Kept in a single string rather than scattered across widgets so that
    changing how a button looks is one edit in one place — the same argument as
    the colour tokens on the generated sites.
    """
    return f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
    font-size: 14px;
    color: {p.ink};
}}
/* Qt does not inherit backgrounds down a widget tree the way a browser does,
   so every container that holds a page has to be told. Without these the
   scroll viewport renders in the default light grey and the dark theme looks
   half-applied. */
QWidget#Root, QMainWindow, QStackedWidget {{ background: {p.bg}; }}
QStackedWidget > QWidget {{ background: {p.bg}; }}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QFrame#ActivityStrip {{ background: {p.sidebar}; border-top: 1px solid {p.edge}; }}

/* ---------------------------------------------------------- sidebar ---
   The sidebar is dark in both themes, so it needs its own muted grey. The
   light theme's `--muted` is a dark grey chosen for white cards; on this
   background it measures 2.55:1, which is unreadable. Same trap the generated
   sites hit with their dark sections: a token used on a surface it was not
   designed for. */
QWidget#Sidebar {{ background: {p.sidebar}; border: none; }}
QLabel#Wordmark {{
    color: #ffffff; font-size: 17px; font-weight: 600;
    padding: 20px 20px 4px 20px; letter-spacing: -0.2px;
}}
QLabel#WordmarkSub {{
    color: {p.sidebar_muted}; font-size: 11px; padding: 0 20px 18px 20px;
    letter-spacing: 0.4px; text-transform: uppercase;
}}
QPushButton#NavButton {{
    background: transparent; border: none; border-radius: 8px;
    color: {p.sidebar_ink}; text-align: left;
    padding: 10px 14px; margin: 2px 10px; font-size: 14px;
}}
QPushButton#NavButton:hover {{ background: rgba(255,255,255,0.07); }}
QPushButton#NavButton:checked {{
    background: {p.accent}; color: #ffffff; font-weight: 600;
}}
QLabel#NavBadge {{
    color: {p.sidebar_muted}; font-size: 11px; padding: 14px 20px 8px 20px;
    text-transform: uppercase; letter-spacing: 0.6px;
}}
QLabel#SidebarFoot {{ color: {p.sidebar_muted}; font-size: 11px; padding: 12px 20px 18px; }}

/* ------------------------------------------------------------ header --- */
QLabel#PageTitle {{ font-size: 26px; font-weight: 600; letter-spacing: -0.4px; }}
QLabel#PageHint  {{ color: {p.muted}; font-size: 13px; }}

/* ------------------------------------------------------------- cards --- */
QFrame#Card {{
    background: {p.surface}; border: 1px solid {p.edge}; border-radius: 12px;
}}
QFrame#Card[accent="true"] {{ border: 1px solid {p.accent}; }}
QLabel#CardTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#CardSub   {{ color: {p.muted}; font-size: 12px; }}
QLabel#Figure {{ font-size: 28px; font-weight: 650; letter-spacing: -0.6px; }}
QLabel#FigureLabel {{
    color: {p.muted}; font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.6px;
}}
QLabel#Muted {{ color: {p.muted}; }}
QLabel#Good {{ color: {p.good}; font-weight: 600; }}
QLabel#Warn {{ color: {p.warn}; font-weight: 600; }}
QLabel#Bad  {{ color: {p.bad};  font-weight: 600; }}

/* ----------------------------------------------------------- buttons --- */
QPushButton {{
    background: {p.surface_alt}; border: 1px solid {p.edge};
    border-radius: 8px; padding: 8px 16px; min-height: 20px;
}}
QPushButton:hover {{ border-color: {p.accent}; }}
QPushButton:disabled {{ color: {p.muted}; border-color: {p.edge}; }}
QPushButton[kind="primary"] {{
    background: {p.accent}; color: {p.accent_ink}; border: 1px solid {p.accent};
    font-weight: 600;
}}
QPushButton[kind="primary"]:hover {{ background: #3f7ff5; }}
QPushButton[kind="primary"]:disabled {{ background: {p.surface_alt}; color: {p.muted};
                                        border-color: {p.edge}; }}
QPushButton[kind="danger"] {{ color: {p.bad}; }}
QPushButton[kind="danger"]:hover {{ border-color: {p.bad}; }}
QPushButton[kind="quiet"] {{ background: transparent; border-color: transparent;
                             color: {p.muted}; padding: 6px 10px; }}
QPushButton[kind="quiet"]:hover {{ color: {p.ink}; border-color: {p.edge}; }}

/* ------------------------------------------------------------ inputs --- */
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface}; border: 1px solid {p.edge}; border-radius: 8px;
    padding: 8px 10px; selection-background-color: {p.accent};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus {{ border: 1px solid {p.accent}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {p.bad}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p.surface}; border: 1px solid {p.edge};
    selection-background-color: {p.accent}; selection-color: #fff; padding: 4px;
}}
QLabel#FieldLabel {{ font-size: 12px; font-weight: 600; color: {p.muted}; }}
QLabel#FieldError {{ color: {p.bad}; font-size: 12px; }}

/* ------------------------------------------------------------ tables --- */
QTableWidget, QTableView {{
    background: {p.surface}; border: 1px solid {p.edge}; border-radius: 10px;
    gridline-color: transparent; selection-background-color: {p.accent};
    selection-color: #ffffff;
}}
QTableWidget::item, QTableView::item {{ padding: 9px 10px; border: none; }}
QHeaderView::section {{
    background: {p.surface}; color: {p.muted}; border: none;
    border-bottom: 1px solid {p.edge}; padding: 9px 10px;
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QTableCornerButton::section {{ background: {p.surface}; border: none; }}

/* -------------------------------------------------------------- misc --- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.edge}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.edge}; border-radius: 5px; min-width: 30px; }}

QProgressBar {{
    background: {p.surface_alt}; border: none; border-radius: 4px;
    height: 6px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}

QFrame#Divider {{ background: {p.edge}; max-height: 1px; border: none; }}

QLabel#Pill {{
    background: {p.surface_alt}; border: 1px solid {p.edge}; border-radius: 10px;
    padding: 3px 9px; font-size: 11px; color: {p.muted};
}}
QLabel#Pill[tone="good"] {{ color: {p.good}; border-color: {p.good}; }}
QLabel#Pill[tone="warn"] {{ color: {p.warn}; border-color: {p.warn}; }}
QLabel#Pill[tone="bad"]  {{ color: {p.bad};  border-color: {p.bad}; }}
QLabel#Pill[tone="accent"] {{ color: {p.accent}; border-color: {p.accent}; }}

QToolTip {{
    background: {p.surface}; color: {p.ink}; border: 1px solid {p.edge};
    padding: 6px 8px; border-radius: 6px;
}}

QDialog {{ background: {p.bg}; }}
QLabel#DialogTitle {{ font-size: 18px; font-weight: 600; }}
QFrame#Banner {{
    background: {p.surface_alt}; border: 1px solid {p.warn};
    border-radius: 10px;
}}
QFrame#Banner[tone="bad"] {{ border-color: {p.bad}; }}
QFrame#Banner[tone="accent"] {{ border-color: {p.accent}; }}
"""


def apply(app, dark: bool) -> Palette:
    """Stylesheet *and* QPalette.

    The stylesheet does the visible work, but a handful of things Qt draws
    itself — the text cursor, selection in a spin box, a message box's own
    background — read QPalette instead. Setting only the stylesheet leaves
    those in default light colours on a dark window, which is the tell of an
    application that was themed rather than designed.
    """
    from PySide6.QtGui import QColor, QPalette

    global CURRENT
    p = CURRENT = palette_for(dark)
    q = QPalette()
    q.setColor(QPalette.Window, QColor(p.bg))
    q.setColor(QPalette.WindowText, QColor(p.ink))
    q.setColor(QPalette.Base, QColor(p.surface))
    q.setColor(QPalette.AlternateBase, QColor(p.surface_alt))
    q.setColor(QPalette.Text, QColor(p.ink))
    q.setColor(QPalette.PlaceholderText, QColor(p.muted))
    q.setColor(QPalette.Button, QColor(p.surface_alt))
    q.setColor(QPalette.ButtonText, QColor(p.ink))
    q.setColor(QPalette.Highlight, QColor(p.accent))
    q.setColor(QPalette.HighlightedText, QColor(p.accent_ink))
    q.setColor(QPalette.ToolTipBase, QColor(p.surface))
    q.setColor(QPalette.ToolTipText, QColor(p.ink))
    q.setColor(QPalette.Link, QColor(p.accent))
    app.setPalette(q)
    app.setStyleSheet(stylesheet(p))
    return p
