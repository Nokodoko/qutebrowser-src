# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tiling view state displayed in the statusbar.

Shows the tagset being viewed, and a ``PICK n`` marker while a transient pick
view (``TileArea.set_pick``) is on screen instead of a tagset. Add it with
``set statusbar.widgets [... tiling]``.
"""

from qutebrowser.mainwindow import tiling
from qutebrowser.mainwindow.statusbar import textbase
from qutebrowser.utils import objreg


class TilingStatus(textbase.TextBase):

    """Shows the viewed tagset, or `PICK n` while a pick view is active."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tiles = None

    def register_window(self, win_id: int) -> None:
        """Follow the tiling view of the window this bar belongs to."""
        try:
            browser = objreg.get('tabbed-browser', scope='window',
                                 window=win_id)
        except objreg.RegistryUnavailableError:
            # The window is not up yet; _draw_widgets retries.
            return
        tiles = browser.widget.tiles
        if tiles is self._tiles:
            return
        if self._tiles is not None:
            self._tiles.view_changed.disconnect(self._update)
            self._tiles.pick_changed.disconnect(self._update)
        self._tiles = tiles
        tiles.view_changed.connect(self._update)
        tiles.pick_changed.connect(self._update)
        self._update()

    def _update(self, *args) -> None:
        """Show the pick count, or the tags on view."""
        if self._tiles is None:
            self.setText('')
            return
        count = len(self._tiles.pick) if self._tiles.pick is not None else 0
        if count:
            self.setText(f'PICK {count}')
        else:
            viewed = tiling.tag_numbers(self._tiles.seltags)
            self.setText(' '.join(str(tag) for tag in viewed))
