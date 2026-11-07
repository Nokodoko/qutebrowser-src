# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The icarus bar's transcript follows a stream (icarusbar.Follow).

The transcript is re-rendered with setHtml on every delta. That resets a
QTextBrowser to the top, and the scroll range it leaves is an estimate the
finished layout moves a tick later, so a position applied once after the
render parked the view short of its end, then toward the top, token by token.
"""

from unittest import mock

import pytest

from qutebrowser.qt.core import QPoint, QPointF, Qt
from qutebrowser.qt.gui import QWheelEvent

from qutebrowser.icarus import conversation
from qutebrowser.mainwindow import icarusbar


@pytest.fixture
def bar(qtbot, config_stub, monkeypatch):
    monkeypatch.setattr(icarusbar, 'modeman', mock.MagicMock())
    monkeypatch.setattr(icarusbar, 'client', mock.MagicMock())
    widget = icarusbar.IcarusBar(0, None)
    qtbot.add_widget(widget)
    widget.resize(900, 400)
    widget.show()
    qtbot.wait(20)
    return widget


def _conversation(turns):
    conv = conversation.Conversation(mock.MagicMock(), 'tab')
    for i in range(turns):
        conv._append('you', f'question {i}')
        conv._append('icarus', f'answer {i} ' + 'lorem ipsum dolor sit amet ' * 8)
    return conv


def _attach(bar, conv, qtbot):
    with mock.patch.object(bar, '_tabbed_browser'):
        bar._attach(None, conv)
    qtbot.wait(20)


def _stream(conv, qtbot, tokens, *, word='token'):
    conv._append('you', 'a new question')
    conv.changed.emit()
    for i in range(tokens):
        conv._on_event({'kind': 'delta', 'text': f'{word}{i} '})
        qtbot.wait(5)


@pytest.mark.parametrize('turns', [40, 300])
def test_follows_a_stream_to_its_end(bar, qtbot, turns):
    conv = _conversation(turns)
    _attach(bar, conv, qtbot)
    scroll = bar._transcript.verticalScrollBar()
    assert scroll.maximum() > 0
    assert scroll.value() == scroll.maximum()
    _stream(conv, qtbot, 30, word='a-much-longer-streamed-word-')
    assert scroll.value() == scroll.maximum()


def test_keeps_a_scrolled_up_reader(bar, qtbot):
    conv = _conversation(40)
    _attach(bar, conv, qtbot)
    scroll = bar._transcript.verticalScrollBar()
    bar._scroll(-2)
    held = scroll.value()
    assert 0 < held < scroll.maximum()
    _stream(conv, qtbot, 20)
    assert scroll.value() == held


def test_scrolling_back_to_the_end_follows_again(bar, qtbot):
    conv = _conversation(40)
    _attach(bar, conv, qtbot)
    scroll = bar._transcript.verticalScrollBar()
    bar._scroll(-2)
    bar._scroll(1e6)  # G
    _stream(conv, qtbot, 20)
    assert scroll.value() == scroll.maximum()


def test_a_wheel_up_stops_the_follow(bar, qtbot):
    conv = _conversation(40)
    _attach(bar, conv, qtbot)
    scroll = bar._transcript.verticalScrollBar()
    viewport = bar._transcript.viewport()
    centre = QPointF(viewport.rect().center())
    wheel = QWheelEvent(centre, viewport.mapToGlobal(centre), QPoint(),
                        QPoint(0, 360), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                        False)
    bar._transcript.wheelEvent(wheel)
    held = scroll.value()
    assert 0 < held < scroll.maximum()
    _stream(conv, qtbot, 20)
    assert scroll.value() == held
