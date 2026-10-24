# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The icarus bar's editor split: the page it loads and the buffer it reads.

The split is icarus serve's GET /editor page in a QWebEngineView; the page
reports over its title (static/editorsplit.js) and parks the buffer on the
window for runJavaScript to read. These are the two pure seams of that
contract.
"""

import pytest

from qutebrowser.mainwindow import icarusbar


@pytest.mark.parametrize('base', ['http://127.0.0.1:7654', 'http://127.0.0.1:7654/'])
def test_editor_url_targets_serve_editor_page(base):
    url = icarusbar.editor_url(base, 'hello there\nline two')
    assert url.scheme() == 'http'
    assert url.host() == '127.0.0.1'
    assert url.port() == 7654
    assert url.path() == '/editor'
    query = url.query()
    assert 'label=prompt.md' in query
    assert 'seed=hello+there%0Aline+two' in query


def test_editor_url_label():
    url = icarusbar.editor_url('http://h:1', '', label='icarus.md')
    assert 'label=icarus.md' in url.query()
    assert url.path() == '/editor'


def test_editor_result_reads_the_parked_buffer():
    assert icarusbar.editor_result('{"text": "edited", "autoSend": false}') == ('edited', False)
    assert icarusbar.editor_result('{"text": "send", "autoSend": true}') == ('send', True)
    # QtWebEngine may hand back the decoded object rather than JSON text.
    assert icarusbar.editor_result({'text': 'obj'}) == ('obj', False)


@pytest.mark.parametrize('raw', [None, '', 'null', 'not json', '[1]', '{"autoSend": true}',
                                 '{"text": 3}'])
def test_editor_result_rejects_anything_else(raw):
    assert icarusbar.editor_result(raw) is None


def test_editor_titles_match_the_page_protocol():
    assert icarusbar.EDITOR_DONE == 'icarus-editor:done'
    assert icarusbar.EDITOR_CANCEL == 'icarus-editor:cancel'
    assert '__icarusEditorResult' in icarusbar.EDITOR_RESULT_JS
