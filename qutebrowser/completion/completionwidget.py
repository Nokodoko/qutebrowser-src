# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Completion view for statusbar command section.

Defines a CompletionView which uses CompletionFilterModel and CompletionModel
subclasses to provide completions.
"""

from typing import TYPE_CHECKING

from qutebrowser.qt.widgets import (QTreeView, QSizePolicy, QStyleFactory, QWidget,
                                    QStyle, QStyleOptionViewItem)
from qutebrowser.qt.core import (pyqtSlot, pyqtSignal, Qt, QItemSelectionModel,
                                 QSize, QRect, QRectF, QPoint)
from qutebrowser.qt.gui import QFontMetrics, QPainter

from qutebrowser.config import config, stylesheet
from qutebrowser.completion import completiondelegate, tabpreview
from qutebrowser.completion.models import completionmodel
from qutebrowser.mainwindow import glass
from qutebrowser.utils import utils, usertypes, debug, log, qtutils, objreg
from qutebrowser.api import cmdutils
if TYPE_CHECKING:
    from qutebrowser.mainwindow.statusbar import command


class CompletionView(QTreeView):

    """The view showing available completions.

    Based on QTreeView but heavily customized so root elements show as category
    headers, and children show as flat list.

    Attributes:
        pattern: Current filter pattern, used for highlighting.
        _win_id: The ID of the window this CompletionView is associated with.
        _height: The height to use for the CompletionView.
        _height_perc: Either None or a percentage if height should be relative.
        _delegate: The item delegate used.
        _column_widths: A list of column widths, in percent.
        _active: Whether a selection is active.
        _cmd: The statusbar Command object.
        _preview: The ivy-style pane previewing the tab under the cursor.
        _glassy: Whether the glass chrome is painted (glass.enabled).
        _previewing: Whether the current model gets the preview pane.
        _frost: The frosted page under the completion.

    Signals:
        update_geometry: Emitted when the completion should be resized.
        selection_changed: Emitted when the completion item selection changes.
        pattern_reset: Emitted when the completer must blank the token under
                       the cursor and re-filter with the empty pattern, so the
                       whole list comes back after a mark (see
                       `completion_item_mark`).
    """

    # Drawing the item foreground will be done by CompletionItemDelegate, so we
    # don't define that in this stylesheet.
    STYLESHEET = """
        QTreeView {
            font: {{ conf.fonts.completion.entry }};
            background-color: {{ conf.colors.completion.even.bg }};
            alternate-background-color: {{ conf.colors.completion.odd.bg }};
            outline: 0;
            border: 0px;
        }

        QTreeView::item:disabled {
            background-color: {{ conf.colors.completion.category.bg }};
            border-top: 1px solid
                {{ conf.colors.completion.category.border.top }};
            border-bottom: 1px solid
                {{ conf.colors.completion.category.border.bottom }};
        }

        QTreeView::item:selected, QTreeView::item:selected:hover {
            border-top: 1px solid
                {{ conf.colors.completion.item.selected.border.top }};
            border-bottom: 1px solid
                {{ conf.colors.completion.item.selected.border.bottom }};
            background-color: {{ conf.colors.completion.item.selected.bg }};
        }

        QTreeView:item::hover {
            border: 0px;
        }

        QTreeView QScrollBar {
            width: {{ conf.completion.scrollbar.width }}px;
            background: {{ conf.colors.completion.scrollbar.bg }};
        }

        QTreeView QScrollBar::handle {
            background: {{ conf.colors.completion.scrollbar.fg }};
            border: {{ conf.completion.scrollbar.padding }}px solid
                    {{ conf.colors.completion.scrollbar.bg }};
            min-height: 10px;
        }

        QTreeView QScrollBar::sub-line, QScrollBar::add-line {
            border: none;
            background: none;
        }

        /* With glass the view paints its own rows (drawRow), so the item
           rules above must not paint over them. */
        QTreeView[glass="true"] {
            /* the backdrop's frosted glass shows through the list */
            background: transparent;
            /* else the favicon gutter gets the style's highlight */
            show-decoration-selected: 0;
        }

        QTreeView[glass="true"]::item,
        QTreeView[glass="true"]::item:selected,
        QTreeView[glass="true"]::item:selected:hover,
        QTreeView[glass="true"]::item:disabled,
        QTreeView[glass="true"]::branch {
            background: transparent;
            border: 0px;
        }
    """

    update_geometry = pyqtSignal()
    selection_changed = pyqtSignal(str)
    pattern_reset = pyqtSignal()

    def __init__(self, *,
                 cmd: 'command.Command',
                 win_id: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pattern: str | None = None
        self._win_id = win_id
        self._cmd = cmd
        self._active = False
        self._glassy = False
        self._previewing = False
        self._backdrop = _Backdrop(self)
        self._frost = glass.Frost(self, self._backdrop.update)
        self._marked: list[str] = []
        self._marking = False
        self._preview = tabpreview.TabPreview(self)

        config.instance.changed.connect(self._on_config_changed)

        self._delegate = completiondelegate.CompletionItemDelegate(self)
        self.setItemDelegate(self._delegate)
        self.setStyle(QStyleFactory.create('Fusion'))
        stylesheet.set_register(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setHeaderHidden(True)
        self.setAlternatingRowColors(True)
        self.setIndentation(0)
        self.setItemsExpandable(False)
        self.setExpandsOnDoubleClick(False)
        self.setAnimated(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # WORKAROUND
        # This is a workaround for weird race conditions with invalid
        # item indexes leading to segfaults in Qt.
        #
        # Some background: https://bugs.quassel-irc.org/issues/663
        # The proposed fix there was later reverted because it didn't help.
        self.setUniformRowHeights(True)
        self.hide()
        # FIXME set elidemode
        # https://github.com/qutebrowser/qutebrowser/issues/118

    def __repr__(self):
        return utils.get_repr(self)

    def _model(self) -> completionmodel.CompletionModel:
        """Get the current completion model.

        Ensures the model is not None.
        """
        model = self.model()
        assert isinstance(model, completionmodel.CompletionModel), model
        return model

    def _selection_model(self) -> QItemSelectionModel:
        """Get the current selection model.

        Ensures the model is not None.
        """
        model = self.selectionModel()
        assert model is not None
        return model

    # Marks: picking several tabs at once from the `:tab-select` completion

    def _tab_model(self) -> completionmodel.CompletionModel | None:
        """Return the model being completed if it is a pickable tab model.

        A pickable tab model is a tab completion model whose first column
        holds the [win_id/]index of a tab, i.e. the model behind
        `:tab-select`. Any other model returns None, and marking then behaves
        exactly like plain focus.
        """
        if not self._active:
            return None
        model = self.model()
        if isinstance(model, completionmodel.CompletionModel) and \
                model.tab_indices:
            return model
        return None

    def marked(self) -> list[str]:
        """Return the marked tab indices, in the order they were marked."""
        return list(self._marked)

    def is_marked(self, tab_index: str) -> bool:
        """Whether the tab with the given [win_id/]index is marked."""
        return tab_index in self._marked

    def mark_prefix(self) -> str | None:
        """Return the command to run instead of the typed one, if marked.

        Called by the completer before it fills the commandline with the
        completion under the cursor, and by `:command-accept` before it runs
        what is in the commandline. With marks, both are replaced by
        `:tab-pick` with the marked indices in mark order, so the typed
        pattern and the marks never fight over the commandline.

        `:tab-pick` takes no flags, so nothing of the typed command carries
        over.

        Returns:
            The command to run instead of the one in the commandline, or None
            to run that as is.
        """
        if not self._marked or self._tab_model() is None:
            return None
        return 'tab-pick ' + ' '.join(self._marked)

    def keeps_pattern(self) -> bool:
        """Whether a selection change must leave the commandline alone.

        On the `:tab-select` completion the text in the commandline is the
        pattern the list is filtered by, not the item under the cursor, so
        marking must never overwrite what the user typed - whether or not a
        mark exists at that moment: the very first mark moves the focus before
        anything is marked, and unmarking the last mark moves it after none is
        marked; both would otherwise replace the pattern with the tab index
        under the cursor and destroy the filter.

        So: on a pickable tab model, any selection change happening inside a
        `:completion-item-mark` call, or while anything is marked, keeps the
        pattern. Plain `:completion-item-focus` with no marks, and any model
        which is not a pickable tab model, keep the stock behaviour of filling
        the commandline with the item under the cursor.
        """
        if self._tab_model() is None:
            return False
        return bool(self._marked) or self._marking

    def clear_marks(self) -> None:
        """Forget all marks and repaint the rows."""
        if self._marked:
            self._marked = []
            self.viewport().update()

    def _toggle_mark(self, index) -> None:
        """Toggle the mark on the row at the given index.

        Marks are keyed by the tab index in the first column rather than by
        row, so they survive the model being re-filtered while the user keeps
        typing.
        """
        # The selection can be on any column, and the view permutes the
        # columns of a tab model (see _reorder_columns), so walk the row back
        # to its first column and ask the model for its data.
        while index.column() > 0:
            index = index.siblingAtColumn(index.column() - 1)
        tab_index = self._model().data(index)
        if not tab_index:
            return
        if tab_index in self._marked:
            self._marked.remove(tab_index)
        else:
            self._marked.append(tab_index)
        log.completion.debug("completion mark {}: {}".format(
            'on' if tab_index in self._marked else 'off', tab_index))
        self.viewport().update()

    @cmdutils.register(instance='completion', maxsplit=0, scope='window')
    @cmdutils.argument('which', choices=['next', 'prev'])
    def completion_item_mark(self, which: str) -> None:
        """Mark the selected tab in the completion, or just move the focus.

        On the `:tab-select` completion this toggles a mark on the selected
        tab and moves the focus along; `:` then runs `:tab-pick` with all the
        marked tabs. Anywhere else this is the same as `:completion-item-focus`.

        Args:
            which: 'next' or 'prev'.
        """
        if self._tab_model() is None:
            self.completion_item_focus(which)
            return
        # Whether the filter had singled the marked row out, and so the
        # pattern is worth clearing once it is marked.
        single_match = self._model().count() == 1
        # The moves below change the selection, which would otherwise fill the
        # commandline with the tab index under the cursor and throw away the
        # pattern the user typed (see keeps_pattern).
        self._marking = True
        try:
            # The first move lands on the category header, which carries no tab
            # index; move onto the first item and mark that instead.
            if not self.currentIndex().parent().isValid():
                self.completion_item_focus(which)
            self._toggle_mark(self.currentIndex())
            if self.currentIndex().parent().isValid():
                # Only advance once a row is actually marked, so a Tab on an
                # empty list cannot walk past the end.
                self.completion_item_focus(which)
        finally:
            self._marking = False
        # A filter that left exactly one tab has done its job: hand the empty
        # pattern back so the user can search for the next tab without first
        # deleting anything. The marks are keyed by tab index, so they survive.
        if single_match and self._marked:
            self.pattern_reset.emit()

    @pyqtSlot(str)
    def _on_config_changed(self, option):
        if option in ['completion.height', 'completion.shrink']:
            self.update_geometry.emit()

    # ---- glass chrome and the tab picker ---------------------------------
    #
    # With glass.enabled every completion is a frosted panel holding a
    # rounded result card, with rows painted as pills (drawRow). A tab model
    # additionally gets the ivy tab picker: a preview card on the right,
    # favicons in the indentation gutter and the title before the URL.

    #: Share of the completion width the preview pane takes.
    PREVIEW_PERC = 45
    #: Logical columns of a tab model, shown title first in the picker.
    _URL_COLUMN, _TITLE_COLUMN = 1, 2

    @property
    def glass(self) -> bool:
        """Whether the view paints the glass chrome."""
        return self._glassy

    @property
    def previewing(self) -> bool:
        """Whether the view is the tab picker (list plus preview)."""
        return self._previewing

    def primary_column(self) -> int:
        """The column that reads as the item, the others recede."""
        return self._TITLE_COLUMN if self._previewing else 0

    def _set_mode(self, *, glassy: bool, previewing: bool) -> None:
        """Switch between the stock look, glass, and the tab picker."""
        self._glassy = glassy
        self._previewing = glassy and previewing
        # The cards have no room for a scrollbar; the counter tells the size.
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if glassy
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setAlternatingRowColors(not glassy)
        # The indentation is the favicon gutter, painted by drawBranches.
        self.setRootIsDecorated(not self._previewing)
        self.setIndentation(self._gutter() if self._previewing else 0)
        self.setProperty('glass', glassy)
        viewport = self.viewport()
        assert viewport is not None
        viewport.setAutoFillBackground(not glassy)
        style = self.style()
        assert style is not None
        style.unpolish(self)
        style.polish(self)
        self._order_columns()
        self._place_chrome()
        if not self._previewing:
            self._preview.clear()
        if not glassy:
            self._frost.clear()
        elif self.isVisible():
            self._frost.refresh()

    def _gutter(self) -> int:
        return QFontMetrics(self.font()).height() + 12

    def _order_columns(self) -> None:
        """Show the title before the URL in the picker, as stored otherwise."""
        header = self.header()
        assert header is not None
        if header.count() <= self._TITLE_COLUMN:
            return
        want = self._URL_COLUMN if self._previewing else self._TITLE_COLUMN
        current = header.visualIndex(self._TITLE_COLUMN)
        if current != want:
            header.moveSection(current, want)

    def _list_outer(self) -> QRect:
        """The area of the result card: all of it, or left of the preview."""
        if not self._previewing:
            return self.rect()
        width = self.width() * self.PREVIEW_PERC // 100
        return QRect(0, 0, self.width() - width, self.height())

    def _place_chrome(self) -> None:
        if not self._glassy:
            self.setViewportMargins(0, 0, 0, 0)
            self._backdrop.hide()
            self._preview.hide()
            return
        outer = self._list_outer()
        content = glass.content_rect(outer, self.font())
        self.setViewportMargins(content.left(), content.top(),
                                self.width() - content.right() - 1,
                                self.height() - content.bottom() - 1)
        self._backdrop.setGeometry(self.rect())
        self._backdrop.show()
        self._backdrop.lower()
        if self._previewing:
            self._preview.setGeometry(outer.right() + 1, 0,
                                      self.width() - outer.width(),
                                      self.height())
            self._preview.show()
            self._preview.raise_()
        else:
            self._preview.hide()

    def _card_title(self) -> str:
        """The result card's title: Tabs, or the command being completed."""
        if self._previewing:
            return 'Tabs'
        words = self._cmd.text().lstrip(':').split()
        return words[0] if words else 'Completion'

    def paint_backdrop(self, painter: QPainter) -> None:
        """Paint the panel and the cards behind the list."""
        colors = glass.Colors.from_config()
        glass.paint_panel(painter, self.rect(), colors, self._frost)
        model = self.model()
        count = ''
        if isinstance(model, completionmodel.CompletionModel):
            count = str(model.count())
            if self._previewing:
                count += f' / {self._total_tabs()}'
        glass.paint_card(painter, self._list_outer(), self.font(), colors,
                         self._card_title(), count)
        if self._previewing:
            preview_outer = self._preview.outer_rect().translated(
                self._preview.pos())
            glass.paint_card(painter, preview_outer, self._preview.font(),
                             colors, 'Preview')

    @staticmethod
    def _total_tabs() -> int:
        total = 0
        for win_id in objreg.window_registry:
            try:
                tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                            window=win_id)
            except (KeyError, objreg.RegistryUnavailableError):
                continue
            total += tabbed_browser.widget.count()
        return total

    def drawRow(self, painter, option, index):
        """With glass, paint rows as pills and categories as rules."""
        if not self._glassy:
            super().drawRow(painter, option, index)
            return
        colors = glass.Colors.from_config()
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not index.parent().isValid():
            self._draw_category(painter, rect, index, colors)
            painter.restore()
            return
        if self.row_selected(index):
            pill = rect.adjusted(2, 2, -2, -2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.selected_bg)
            painter.drawRoundedRect(pill, 7, 7)
            bar = QRectF(pill.left() + 4, pill.top() + 6, 3,
                         max(pill.height() - 12, 2))
            painter.setBrush(colors.accent)
            painter.drawRoundedRect(bar, 1.5, 1.5)
        painter.restore()
        # Qt would fill the style's highlight over the pill; the delegate
        # asks row_selected() instead.
        option = QStyleOptionViewItem(option)
        option.state &= ~QStyle.StateFlag.State_Selected
        super().drawRow(painter, option, index)

    def row_selected(self, index) -> bool:
        selmodel = self.selectionModel()
        return (selmodel is not None and
                selmodel.isRowSelected(index.row(), index.parent()))

    def _draw_category(self, painter, rect, index, colors) -> None:
        """A dim NAME · count label with a hairline after it."""
        name = str(self._model().data(index))
        if self._previewing and name.isdigit():
            name = f'window {name}'
        label = f'{name} · {self._model().rowCount(index)}'.upper()
        font = glass.title_font(self.font())
        metrics = QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(colors.dim)
        text_rect = rect.adjusted(8, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, label)
        left = text_rect.left() + metrics.horizontalAdvance(label) + 10
        if left < rect.right() - 8:
            painter.fillRect(QRectF(left, rect.center().y(),
                                    rect.right() - 8 - left, 1),
                             colors.border)

    def drawBranches(self, painter, rect, index):
        """In the picker, the indentation holds the tab's favicon."""
        if not self._glassy:
            super().drawBranches(painter, rect, index)
            return
        if not self._previewing or not index.parent().isValid():
            return
        tab = self._tab_for(str(self._model().data(index.sibling(
            index.row(), 0))))
        if tab is None:
            return
        icon = tab.icon()
        if icon.isNull():
            return
        size = min(rect.height() - 8, QFontMetrics(self.font()).height())
        target = QRect(0, 0, size, size)
        target.moveCenter(rect.center() + QPoint(4, 0))
        icon.paint(painter, target)

    def _tab_for(self, data: str):
        """The tab behind a tab completion's [win_id/]index, or None."""
        win_id, _sep, index = data.rpartition('/')
        try:
            win = int(win_id) if win_id else self._win_id
            idx = int(index) - 1
            tabbed_browser = objreg.get('tabbed-browser', scope='window',
                                        window=win)
        except (ValueError, KeyError, objreg.RegistryUnavailableError):
            return None
        if not 0 <= idx < tabbed_browser.widget.count():
            return None
        return tabbed_browser.widget.widget(idx)

    def _update_preview(self) -> None:
        """Preview the selected item, or the first one like telescope."""
        if not self._glassy or not self.isVisible():
            return
        self._backdrop.update()  # the match counter
        if not self._previewing:
            return
        model = self._model()
        idx = self._selection_model().currentIndex()
        if not idx.isValid() or not idx.parent().isValid():
            idx = model.first_item()
        if not idx.isValid():
            self._preview.clear()
            return
        data = model.data(idx.sibling(idx.row(), 0))
        tab = self._tab_for(str(data))
        if tab is not self._preview.current_tab():
            self._preview.set_tab(tab)

    def _resize_columns(self):
        """Resize the completion columns based on column_widths."""
        if self.model() is None:
            return
        width = self.viewport().width()
        column_widths = self._model().column_widths
        pixel_widths = [(width * perc // 100) for perc in column_widths]

        bar = self.verticalScrollBar()
        assert bar is not None
        if self._previewing and len(pixel_widths) > self._TITLE_COLUMN:
            # The first column also holds the favicon gutter; the title
            # gives up the room.
            gutter = self.indentation()
            pixel_widths[0] += gutter
            pixel_widths[self._TITLE_COLUMN] = max(
                pixel_widths[self._TITLE_COLUMN] - gutter, 0)
        delta = 0 if self._glassy else bar.sizeHint().width()
        for i, width in reversed(list(enumerate(pixel_widths))):
            if width > delta:
                pixel_widths[i] -= delta
                break

        for i, w in enumerate(pixel_widths):
            assert w >= 0, (i, w)
            self.setColumnWidth(i, w)

    def _next_idx(self, upwards):
        """Get the previous/next QModelIndex displayed in the view.

        Used by tab_handler.

        Args:
            upwards: Get previous item, not next.

        Return:
            A QModelIndex.
        """
        model = self._model()
        idx = self._selection_model().currentIndex()
        if not idx.isValid():
            # No item selected yet
            if upwards:
                return model.last_item()
            else:
                return model.first_item()

        while True:
            idx = self.indexAbove(idx) if upwards else self.indexBelow(idx)
            # wrap around if we arrived at beginning/end
            if not idx.isValid() and upwards:
                return model.last_item()
            elif not idx.isValid() and not upwards:
                idx = model.first_item()
                self.scrollTo(idx.parent())
                return idx
            elif idx.parent().isValid():
                # Item is a real item, not a category header -> success
                return idx

        raise utils.Unreachable

    def _next_page(self, upwards):
        """Return the index a page away from the selected index.

        Args:
            upwards: Get previous item, not next.

        Return:
            A QModelIndex.
        """
        old_idx = self._selection_model().currentIndex()
        idx = old_idx
        model = self._model()

        if not idx.isValid():
            # No item selected yet
            return model.last_item() if upwards else model.first_item()

        # Find height of each CompletionView element
        rect = self.visualRect(idx)
        qtutils.ensure_valid(rect)
        page_length = self.height() // rect.height()

        # Skip one pageful, except leave one old line visible
        offset = -(page_length - 1) if upwards else page_length - 1
        idx = model.sibling(old_idx.row() + offset, old_idx.column(), old_idx)

        # Skip category headers
        while idx.isValid() and not idx.parent().isValid():
            idx = self.indexAbove(idx) if upwards else self.indexBelow(idx)

        if idx.isValid():
            return idx

        border_item = model.first_item() if upwards else model.last_item()

        # Wrap around if we were already at the beginning/end
        if old_idx == border_item:
            return self._next_idx(upwards)

        # Select the first/last item before wrapping around
        if upwards:
            self.scrollTo(border_item.parent())
        return border_item

    def _next_category_idx(self, upwards):
        """Get the index of the previous/next category.

        Args:
            upwards: Get previous item, not next.

        Return:
            A QModelIndex.
        """
        idx = self._selection_model().currentIndex()
        model = self._model()
        if not idx.isValid():
            return self._next_idx(upwards).sibling(0, 0)
        idx = idx.parent()
        direction = -1 if upwards else 1
        while True:
            idx = idx.sibling(idx.row() + direction, 0)

            if idx.isValid():
                child = model.index(0, 0, idx)
                if child.isValid():
                    self.scrollTo(idx)  # scroll to ensure the category is visible
                    return child
            elif upwards:
                # wrap around to the first item of the last category
                return model.last_item().sibling(0, 0)
            else:
                # wrap around to the first item of the first category
                idx = model.first_item()
                self.scrollTo(idx.parent())
                return idx

        raise utils.Unreachable

    @cmdutils.register(instance='completion',
                       modes=[usertypes.KeyMode.command], scope='window')
    @cmdutils.argument('which', choices=['next', 'prev',
                                         'next-category', 'prev-category',
                                         'next-page', 'prev-page'])
    @cmdutils.argument('history', flag='H')
    def completion_item_focus(self, which, history=False):
        """Shift the focus of the completion menu to another item.

        Args:
            which: 'next', 'prev',
                   'next-category', 'prev-category',
                   'next-page', or 'prev-page'.
            history: Navigate through command history if no text was typed.
        """
        if history:
            if (self._cmd.text() == ':' or self._cmd.history.is_browsing() or
                    not self._active):
                if which == 'next':
                    self._cmd.command_history_next()
                    return
                elif which == 'prev':
                    self._cmd.command_history_prev()
                    return
                else:
                    raise cmdutils.CommandError("Can't combine --history with "
                                                "{}!".format(which))

        if not self._active:
            return

        selmodel = self._selection_model()
        indices = {
            'next': lambda: self._next_idx(upwards=False),
            'prev': lambda: self._next_idx(upwards=True),
            'next-category': lambda: self._next_category_idx(upwards=False),
            'prev-category': lambda: self._next_category_idx(upwards=True),
            'next-page': lambda: self._next_page(upwards=False),
            'prev-page': lambda: self._next_page(upwards=True),
        }
        idx = indices[which]()

        if not idx.isValid():
            return

        selmodel.setCurrentIndex(
            idx,
            QItemSelectionModel.SelectionFlag.ClearAndSelect |
            QItemSelectionModel.SelectionFlag.Rows)

        # if the last item is focused, try to fetch more
        next_idx = self.indexBelow(idx)
        if not self.visualRect(next_idx).isValid():
            self.expandAll()

        count = self._model().count()
        if count == 0:
            self.hide()
        elif count == 1 and config.val.completion.quick:
            # Quick-completing a single match hides the list, unless the user
            # is marking tabs: then the list is how they see what is marked
            # and what is left to pick, so it stays up.
            if not self.keeps_pattern():
                self.hide()
        elif config.val.completion.show == 'auto':
            self.show()

    def set_model(self, model):
        """Switch completion to a new model.

        Called from on_update_completion().

        Args:
            model: The model to use.
        """
        old_model = self.model()
        if old_model is not None and model is not old_model:
            old_model.deleteLater()

        if model is None:
            self._active = False
            self._set_mode(glassy=False, previewing=False)
            # The marks belong to the tab list they were made on; without
            # this, marks left over from `:tab-select` would hijack the next
            # completion's Enter (see mark_prefix).
            self.clear_marks()
            self.hide()
            return

        self.setModel(model)
        model.setParent(self)
        self._active = True
        self.pattern = None
        self._set_mode(glassy=glass.enabled(),
                       previewing=(model.tab_preview and
                                   config.val.completion.tab_preview))
        self._maybe_show()

        self._resize_columns()
        for i in range(model.rowCount()):
            self.expand(model.index(i, 0))
        self._update_preview()

    def set_pattern(self, pattern: str) -> None:
        """Set the pattern on the underlying model."""
        if not self.model():
            return
        if self.pattern == pattern:
            # no changes, abort
            log.completion.debug(
                "Ignoring pattern set request as pattern has not changed.")
            return
        self.pattern = pattern
        with debug.log_time(log.completion, 'Set pattern {}'.format(pattern)):
            self._model().set_pattern(pattern)
            self._selection_model().clear()
            self._maybe_update_geometry()
            self._maybe_show()
            self._update_preview()

    def _maybe_show(self):
        if (config.val.completion.show == 'always' and
                self._model().count() > 0):
            self.show()
        else:
            self.hide()

    def _maybe_update_geometry(self):
        """Emit the update_geometry signal if the config says so."""
        if config.val.completion.shrink:
            self.update_geometry.emit()

    @pyqtSlot()
    def on_clear_completion_selection(self):
        """Clear the selection model when an item is activated."""
        self.hide()
        self.clear_marks()
        selmod = self.selectionModel()
        if selmod is not None:
            selmod.clearSelection()
            selmod.clearCurrentIndex()

    def sizeHint(self):
        """Get the completion size according to the config."""
        # Get the configured height/percentage.
        confheight = str(config.val.completion.height)
        if confheight.endswith('%'):
            perc = int(confheight.rstrip('%'))
            window = self.window()
            assert window is not None
            height = window.height() * perc // 100
        else:
            height = int(confheight)
        # Shrink to content size if needed and shrinking is enabled
        if config.val.completion.shrink:
            bar = self.horizontalScrollBar()
            assert bar is not None
            contents_height = (
                self.viewportSizeHint().height() +
                bar.sizeHint().height())
            height = min(height, contents_height)
        # The width isn't really relevant as we're expanding anyways.
        return QSize(-1, height)

    def selectionChanged(self, selected, deselected):
        """Extend selectionChanged to call completers selection_changed."""
        if not self._active:
            return
        super().selectionChanged(selected, deselected)
        indexes = selected.indexes()
        if not indexes:
            return
        data = str(self._model().data(indexes[0]))
        self.selection_changed.emit(data)
        self._update_preview()

    def resizeEvent(self, e):
        """Extend resizeEvent to adjust column size."""
        super().resizeEvent(e)
        self._place_chrome()
        self._resize_columns()
        if self._glassy and self.isVisible():
            self._frost.schedule()

    def showEvent(self, e):
        """Adjust the completion size and scroll when it's freshly shown."""
        self.update_geometry.emit()
        scrollbar = self.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.minimum())
        super().showEvent(e)
        if self._glassy:
            self._frost.refresh()
        self._update_preview()

    def hideEvent(self, e):
        """Drop the previewed tab and the glass, nothing is kept around."""
        super().hideEvent(e)
        self._preview.clear()
        self._frost.clear()

    @cmdutils.register(instance='completion',
                       modes=[usertypes.KeyMode.command], scope='window')
    def completion_item_del(self):
        """Delete the current completion item."""
        index = self.currentIndex()
        if not index.isValid():
            raise cmdutils.CommandError("No item selected!")
        self._model().delete_cur_item(index)

    @cmdutils.register(instance='completion',
                       modes=[usertypes.KeyMode.command], scope='window')
    def completion_item_yank(self, sel=False):
        """Yank the current completion item into the clipboard.

        Args:
            sel: Use the primary selection instead of the clipboard.
        """
        text = self._cmd.selectedText()
        if not text:
            index = self.currentIndex()
            if not index.isValid():
                raise cmdutils.CommandError("No item selected!")
            text = self._model().data(index)

        if not utils.supports_selection():
            sel = False

        utils.set_clipboard(text, selection=sel)


class _Backdrop(QWidget):

    """Paints the glass panel and cards under the list's viewport."""

    def __init__(self, view: CompletionView) -> None:
        super().__init__(view)
        self._view = view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.hide()

    def paintEvent(self, e):
        painter = QPainter(self)
        self._view.paint_backdrop(painter)
