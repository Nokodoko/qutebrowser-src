# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the bar's reply renderer (XML envelopes, markdown, code)."""

from qutebrowser.icarus import render


def blocks(text):
    """The parse as plain data."""
    def plain(block):
        if isinstance(block, str):
            return block
        if isinstance(block, render._Code):
            return ('code', block.lang, block.lines, block.closed)
        return (block.name, block.attrs, [plain(c) for c in block.children],
                block.closed)
    return [plain(b) for b in render._parse(text)]


def test_parse_envelopes_and_dedent():
    text = '<summary>\n  Done.\n  <evidence>\n    a.go:1\n  </evidence>\n</summary>'
    assert blocks(text) == [
        ('summary', '', ['Done.', ('evidence', '', ['a.go:1'], True)], True),
    ]


def test_parse_code_fence_wins_over_tags():
    text = '```xml\n<summary>\n</summary>\n```\nafter'
    assert blocks(text) == [('code', 'xml', ['<summary>', '</summary>'], True),
                            'after']


def test_parse_code_inside_an_envelope_keeps_its_own_indent():
    text = '<plan>\n  ```python\n  def f():\n      return 1\n  ```\n</plan>'
    assert blocks(text) == [
        ('plan', '', [('code', 'python', ['def f():', '    return 1'], True)], True),
    ]


def test_parse_streaming_envelope_and_fence():
    text = '<findings>\n  - one\n  ```go\n  x := 1'
    assert blocks(text) == [
        ('findings', '', ['- one', ('code', 'go', ['x := 1'], False)], False),
    ]


def test_parse_unbalanced_close_stays_text_and_self_closing():
    assert blocks('a\n</nope>\n<br/>') == ['a\n</nope>', ('br', '', [], True)]


def test_inline_tag_words_are_not_envelopes():
    assert blocks('use <b> for bold') == ['use <b> for bold']


def test_reply_html_tags_are_magenta_and_live_amber():
    out = render.reply_html('<summary>\n  **hi**\n</summary>')
    assert f'color:{render.XML_TAG}' in out
    assert '&lt;summary&gt;' in out and '&lt;/summary&gt;' in out
    assert 'margin-left:16px' in out
    assert 'hi' in out and 'font-weight' in out  # markdown bold survived

    live = render.reply_html('<summary>\n  still going')
    assert f'color:{render.AMBER}' in live
    assert '&lt;/summary&gt;' not in live


def test_reply_html_escapes_model_markup():
    out = render.reply_html('<script>alert(1)</script>\n\n```html\n<script>x</script>\n```')
    assert '<script>' not in out


def test_highlight_uses_the_web_ui_colours():
    out = render.highlight('def f():\n    return "s"  # c', 'python').lower()
    assert render.PURPLE in out  # keyword
    assert render.GREEN in out   # string
    assert render.DIM in out     # comment
    assert render.highlight('<x>', 'nosuchlang') == '&lt;x&gt;'
    assert render.highlight('<x>', '') == '&lt;x&gt;'
