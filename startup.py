"""The splash screen a frozen build shows while it is still starting.

A one-file build spends most of its startup before any of our Python runs: the
bootloader unpacks ~56MB to a temporary directory, and importing PySide6 costs
seconds more on top. Measured cold on a developer machine:

    import PySide6    6.7s
    Window()          1.3s
    everything else   0.6s

Nothing we write can draw on screen for the first two phases, because Qt is the
thing being loaded — a QSplashScreen needs the import that is taking the time.
So the splash is PyInstaller's own: drawn by the bootloader in C within a
fraction of a second of the double-click, and closed from here once the window
is up.

Every function is a no-op when running from source, where `pyi_splash` does not
exist. Nothing here may raise: a splash that fails is a cosmetic problem, and
taking the app down with it would turn it into a real one.
"""
from __future__ import annotations


def _splash():
    """The bootloader's splash module, or None if there is no splash."""
    try:
        import pyi_splash                                  # type: ignore
    except ImportError:                # running from source, or no splash
        return None
    try:
        return pyi_splash if pyi_splash.is_alive() else None
    except Exception:                                      # noqa: BLE001
        return None


def note(text: str) -> None:
    """Say what is happening, on the splash itself."""
    splash = _splash()
    if splash is None:
        return
    try:
        splash.update_text(text)
    except Exception:                                      # noqa: BLE001
        pass


def done() -> None:
    """Take the splash down. Safe to call twice, and safe to never reach.

    If startup raises before this, the process exits and the bootloader closes
    the splash with it — the failure mode is a missing window, never a splash
    left on screen over nothing.
    """
    splash = _splash()
    if splash is None:
        return
    try:
        splash.close()
    except Exception:                                      # noqa: BLE001
        pass
