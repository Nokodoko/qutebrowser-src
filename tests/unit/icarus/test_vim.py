# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the icarus bar's vim NORMAL mode."""

import pytest

from qutebrowser.icarus import vim
from qutebrowser.mainwindow import icarusbar


def run(keys, text, pos=0, engine=None):
    """Feed keys like the bar does; return (text, pos, insert, scroll, undo)."""
    engine = engine or vim.Normal()
    insert, scroll, undo = False, 0, False
    for key in keys:
        out = engine.feed(key, text, pos)
        if out.edit is not None:
            start, end, new = out.edit
            text = text[:start] + new + text[end:]
        if out.pos is not None:
            pos = out.pos
        insert = insert or out.insert
        scroll = out.scroll or scroll
        undo = undo or out.history == 'undo'
        if insert:
            break
    return text, pos, insert, scroll, undo


@pytest.mark.parametrize('keys, text, pos, expected', [
    ('w', 'foo bar.baz', 0, 4),
    ('ww', 'foo bar.baz', 0, 7),
    ('W', 'foo bar.baz qux', 4, 12),
    ('e', 'foo bar', 0, 2),
    ('ee', 'foo bar', 0, 6),
    ('b', 'foo bar.baz', 8, 7),
    ('B', 'foo bar.baz', 8, 4),
    ('$', 'foo bar', 0, 6),
    ('0', 'foo bar', 5, 0),
    ('^', '   foo', 0, 3),
    ('l', 'ab', 1, 1),  # never past the last character
    ('3l', 'abcdef', 0, 3),
    ('10l', 'abcdefghijkl', 0, 10),
    ('h', 'ab\ncd', 3, 3),  # not across the line start
    ('fb', 'aabab', 0, 2),
    ('fb;', 'aabab', 0, 4),
    ('fb;,', 'aabab', 0, 2),
    ('tb', 'aaab', 0, 2),
    ('Fa', 'abab', 3, 2),
    ('Ta', 'abab', 3, 3),
    ('zz', 'abc', 1, 1),  # unknown keys do nothing
    ('dz', 'abc', 1, 1),  # nor an operator before one
])
def test_motions(keys, text, pos, expected):
    assert run(keys, text, pos)[1] == expected


def test_unknown_key_is_unhandled():
    assert not vim.Normal().feed('z', 'abc', 0).handled


def test_vertical_motion_keeps_the_column():
    text = 'abcdef\nab\nabcdef'
    assert run('j', text, 4)[1] == 8  # clamped to 'b' of 'ab'
    assert run('jj', text, 4)[1] == 14  # back to column 4, as in vim
    assert run('jlj', text, 4)[1] == 11  # a sideways move resets it
    engine = vim.Normal()
    run('j', text, 4, engine)
    engine.forget_column()
    assert run('j', text, 8, engine)[1] == 11
    assert run('k', text, 14)[1] == 8
    assert run('G', text, 2)[1] == 10
    assert run('gg', text, 12)[1] == 0


@pytest.mark.parametrize('keys, scroll', [
    ('j', 1), ('k', -1), ('G', 2), ('gg', -2),
])
def test_vertical_motion_the_input_cannot_take_scrolls(keys, scroll):
    assert run(keys, 'one line', 3)[3] == scroll


@pytest.mark.parametrize('keys, text, pos, after, cursor', [
    ('dw', 'foo bar', 0, 'bar', 0),
    ('d2w', 'a b c', 0, 'c', 0),
    ('2dw', 'a b c', 0, 'c', 0),
    ('dw', 'foo\nbar', 0, '\nbar', 0),  # stops at the end of the line
    ('de', 'foo bar', 0, ' bar', 0),
    ('d$', 'foo bar', 4, 'foo ', 3),
    ('D', 'foo bar', 4, 'foo ', 3),
    ('d0', 'foo bar', 4, 'bar', 0),
    ('dfa', 'xxaxx', 0, 'xx', 0),
    ('dtx', 'abcx', 0, 'x', 0),
    ('x', 'abc', 1, 'ac', 1),
    ('x', 'abc', 2, 'ab', 1),
    ('3x', 'abcde', 1, 'ae', 1),
    ('X', 'abc', 2, 'ac', 1),
    ('dd', 'one\ntwo\nthree', 4, 'one\nthree', 4),
    ('dd', 'one\ntwo', 4, 'one', 0),  # the last line takes its newline
    ('dd', 'one', 1, '', 0),
    ('2dd', 'a\nb\nc', 0, 'c', 0),
    ('dj', 'a\nb\nc', 0, 'c', 0),
    ('dk', 'a\nb\nc', 2, 'c', 0),
    ('dG', 'a\nb\nc', 2, 'a', 0),
    ('rX', 'abc', 1, 'aXc', 1),
    ('2rX', 'abc', 0, 'XXc', 1),
    ('5rX', 'abc', 0, 'abc', 0),  # not enough characters: nothing
])
def test_deletes(keys, text, pos, after, cursor):
    result = run(keys, text, pos)
    assert result[:2] == (after, cursor)
    assert not result[2]


@pytest.mark.parametrize('keys, text, pos, after, cursor', [
    ('cw', 'foo bar', 0, ' bar', 0),  # cw is ce
    ('cc', 'one\ntwo', 4, 'one\n', 4),
    ('S', 'one', 1, '', 0),
    ('C', 'foo bar', 4, 'foo ', 4),
    ('s', 'abc', 1, 'ac', 1),
    ('i', 'abc', 1, 'abc', 1),
    ('a', 'abc', 1, 'abc', 2),
    ('a', 'abc', 2, 'abc', 3),
    ('I', '  abc', 4, '  abc', 2),
    ('A', 'abc\nd', 0, 'abc\nd', 3),
    ('o', 'abc\nd', 1, 'abc\n\nd', 4),
    ('O', 'abc\nd', 4, 'abc\n\nd', 4),
])
def test_changes_enter_insert(keys, text, pos, after, cursor):
    result = run(keys, text, pos)
    assert result[:3] == (after, cursor, True)


def test_yank_and_put():
    engine = vim.Normal()
    text, pos, *_ = run('yyp', 'one\ntwo', 0, engine)
    assert (text, pos) == ('one\none\ntwo', 4)
    text, pos, *_ = run('P', 'x', 0, engine)
    assert (text, pos) == ('one\nx', 0)

    text, pos, *_ = run('ywP', 'foo bar', 0, engine)
    assert (text, pos) == ('foo foo bar', 3)
    text, pos, *_ = run('$p', 'ab', 0, engine)
    assert (text, pos) == ('abfoo ', 5)


def test_put_with_an_empty_register():
    assert run('p', 'abc', 1)[:2] == ('abc', 1)


def test_undo_is_the_editors():
    assert run('u', 'abc', 0)[4]


def test_pending_and_reset():
    engine = vim.Normal()
    assert not engine.pending
    engine.feed('d', 'abc', 0)
    assert engine.pending
    engine.reset()
    assert not engine.pending
    engine.feed('2', 'abc', 0)
    assert engine.pending


@pytest.mark.parametrize('text, pos, expected', [
    ('abc', 3, 2),
    ('abc', 0, 0),
    ('ab\n', 3, 3),
    ('ab\ncd', 3, 3),
])
def test_escape_pos(text, pos, expected):
    assert vim.escape_pos(text, pos) == expected


@pytest.mark.parametrize('text, index, units', [
    ('abc', 2, 2),
    ('a😀b', 2, 3),
    ('a😀b', 3, 4),
])
def test_utf16_positions(text, index, units):
    assert icarusbar._to_u16(text, index) == units
    assert icarusbar._from_u16(text, units) == index
