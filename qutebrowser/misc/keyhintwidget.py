# SPDX-FileCopyrightText: Ryan Roden-Corrent (rcorre) <ryan@rcorre.net>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Small window that pops up to show hints for possible keystrings.

When a user inputs a key that forms a partial match, this shows a small window
with each possible completion of that keystring and the corresponding command.
It is intended to help discoverability of keybindings.
"""

import html
import re

from qutebrowser.qt.widgets import QLabel, QSizePolicy
from qutebrowser.qt.core import pyqtSlot, pyqtSignal, Qt
from qutebrowser.qt.gui import QKeySequence, QPainter

from qutebrowser.config import config, stylesheet
from qutebrowser.utils import utils, usertypes
from qutebrowser.misc import objects
from qutebrowser.mainwindow import glass
from qutebrowser.keyinput import keyutils


class KeyHintView(QLabel):

    """The view showing hints for key bindings based on the current key string.

    Attributes:
        _win_id: Window ID of parent.

    Signals:
        update_geometry: Emitted when this widget should be resized/positioned.
    """

    STYLESHEET = """
        QLabel {
            font: {{ conf.fonts.keyhint }};
            {% if conf.glass.enabled %}
                color: {{ qcolor_to_qsscolor(conf.colors.glass.fg) }};
                background: transparent;
                padding: 16px 20px;
            {% else %}
                color: {{ conf.colors.keyhint.fg }};
                background-color: {{ conf.colors.keyhint.bg }};
                padding: 6px;
                {% if conf.statusbar.position == 'top' %}
                    border-bottom-right-radius: {{ conf.keyhint.radius }}px;
                {% else %}
                    border-top-right-radius: {{ conf.keyhint.radius }}px;
                {% endif %}
            {% endif %}
        }
    """

    #: Gap between the glass card and the widget's edges.
    _CARD_MARGIN = 6
    update_geometry = pyqtSignal()

    def __init__(self, win_id, parent=None):
        super().__init__(parent)
        # Before hide() below, which sends a hideEvent.
        self._frost = glass.Frost(self)
        self.setTextFormat(Qt.TextFormat.RichText)
        self._win_id = win_id
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.hide()
        self._show_timer = usertypes.Timer(self, 'keyhint_show')
        self._show_timer.timeout.connect(self.show)
        self._show_timer.setSingleShot(True)
        stylesheet.set_register(self)

    def __repr__(self):
        return utils.get_repr(self, win_id=self._win_id)

    def showEvent(self, e):
        """Adjust the keyhint size when it's freshly shown."""
        self.update_geometry.emit()
        super().showEvent(e)
        self._frost.refresh()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._frost.clear()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if glass.enabled() and self.isVisible():
            self._frost.schedule()

    def paintEvent(self, e):
        """With glass, a floating frosted card goes under the text."""
        if glass.enabled():
            painter = QPainter(self)
            margin = self._CARD_MARGIN
            glass.paint_surface(
                painter, self.rect().adjusted(margin, margin, -margin, -margin),
                glass.Colors.from_config(), self._frost)
            painter.end()
        super().paintEvent(e)

    @pyqtSlot(usertypes.KeyMode, str)
    def update_keyhint(self, mode, prefix):
        """Show hints for the given prefix (or hide if prefix is empty).

        Args:
            mode: The key mode to show the keyhints for.
            prefix: The current partial keystring.
        """
        match = re.fullmatch(r'(\d*)(.*)', prefix)
        assert match is not None, prefix

        countstr, prefix = match.groups()
        if not prefix:
            self._show_timer.stop()
            self.hide()
            return

        def blacklisted(keychain):
            excluded = config.val.keyhint.blacklist
            return utils.match_globs(excluded, keychain) is not None

        def takes_count(cmdstr):
            """Return true iff this command can take a count argument."""
            cmdname = cmdstr.split(' ')[0]
            cmd = objects.commands.get(cmdname)
            return cmd and cmd.takes_count()

        bindings_dict = config.key_instance.get_bindings_for(mode.name)
        bindings = [
            (k, v)
            for (k, v) in sorted(bindings_dict.items())
            if keyutils.KeySequence.parse(prefix).matches(k) != QKeySequence.SequenceMatch.NoMatch
            and not blacklisted(str(k))
            and (takes_count(v) or not countstr)
        ]

        if not bindings:
            self._show_timer.stop()
            return

        # delay so a quickly typed keychain doesn't display hints
        self._show_timer.setInterval(config.val.keyhint.delay)
        self._show_timer.start()
        if glass.enabled():
            # Typed keys recede, the keys left to press stand out.
            colors = config.val.colors.glass
            prefix_style = " style='color: {}'".format(colors.dim.fg.name())
            suffix_color = colors.match.fg.name()
        else:
            prefix_style = ''
            suffix_color = html.escape(config.val.colors.keyhint.suffix.fg)

        text = ''
        for seq, cmd in bindings:
            text += (
                "<tr>"
                "<td{}>{}</td>"
                "<td style='color: {}'>{}</td>"
                "<td style='padding-left: 2ex'>{}</td>"
                "</tr>"
            ).format(
                prefix_style,
                html.escape(prefix),
                suffix_color,
                html.escape(str(seq).removeprefix(prefix)),
                html.escape(cmd)
            )
        text = '<table>{}</table>'.format(text)

        self.setText(text)
        self.adjustSize()
        self.update_geometry.emit()
