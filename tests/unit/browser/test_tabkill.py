# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""`:tab-kill`: close every tab matching a pattern, after confirming."""

import pytest

from qutebrowser.qt.core import QUrl

from qutebrowser.api import cmdutils
from qutebrowser.browser import tabkill
from qutebrowser.completion.models import miscmodels
from qutebrowser.utils import message


@pytest.fixture
def browsers(fake_web_tab, tabbed_browser_stubs):
    """Two windows of tabs; each stub records what close_tab was given."""
    tabbed_browser_stubs[0].widget.tabs = [
        fake_web_tab(QUrl('https://app.ddog-gov.com/monitors'), 'Monitor List | Datadog', 0),
        fake_web_tab(QUrl('https://github.com/Nokodoko'), 'Nokodoko (n0ko)', 1),
        fake_web_tab(QUrl('https://app.ddog-gov.com/logs'), 'Logs | Datadog', 2),
    ]
    tabbed_browser_stubs[1].widget.tabs = [
        fake_web_tab(QUrl('https://app.ddog-gov.com/apm'), 'APM | Datadog', 3),
        fake_web_tab(QUrl('https://www.youtube.com/'), 'YouTube', 4),
    ]
    for stub in tabbed_browser_stubs:
        stub.closed = []
        stub.widget.indexOf = lambda tab, s=stub: (
            s.widget.tabs.index(tab) if tab in s.widget.tabs else -1)

        def close_tab(tab, *, new_undo=True, s=stub):
            s.closed.append((tab, new_undo))
            s.widget.tabs.remove(tab)

        stub.close_tab = close_tab
        stub.tab_close_prompt_if_pinned = lambda tab, force, yes_action: yes_action()
    return tabbed_browser_stubs


@pytest.fixture
def confirm(monkeypatch):
    """Capture the confirmation instead of showing it; .yes() answers it."""
    asked = {}

    def confirm_async(*, yes_action, **kwargs):
        asked['kwargs'] = kwargs
        asked['yes'] = yes_action

    monkeypatch.setattr(message, 'confirm_async', confirm_async)
    return asked


def _titles(stub):
    return [tab.title() for tab in stub.widget.tabs]


def test_matches_the_way_the_completion_filters(browsers):
    model = miscmodels.tabs()
    model.set_pattern('datadog logs')
    listed = {model.data(model.index(r, 0, model.index(c, 0)))
              for c in range(model.rowCount())
              for r in range(model.rowCount(model.index(c, 0)))}
    matched = {fields[0] for _tb, _tab, fields in tabkill._rows()
               if tabkill.matches(fields, 'datadog logs')}
    assert listed == matched == {'0/3'}


def test_closes_the_group_across_windows_after_confirming(browsers, confirm):
    tabkill.tab_kill('datadog')
    assert 'Close 3 tab(s) matching &quot;datadog&quot;?' in confirm['kwargs']['title']
    assert 'Monitor List | Datadog' in confirm['kwargs']['text']
    assert _titles(browsers[0]) == ['Monitor List | Datadog', 'Nokodoko (n0ko)',
                                    'Logs | Datadog']
    confirm['yes']()
    assert _titles(browsers[0]) == ['Nokodoko (n0ko)']
    assert _titles(browsers[1]) == ['YouTube']


def test_one_undo_entry_per_window(browsers, confirm):
    tabkill.tab_kill('datadog')
    confirm['yes']()
    assert [new for _tab, new in browsers[0].closed] == [True, False]
    assert [new for _tab, new in browsers[1].closed] == [True]


def test_yes_skips_the_confirmation(browsers, confirm):
    tabkill.tab_kill('datadog', yes=True)
    assert 'yes' not in confirm
    assert _titles(browsers[0]) == ['Nokodoko (n0ko)']


def test_a_tab_closed_meanwhile_is_skipped(browsers, confirm):
    tabkill.tab_kill('datadog')
    gone = browsers[0].widget.tabs.pop(0)
    confirm['yes']()
    assert gone not in [tab for tab, _new in browsers[0].closed]
    assert _titles(browsers[0]) == ['Nokodoko (n0ko)']


def test_pinned_tabs_are_kept_unless_forced(browsers, confirm):
    browsers[0].widget.tabs[0].data.pinned = True
    tabkill.tab_kill('datadog')
    assert 'Close 2 tab(s)' in confirm['kwargs']['title']
    confirm['yes']()
    assert _titles(browsers[0]) == ['Monitor List | Datadog', 'Nokodoko (n0ko)']
    tabkill.tab_kill('datadog', force=True, yes=True)
    assert _titles(browsers[0]) == ['Nokodoko (n0ko)']


def test_no_match_closes_nothing(browsers, confirm, message_mock):
    tabkill.tab_kill('nothing-like-this')
    assert 'yes' not in confirm
    assert message_mock.getmsg().text == 'No tab matches "nothing-like-this"'
    assert len(_titles(browsers[0])) == 3


def test_a_picked_row_closes_that_tab_alone(browsers, confirm):
    tabkill.tab_kill('1/2')
    assert 'yes' not in confirm
    assert _titles(browsers[1]) == ['APM | Datadog']
    assert _titles(browsers[0]) == ['Monitor List | Datadog', 'Nokodoko (n0ko)',
                                    'Logs | Datadog']


def test_a_picked_row_that_is_gone_is_an_error(browsers):
    with pytest.raises(cmdutils.CommandError, match="no tab 1/9"):
        tabkill.tab_kill('1/9')
    with pytest.raises(cmdutils.CommandError, match="no window 7"):
        tabkill.tab_kill('7/1')


def test_titles_are_escaped_in_the_prompt(browsers, confirm, fake_web_tab):
    browsers[0].widget.tabs.append(
        fake_web_tab(QUrl('https://x.test/'), '<b>bold</b> x', 9))
    tabkill.tab_kill('bold')
    assert '&lt;b&gt;bold&lt;/b&gt;' in confirm['kwargs']['text']
