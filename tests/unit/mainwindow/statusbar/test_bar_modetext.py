# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the mode label segment of the statusbar.

The mode label (bar.StatusBar.txt) is selected by name in the statusbar
stylesheet, so colors.statusbar.segments.mode.{bg,fg} can colour it on its own
while the other segments keep the current mode's colors.
"""

import pytest

from qutebrowser.config import stylesheet
from qutebrowser.mainwindow.statusbar import bar


@pytest.fixture
def statusbar(qtbot, config_stub, win_registry, mocker):
    """Fixture providing a StatusBar widget.

    The default statusbar.widgets includes progress, whose tab hookup needs a
    real tab; a StatusBar unit test only cares about the widgets themselves.
    """
    config_stub.val.statusbar.widgets = ['keypress']
    mocker.patch('qutebrowser.mainwindow.statusbar.bar.QTimer')
    mocker.patch('qutebrowser.mainwindow.statusbar.command.objreg.get',
                 return_value=mocker.Mock(data=mocker.Mock()))
    statusbar = bar.StatusBar(win_id=0, private=False)
    qtbot.add_widget(statusbar)
    return statusbar


def _rendered_stylesheet():
    """Render the statusbar stylesheet template with the current config."""
    return stylesheet._render_stylesheet(bar.StatusBar.STYLESHEET)


def test_mode_text_object_name(statusbar):
    """The mode label is named so the stylesheet can select it."""
    assert statusbar.txt.objectName() == 'ModeText'


def test_no_mode_rule_by_default(config_stub):
    """Without colors.statusbar.segments.mode.bg no override is emitted."""
    assert config_stub.val.colors.statusbar.segments.mode.bg is None
    assert 'ModeText' not in _rendered_stylesheet()


def test_mode_bg_rule(config_stub):
    """With segments and a mode bg, the mode label gets its own segmentBg."""
    config_stub.val.statusbar.segments = True
    config_stub.val.colors.statusbar.segments.mode.bg = '#ffd700'
    qss = _rendered_stylesheet()
    assert 'QLabel#ModeText' in qss
    assert 'qproperty-segmentBg: #ffd700;' in qss


def test_mode_fg_rule(config_stub):
    """The mode fg is emitted together with the mode bg."""
    config_stub.val.statusbar.segments = True
    config_stub.val.colors.statusbar.segments.mode.bg = '#ffd700'
    config_stub.val.colors.statusbar.segments.mode.fg = 'black'
    qss = _rendered_stylesheet()
    assert 'QLabel#ModeText' in qss
    assert 'color: black;' in qss


def test_mode_rule_specificity(config_stub):
    """The mode rule uses two IDs, so it outranks the per-flag QLabel rules.

    A per-flag rule is one ID, one attribute and two types; two IDs always win
    in Qt stylesheet specificity, so the mode label keeps its own colors in a
    flagged mode such as insert.
    """
    config_stub.val.statusbar.segments = True
    config_stub.val.colors.statusbar.insert.bg = 'green'
    config_stub.val.colors.statusbar.insert.fg = 'white'
    config_stub.val.colors.statusbar.segments.mode.bg = '#ffd700'
    config_stub.val.colors.statusbar.segments.mode.fg = 'black'
    qss = _rendered_stylesheet()
    mode_rules = [line for line in qss.splitlines() if 'ModeText' in line]
    assert mode_rules
    for rule in mode_rules:
        assert rule.count('#') == 2
    insert_rule = [line for line in qss.splitlines()
                   if 'color_flags~="insert"' in line and 'QLabel' in line]
    assert insert_rule
    assert insert_rule[0].count('#') == 1


def test_mode_fg_without_bg(config_stub):
    """Without a mode bg, the mode fg alone changes nothing."""
    config_stub.val.statusbar.segments = True
    config_stub.val.colors.statusbar.segments.mode.fg = 'black'
    assert 'ModeText' not in _rendered_stylesheet()


def test_mode_rule_without_segments(config_stub):
    """The mode override needs statusbar.segments to be emitted."""
    config_stub.val.colors.statusbar.segments.mode.bg = '#ffd700'
    assert 'ModeText' not in _rendered_stylesheet()
