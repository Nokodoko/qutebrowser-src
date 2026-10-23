# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from qutebrowser.qt.core import Qt, QObject, pyqtSignal
from qutebrowser.qt.gui import QKeyEvent, QKeySequence

from qutebrowser.utils import usertypes
from qutebrowser.keyinput import keyutils
from qutebrowser.misc import objects


class FakeKeyparser(QObject):

    """A fake BaseKeyParser which doesn't handle anything."""

    keystring_updated = pyqtSignal(str)
    request_leave = pyqtSignal(usertypes.KeyMode, str, bool)

    def __init__(self):
        super().__init__()
        self.passthrough = False

    def handle(
        self,
        evt: QKeyEvent,
        *,
        dry_run: bool = False,
    ) -> QKeySequence.SequenceMatch:
        return QKeySequence.SequenceMatch.NoMatch

    def clear_keystring(self):
        """Leaving a mode clears its keychain; nothing to clear here."""


@pytest.fixture
def modeman(mode_manager):
    mode_manager.register(usertypes.KeyMode.normal, FakeKeyparser())
    return mode_manager


@pytest.fixture
def tile_modeman(mode_manager):
    """A mode manager knowing every mode the tile-return tests walk through."""
    for mode in [usertypes.KeyMode.normal, usertypes.KeyMode.tile,
                 usertypes.KeyMode.hint, usertypes.KeyMode.command,
                 usertypes.KeyMode.insert, usertypes.KeyMode.passthrough,
                 usertypes.KeyMode.prompt, usertypes.KeyMode.caret]:
        mode_manager.register(mode, FakeKeyparser())
    return mode_manager


@pytest.mark.parametrize('detour', [
    usertypes.KeyMode.hint,
    usertypes.KeyMode.command,
    usertypes.KeyMode.insert,
    usertypes.KeyMode.caret,
    usertypes.KeyMode.prompt,
])
def test_detour_from_tile_returns_to_tile(tile_modeman, detour):
    tile_modeman.enter(usertypes.KeyMode.tile)
    tile_modeman.enter(detour)
    tile_modeman.leave(detour)
    assert tile_modeman.mode == usertypes.KeyMode.tile


def test_detour_from_normal_returns_to_normal(tile_modeman):
    tile_modeman.enter(usertypes.KeyMode.hint)
    tile_modeman.leave(usertypes.KeyMode.hint)
    assert tile_modeman.mode == usertypes.KeyMode.normal


def test_escape_from_tile_goes_to_normal(tile_modeman):
    tile_modeman.enter(usertypes.KeyMode.tile)
    tile_modeman.leave(usertypes.KeyMode.tile)
    assert tile_modeman.mode == usertypes.KeyMode.normal


def test_chained_detour_returns_to_tile(tile_modeman):
    """Following a hint into a text field (hint -> insert) still ends in tile."""
    tile_modeman.enter(usertypes.KeyMode.tile)
    tile_modeman.enter(usertypes.KeyMode.hint)
    tile_modeman.enter(usertypes.KeyMode.insert)
    tile_modeman.leave(usertypes.KeyMode.insert)
    assert tile_modeman.mode == usertypes.KeyMode.tile


def test_prompt_over_insert_from_tile(tile_modeman):
    """A prompt restores insert; leaving insert then returns to tile."""
    tile_modeman.enter(usertypes.KeyMode.tile)
    tile_modeman.enter(usertypes.KeyMode.insert)
    tile_modeman.enter(usertypes.KeyMode.prompt)
    tile_modeman.leave(usertypes.KeyMode.prompt)
    assert tile_modeman.mode == usertypes.KeyMode.insert
    tile_modeman.leave(usertypes.KeyMode.insert)
    assert tile_modeman.mode == usertypes.KeyMode.tile


def test_passthrough_from_tile_does_not_return(tile_modeman):
    """Engaging is not a detour: leaving passthrough lands in normal."""
    tile_modeman.enter(usertypes.KeyMode.tile)
    tile_modeman.enter(usertypes.KeyMode.passthrough)
    tile_modeman.leave(usertypes.KeyMode.passthrough)
    assert tile_modeman.mode == usertypes.KeyMode.normal


def test_only_if_normal_accepts_tile(tile_modeman):
    """Clicking a text field in tile mode enters insert, as in normal."""
    tile_modeman.enter(usertypes.KeyMode.tile)
    tile_modeman.enter(usertypes.KeyMode.insert, only_if_normal=True)
    assert tile_modeman.mode == usertypes.KeyMode.insert


def test_only_if_normal_still_refuses_other_modes(tile_modeman):
    tile_modeman.enter(usertypes.KeyMode.passthrough)
    tile_modeman.enter(usertypes.KeyMode.insert, only_if_normal=True)
    assert tile_modeman.mode == usertypes.KeyMode.passthrough


@pytest.fixture(autouse=True)
def set_qapp(monkeypatch, qapp):
    monkeypatch.setattr(objects, 'qapp', qapp)


@pytest.mark.parametrize('key, modifiers, filtered', [
    (Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, True),
    (Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier, False),
    # https://github.com/qutebrowser/qutebrowser/issues/1207
    (Qt.Key.Key_A, Qt.KeyboardModifier.ShiftModifier, True),
    (Qt.Key.Key_A, Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier, False),
])
def test_non_alphanumeric(key, modifiers, filtered, modeman):
    """Make sure non-alphanumeric keys are passed through correctly."""
    evt = keyutils.KeyInfo(key=key, modifiers=modifiers).to_event()
    assert modeman.handle_event(evt) == filtered
