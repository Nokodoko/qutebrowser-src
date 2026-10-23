# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the frosted-glass menu chrome."""

import pytest
from qutebrowser.qt.core import QEvent, QRect, Qt
from qutebrowser.qt.gui import QColor, QPainter, QPixmap
from qutebrowser.qt.widgets import QMenu

from qutebrowser.mainwindow import glass


@pytest.fixture
def menu(qtbot):
    widget = QMenu()
    widget.addAction('one')
    qtbot.add_widget(widget)
    return widget


def test_blur_keeps_size_and_softens(qapp):
    pixmap = QPixmap(200, 100)
    pixmap.fill(Qt.GlobalColor.black)
    painter = QPainter(pixmap)
    painter.fillRect(0, 0, 100, 100, Qt.GlobalColor.white)
    painter.end()
    frosted = glass.blur(pixmap, 24)
    assert frosted.size() == pixmap.size()
    edge = QColor(frosted.toImage().pixel(100, 50))
    assert 0 < edge.value() < 255
    assert glass.blur(pixmap, 0) is pixmap


def test_paint_surface_rounds_corners(qapp, config_stub):
    """Inside the card is tinted; the corner outside it stays untouched."""
    config_stub.val.colors.glass.bg = '#ff0000'
    config_stub.val.colors.glass.card.bg = '#00ff00'
    pixmap = QPixmap(100, 60)
    pixmap.fill(Qt.GlobalColor.blue)
    painter = QPainter(pixmap)
    glass.paint_surface(painter, QRect(0, 0, 100, 60),
                        glass.Colors.from_config())
    painter.end()
    image = pixmap.toImage()
    assert QColor(image.pixel(50, 30)).name() == '#00ff00'
    assert QColor(image.pixel(0, 0)).name() == '#0000ff'


def test_frost_without_window_stays_empty(qtbot, config_stub, menu):
    """A widget outside any browser window has no page to frost."""
    frost = glass.Frost(menu)
    menu.show()
    frost.refresh()
    assert frost.frosted is None and frost.sharp is None


def test_menu_glass_polish(qapp, config_stub, menu):
    menu_glass = glass.MenuGlass()
    menu_glass.eventFilter(menu, QEvent(QEvent.Type.Polish))
    assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def test_menu_glass_off(qapp, config_stub, menu):
    config_stub.val.glass.enabled = False
    menu_glass = glass.MenuGlass()
    menu_glass.eventFilter(menu, QEvent(QEvent.Type.Polish))
    assert not menu.testAttribute(
        Qt.WidgetAttribute.WA_TranslucentBackground)
