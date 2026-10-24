# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A tab's icarus bar chat rides along in the session file.

Kept out of tests/unit/misc/test_sessions.py, which is skipped wholesale
without QtWebKit.
"""

import types

import pytest

from qutebrowser.browser import browsertab
from qutebrowser.misc import sessions


STATE = {'session': 'S1', 'model': 'p/m', 'entries': [['you', 'hi']]}


class FakeTab:

    """Just enough of a tab for the icarus part of _save_tab/_load_tab."""

    def __init__(self):
        self.data = browsertab.TabData()
        self.history = types.SimpleNamespace(private_api=self)
        self.history_items = None

    def load_items(self, items):
        self.history_items = items


class FakeConversation:

    def __init__(self, state):
        self._state = state

    def state(self):
        return self._state


@pytest.fixture
def sess_man(tmp_path):
    return sessions.SessionManager(base_path=str(tmp_path))


@pytest.fixture
def tab():
    tab = FakeTab()
    tab.history = []  # what _save_tab iterates
    return tab


def test_saves_the_open_conversation(sess_man, tab):
    tab.data.icarus = FakeConversation(STATE)
    assert sess_man._save_tab(tab, active=False)['icarus'] == STATE


def test_empty_conversation_saves_nothing(sess_man, tab):
    tab.data.icarus = FakeConversation(None)
    assert 'icarus' not in sess_man._save_tab(tab, active=False)


def test_unopened_restore_is_saved_again(sess_man, tab):
    tab.data.icarus_saved = STATE
    data = sess_man._save_tab(tab, active=False)
    assert data['icarus'] == STATE
    assert data['icarus'] is not STATE


def test_no_conversation(sess_man, tab):
    assert 'icarus' not in sess_man._save_tab(tab, active=False)


@pytest.mark.parametrize('saved, expected', [
    (STATE, STATE),
    ('junk', None),
    (None, None),
])
def test_load_parks_the_state(sess_man, saved, expected):
    tab = FakeTab()
    data = {'history': []}
    if saved is not None:
        data['icarus'] = saved
    sess_man._load_tab(tab, data)
    assert tab.data.icarus_saved == expected
    assert tab.history_items == []
