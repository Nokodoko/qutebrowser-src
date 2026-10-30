# SPDX-FileCopyrightText: Freya Bruhin (The Compiler) <mail@qutebrowser.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Function to return the url completion model for the `open` command."""

from collections.abc import Sequence

from qutebrowser.completion.models import (completionmodel, filepathcategory,
                                           listcategory, histcategory,
                                           BaseCategory)
from qutebrowser.browser import downloads, history
from qutebrowser.utils import log, objreg
from qutebrowser.config import config


_URLCOL = 0
_TEXTCOL = 1


def _delete_history(data):
    urlstr = data[_URLCOL]
    log.completion.debug('Deleting history entry {}'.format(urlstr))
    history.web_history.delete_url(urlstr)


def _delete_bookmark(data: Sequence[str]) -> None:
    urlstr = data[_URLCOL]
    log.completion.debug('Deleting bookmark {}'.format(urlstr))
    bookmark_manager = objreg.get('bookmark-manager')
    bookmark_manager.delete(urlstr)


def _delete_quickmark(data: Sequence[str]) -> None:
    name = data[_TEXTCOL]
    quickmark_manager = objreg.get('quickmark-manager')
    log.completion.debug('Deleting quickmark {}'.format(name))
    quickmark_manager.delete(name)


def url(*, info):
    """A model which combines various URLs.

    This combines:
    - bookmarks
    - quickmarks
    - search engines
    - web history URLs

    Used for the `open` command.
    """
    model = completionmodel.CompletionModel(column_widths=(40, 50, 10))

    quickmarks = [(url, name) for (name, url)
                  in objreg.get('quickmark-manager').marks.items()]
    bookmarks = objreg.get('bookmark-manager').marks.items()
    searchengines = [(k, v) for k, v
                     in sorted(config.val.url.searchengines.items())
                     if k != 'DEFAULT']
    categories = config.val.completion.open_categories
    models: dict[str, BaseCategory] = {}

    if searchengines and 'searchengines' in categories:
        models['searchengines'] = listcategory.ListCategory(
            'Search engines', searchengines, sort=False)

    if quickmarks and 'quickmarks' in categories:
        models['quickmarks'] = listcategory.ListCategory(
            'Quickmarks', quickmarks, delete_func=_delete_quickmark,
            sort=False)
    if bookmarks and 'bookmarks' in categories:
        models['bookmarks'] = listcategory.ListCategory(
            'Bookmarks', bookmarks, delete_func=_delete_bookmark, sort=False)

    history_disabled = info.config.get('completion.web_history.max_items') == 0
    if not history_disabled and 'history' in categories:
        hist_cat = histcategory.HistoryCategory(database=history.web_history.database,
                                                delete_func=_delete_history)
        models['history'] = hist_cat

    if 'filesystem' in categories:
        models['filesystem'] = filepathcategory.FilePathCategory(name='Filesystem')

    for category in categories:
        if category in models:
            model.add_category(models[category])

    return model


def download_dest(*, info):
    """A model which suggests filesystem paths for a download destination.

    Unlike `url`, this never suggests URLs: the argument it completes (`:download
    -d`) is a path on the machine qutebrowser is running on, and it is written
    out as-is. An empty pattern offers the configured download directory, so that
    browsing starts where downloads would have gone anyway.

    Used for the `download` command.
    """
    model = completionmodel.CompletionModel(column_widths=(100,))
    model.add_category(
        filepathcategory.FilePathCategory(
            name='Filesystem',
            initial_path=downloads.download_dir,
        )
    )
    return model
