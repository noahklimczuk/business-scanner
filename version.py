"""The one place the version number lives.

`pyproject.toml` reads this attribute rather than carrying its own copy, so
there is nothing to keep in sync. That matters more than it looks: the update
check compares this string against the newest GitHub release, so a version that
drifted from the released tag would either offer an update that is already
installed, forever, or hide one that is not.

A frozen build cannot read pyproject.toml — the spec does not ship it, and a
one-file build unpacks to a temp directory anyway. A Python module travels.
"""
from __future__ import annotations

VERSION = "0.2.5"
