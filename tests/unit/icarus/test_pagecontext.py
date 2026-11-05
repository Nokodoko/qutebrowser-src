# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The context block the page's ``icarus`` menu entry hands the bar."""

from qutebrowser.icarus import pagecontext


def test_nothing_selected_no_link_is_empty():
    assert pagecontext.block(page_url='https://x', title='X') == ''
    assert pagecontext.block(selection='   \n') == ''


def test_selection_is_fenced_with_its_page():
    got = pagecontext.block(selection=' some text ', page_url='https://x/a',
                            title='A page')
    assert got == ('Context from the browser (page A page (https://x/a)):\n'
                   'Selected text:\n```\nsome text\n```')


def test_link_with_text():
    got = pagecontext.block(link_url='https://y/b', link_text='the docs',
                            page_url='https://x')
    assert got == ('Context from the browser (page https://x):\n'
                   'Link: [the docs](https://y/b)')


def test_link_whose_text_is_the_url():
    got = pagecontext.block(link_url='https://y/b', link_text='https://y/b')
    assert got == 'Context from the browser:\nLink: https://y/b'


def test_selected_link_carries_both():
    got = pagecontext.block(selection='click me', link_url='https://y',
                            link_text='click me')
    assert 'Link: [click me](https://y)' in got
    assert 'Selected text:\n```\nclick me\n```' in got


def test_long_selection_is_clipped_on_a_utf8_boundary():
    got = pagecontext.block(selection='é' * 100, max_bytes=11)
    assert '\n' + 'é' * 5 + '\n… [truncated by the icarus context menu]' in got


def test_preview_is_the_link_and_the_selection_on_one_line():
    block = pagecontext.block(selection='one\n  two', link_url='https://y',
                              link_text='Y', page_url='https://x')
    assert pagecontext.preview(block) == 'Link: [Y](https://y) · “one two”'


def test_preview_is_clipped():
    block = pagecontext.block(selection='w' * 300)
    got = pagecontext.preview(block, limit=20)
    assert len(got) == 20 and got.endswith('…')


def test_preview_of_a_foreign_text_is_its_first_line():
    assert pagecontext.preview('just this\nnot that') == 'just this'


def test_label():
    assert pagecontext.label(selection='abc') == 'selection (3 chars)'
    assert pagecontext.label(link_url='https://y') == 'link'
    assert pagecontext.label(selection='ab', link_url='u') == 'selection (2 chars) + link'
    assert pagecontext.label() == ''
