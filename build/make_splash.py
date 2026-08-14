#!/usr/bin/env python3
"""Render build/splash.png, the image the bootloader shows on launch.

    QT_QPA_PLATFORM=offscreen python build/make_splash.py

Checked in rather than generated during the build, so packaging never depends
on Qt being importable at spec time. Re-run it if the palette changes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt                              # noqa: E402
from PySide6.QtGui import (QColor, QFont, QGuiApplication,         # noqa: E402
                           QImage, QPainter)

from gui import theme                                              # noqa: E402

WIDTH, HEIGHT = 460, 240
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "splash.png")


def render() -> str:
    QGuiApplication.instance() or QGuiApplication([])
    p = theme.DARK

    image = QImage(WIDTH, HEIGHT, QImage.Format_ARGB32)
    image.fill(QColor(p.bg))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    # A card floating on the background, so the splash reads as the same
    # object language as the app rather than a generic loading box.
    painter.setPen(QColor(p.edge))
    painter.setBrush(QColor(p.surface))
    painter.drawRoundedRect(QRectF(16, 16, WIDTH - 32, HEIGHT - 32), 10, 10)

    # The accent bar, the one piece of colour the app leads with.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(p.accent))
    painter.drawRoundedRect(QRectF(40, 62, 46, 5), 2.5, 2.5)

    painter.setPen(QColor(p.ink))
    name = QFont()
    name.setPointSize(28)
    name.setWeight(QFont.DemiBold)
    painter.setFont(name)
    painter.drawText(QRectF(40, 82, WIDTH - 80, 46), Qt.AlignLeft | Qt.AlignVCenter,
                     "Leadsmith")

    painter.setPen(QColor(p.muted))
    sub = QFont()
    sub.setPointSize(10)
    painter.setFont(sub)
    painter.drawText(QRectF(40, 126, WIDTH - 80, 22), Qt.AlignLeft | Qt.AlignVCenter,
                     "Find businesses with no website. Build them one.")
    painter.end()

    image.save(OUT, "PNG")
    return OUT


if __name__ == "__main__":
    print(render())
