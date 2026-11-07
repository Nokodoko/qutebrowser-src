# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The icarus bar's transcript follows a stream (icarusbar.kept_scroll).

The transcript is re-rendered with setHtml on every delta, which resets a
QTextBrowser to the top, and the working strip appearing shrinks it after the
render. Both used to leave a following transcript parked at the top for the
rest of the turn.
"""

import pytest

from qutebrowser.qt.widgets import QTextBrowser

from qutebrowser.mainwindow import icarusbar


def _html(lines):
    return ''.join(f'<p>line {i} lorem ipsum dolor sit amet</p>' for i in range(lines))


@pytest.fixture
def view(qtbot):
    browser = QTextBrowser()
    qtbot.add_widget(browser)
    browser.resize(600, 300)
    browser.show()
    browser.setHtml(_html(40))
    bar = browser.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    return browser


def test_follows_through_a_shrink_after_the_render(view):
    bar = view.verticalScrollBar()
    with icarusbar.kept_scroll(view):
        view.setHtml(_html(41))
        view.resize(600, 280)
    assert bar.value() == bar.maximum()
    for lines in range(42, 50):
        with icarusbar.kept_scroll(view):
            view.setHtml(_html(lines))
        assert bar.value() == bar.maximum()


def test_keeps_a_scrolled_up_position(view):
    bar = view.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    kept = bar.value()
    with icarusbar.kept_scroll(view):
        view.setHtml(_html(45))
    assert bar.value() == kept
    assert bar.value() < bar.maximum()
