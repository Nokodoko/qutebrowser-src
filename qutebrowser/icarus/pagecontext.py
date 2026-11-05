# SPDX-FileCopyrightText: Chris Montgomery
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the page's context menu hands the icarus bar.

Right-click on selected text or a link and pick ``icarus``: the bar opens
with a context block that is prepended to the next prompt, as the nvim chat
(icarus_chat.lua, pending_context) does with a visual selection.
"""

#: Cap on the selected text carried into a prompt (bytes, as the nvim chat).
MAX_BYTES = 24000


def _clip(text: str, max_bytes: int) -> str:
    raw = text.encode('utf-8')
    if len(raw) <= max_bytes:
        return text
    head = raw[:max_bytes].decode('utf-8', errors='ignore')
    return head + '\n… [truncated by the icarus context menu]'


def block(*, selection: str = '', link_url: str = '', link_text: str = '',
          page_url: str = '', title: str = '',
          max_bytes: int = MAX_BYTES) -> str:
    """Build the context block for a selection and/or a link.

    Returns '' when there is neither, so the caller can leave the menu alone.
    """
    selection = selection.strip()
    link_url = link_url.strip()
    if not selection and not link_url:
        return ''
    source = f'{title} ({page_url})' if title and page_url else (page_url or title)
    parts = [f'Context from the browser (page {source}):' if source
             else 'Context from the browser:']
    if link_url:
        link_text = link_text.strip()
        if link_text and link_text != link_url:
            parts.append(f'Link: [{link_text}]({link_url})')
        else:
            parts.append(f'Link: {link_url}')
    if selection:
        parts.append('Selected text:\n```\n' + _clip(selection, max_bytes) + '\n```')
    return '\n'.join(parts)


def preview(context: str, limit: int = 160) -> str:
    """One line for the transcript: the link and the start of the selection.

    context is a block(); anything else is shown as its first line.
    """
    lines = context.split('\n')
    parts = [line for line in lines if line.startswith('Link: ')]
    if 'Selected text:' in lines:
        start = lines.index('Selected text:') + 2
        body = lines[start:-1] if lines[-1] == '```' else lines[start:]
        text = ' '.join(' '.join(body).split())
        if text:
            parts.append(f'“{text}”')
    line = ' · '.join(parts) or (lines[0] if lines else '')
    return line if len(line) <= limit else line[:limit - 1] + '…'


def label(*, selection: str = '', link_url: str = '') -> str:
    """Name the context for the bar's title: what will ride along."""
    kinds = []
    if selection.strip():
        kinds.append(f'selection ({len(selection.strip())} chars)')
    if link_url.strip():
        kinds.append('link')
    return ' + '.join(kinds)
