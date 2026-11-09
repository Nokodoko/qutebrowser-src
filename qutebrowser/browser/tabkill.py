# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Close tabs by pattern: the group kill behind `:tab-kill`.

A port of the Vivaldi tabkill.py: a pattern takes every tab whose title or URL
holds it, the matches are confirmed, and the group closes at once. The
argument completes on the tab list and is matched the way that list filters,
so the menu shows exactly the tabs Enter would close. A row picked from the
menu (its `win_id/index`) closes that one tab.
"""

import html
import re
from collections.abc import Iterator, Sequence

from qutebrowser.api import cmdutils
from qutebrowser.completion.models import miscmodels
from qutebrowser.utils import message, objreg

#: A `win_id/index` row picked from the tab completion.
_PICKED = re.compile(r'(\d+)/(\d+)')

#: How many matches the confirmation lists before summing up the rest.
PREVIEW_ROWS = 8


def matches(fields: Sequence[str], pattern: str) -> bool:
    """Whether the tab completion lists a tab row for pattern.

    The completion keeps a row when one of its columns holds every term of the
    pattern, case-insensitively and in any order (ListCategory.set_pattern).
    """
    terms = pattern.lower().split()
    return any(all(term in field.lower() for term in terms) for field in fields)


def _rows() -> Iterator[tuple[object, object, tuple[str, str, str, str]]]:
    """Every open tab with its completion row (miscmodels._tabs's columns)."""
    for win_id in objreg.window_registry:
        tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                    window=win_id)
        if tabbed_browser.is_shutting_down:
            continue
        for idx in range(tabbed_browser.widget.count()):
            tab = tabbed_browser.widget.widget(idx)
            pid = tab.renderer_process_pid()
            yield tabbed_browser, tab, (
                f'{win_id}/{idx + 1}',
                tab.url().toDisplayString(),
                tabbed_browser.widget.page_title(idx),
                '' if pid is None else f'PID {pid}',
            )


def _close(victims: list[tuple[object, object]]) -> int:
    """Close victims still open; one undo entry per window restores its share."""
    undone: set[int] = set()
    closed = 0
    for tabbed_browser, tab in victims:
        if tabbed_browser.is_shutting_down or tabbed_browser.widget.indexOf(tab) == -1:
            continue
        tabbed_browser.close_tab(tab, new_undo=id(tabbed_browser) not in undone)
        undone.add(id(tabbed_browser))
        closed += 1
    return closed


def _close_picked(win_id: int, index: int, force: bool) -> None:
    try:
        tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                    window=win_id)
    except objreg.RegistryUnavailableError:
        raise cmdutils.CommandError(f"There's no window {win_id}")
    if not 1 <= index <= tabbed_browser.widget.count():
        raise cmdutils.CommandError(f"There's no tab {win_id}/{index}")
    tab = tabbed_browser.widget.widget(index - 1)
    tabbed_browser.tab_close_prompt_if_pinned(
        tab, force, lambda: tabbed_browser.close_tab(tab))


@cmdutils.register(maxsplit=0)
@cmdutils.argument('pattern', completion=miscmodels.tabs)
def tab_kill(pattern: str, force: bool = False, yes: bool = False) -> None:
    """Close every tab matching a pattern, across all windows.

    A tab matches when its title, URL or index holds every word of the
    pattern, in any order and ignoring case: the tabs the completion lists for
    it. The matches are confirmed first and `:undo` reopens them, one window
    at a time. Picking a row from the completion closes that tab alone.

    Args:
        pattern: The words to match, or the `win_id/index` of a picked tab.
        force: Close matching pinned tabs too; they are kept otherwise.
        yes: Close without asking.
    """
    picked = _PICKED.fullmatch(pattern.strip())
    if picked:
        _close_picked(int(picked.group(1)), int(picked.group(2)), force)
        return
    if not pattern.strip():
        raise cmdutils.CommandError("Usage: :tab-kill <pattern>")

    matched = [(tb, tab, fields) for tb, tab, fields in _rows()
               if matches(fields, pattern)]
    if not matched:
        message.info(f'No tab matches "{pattern}"')
        return
    pinned = sum(1 for _tb, tab, _fields in matched if tab.data.pinned)
    if not force:
        matched = [m for m in matched if not m[1].data.pinned]
    kept = '' if force or not pinned else f', kept {pinned} pinned (--force closes them)'
    if not matched:
        message.info(f'Only pinned tabs match "{pattern}"{kept}')
        return
    victims = [(tb, tab) for tb, tab, _fields in matched]

    def close() -> None:
        closed = _close(victims)
        message.info(f'Closed {closed} tab(s) matching "{pattern}"{kept}; '
                     ':undo reopens them')

    if yes:
        close()
        return
    titles = [html.escape(fields[2] or fields[1]) for _tb, _tab, fields in matched]
    if len(titles) > PREVIEW_ROWS:
        titles[PREVIEW_ROWS:] = [f'... and {len(titles) - PREVIEW_ROWS} more']
    message.confirm_async(
        title=html.escape(f'Close {len(victims)} tab(s) matching "{pattern}"?'),
        text='<br/>'.join(titles),
        yes_action=close, default=True)
