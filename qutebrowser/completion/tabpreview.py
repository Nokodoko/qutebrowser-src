# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Telescope-ivy style preview pane for tab completions.

A tab that is on screen is grabbed live. A hidden webview cannot be painted,
so the tile area hands every tab to capture() just before hiding it, and the
preview falls back to that thumbnail. Thumbnails are scaled down when they are
taken, so showing one is a cheap scale of a small pixmap.
"""

import weakref

from qutebrowser.qt.core import Qt, QSize
from qutebrowser.qt.gui import QPixmap
from qutebrowser.qt.widgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from qutebrowser.config import config, stylesheet
from qutebrowser.utils import debug, log, utils


#: Thumbnails are scaled to fit this box when taken (about 1 MB each).
THUMB_SIZE = QSize(720, 450)

_thumbnails: 'weakref.WeakKeyDictionary[QWidget, QPixmap]' = (
    weakref.WeakKeyDictionary())


def _grab(tab: QWidget) -> QPixmap | None:
    """Grab a visible tab, scaled down to the thumbnail box."""
    if not tab.isVisible() or tab.width() <= 0 or tab.height() <= 0:
        return None
    with debug.log_time(log.misc, 'Grabbing tab preview'):
        pixmap = tab.grab()
        if pixmap.isNull():
            return None
        return pixmap.scaled(THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)


def capture(tab: QWidget) -> None:
    """Remember what a tab looks like; called before it is hidden."""
    if not config.val.completion.tab_preview:
        return
    pixmap = _grab(tab)
    if pixmap is not None:
        _thumbnails[tab] = pixmap


def snapshot(tab: QWidget) -> QPixmap | None:
    """A picture of the tab: live if it is on screen, else the last capture."""
    pixmap = _grab(tab)
    if pixmap is not None:
        _thumbnails[tab] = pixmap
        return pixmap
    return _thumbnails.get(tab)


class TabPreview(QFrame):

    """The preview pane on the right of the tab completion."""

    STYLESHEET = """
        TabPreview {
            background-color: {{ conf.colors.completion.even.bg }};
            border-left: 1px solid
                {{ conf.colors.completion.category.border.top }};
        }

        QLabel#title {
            font: {{ conf.fonts.completion.category }};
            color: {{ qcolor_to_qsscolor(conf.colors.completion.category.fg) }};
            background-color: {{ conf.colors.completion.category.bg }};
            padding: 2px 6px;
        }

        QLabel#url, QLabel#image {
            font: {{ conf.fonts.completion.entry }};
            color: {{ qcolor_to_qsscolor(conf.colors.completion.match.fg) }};
            padding: 1px 6px;
        }
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tab: weakref.ReferenceType[QWidget] | None = None
        self._pixmap: QPixmap | None = None
        self._texts = ('', '')

        self._title = QLabel(self)
        self._title.setObjectName('title')
        self._url = QLabel(self)
        self._url.setObjectName('url')
        self._image = QLabel(self)
        self._image.setObjectName('image')
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Ignored)
        for label in [self._title, self._url]:
            label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._title)
        layout.addWidget(self._url)
        layout.addWidget(self._image, 1)

        stylesheet.set_register(self)
        self.hide()

    def __repr__(self) -> str:
        return utils.get_repr(self)

    def current_tab(self) -> QWidget | None:
        return None if self._tab is None else self._tab()

    def set_tab(self, tab: QWidget | None) -> None:
        """Show the given tab (a browsertab.AbstractTab), or nothing."""
        if tab is None:
            self._tab = None
            self._pixmap = None
            self._texts = ('', '')
            self._title.clear()
            self._url.clear()
            self._image.clear()
            return

        self._tab = weakref.ref(tab)
        url = tab.url().toDisplayString()  # type: ignore[attr-defined]
        self._texts = (tab.title() or url, url)  # type: ignore[attr-defined]
        self._pixmap = snapshot(tab)
        self._render()

    def clear(self) -> None:
        self.set_tab(None)

    @staticmethod
    def _elide(label: QLabel, text: str) -> str:
        width = max(label.width() - 12, 0)
        return label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, width)

    def _render(self) -> None:
        """Fit the texts and the picture to the current size."""
        title, url = self._texts
        self._title.setText(self._elide(self._title, title))
        self._url.setText(self._elide(self._url, url))
        if self._pixmap is None:
            self._image.setText("No preview yet")
            return
        size = self._image.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        pixmap = self._pixmap
        if (pixmap.width() > size.width() or
                pixmap.height() > size.height()):
            pixmap = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        self._image.setPixmap(pixmap)

    def resizeEvent(self, e):
        """Rescale the picture and re-elide the text to the new size."""
        super().resizeEvent(e)
        if self.current_tab() is not None:
            self._render()
