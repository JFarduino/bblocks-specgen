"""
ATX headings (# ... through ###### ...) of a Markdown text, ignoring lines
inside fenced code blocks (``` or ~~~), where '#' starts a comment.
"""
from __future__ import annotations

import re

# Matches ATX headings in markdown (# ... through ###### ...)
HEADING_LINE_RE = re.compile(r'^(#{1,6})[ \t]+(.+)', re.MULTILINE)
_FENCE_RE = re.compile(r'^[ ]{0,3}(`{3,}|~{3,})', re.MULTILINE)


def _fenced_spans(md: str) -> list[tuple[int, int]]:
    """(start, end) offsets of the fenced code blocks of md."""
    spans = []
    open_fence = None
    start = 0
    for m in _FENCE_RE.finditer(md):
        fence = m.group(1)
        if open_fence is None:
            open_fence, start = fence, m.start()
        elif fence[0] == open_fence[0] and len(fence) >= len(open_fence):
            line_end = md.find('\n', m.end())
            spans.append((start, len(md) if line_end < 0 else line_end))
            open_fence = None
    if open_fence is not None:
        spans.append((start, len(md)))
    return spans


def heading_matches(md: str) -> list[re.Match]:
    spans = _fenced_spans(md)
    return [m for m in HEADING_LINE_RE.finditer(md)
            if not any(a <= m.start() < b for a, b in spans)]


def normalize_headings(md: str, target_min: int = 3) -> str:
    """Shift all headings so the shallowest level maps to target_min."""
    matches = heading_matches(md)
    if not matches:
        return md
    offset = target_min - min(len(m.group(1)) for m in matches)
    if offset == 0:
        return md
    out, pos = [], 0
    for m in matches:
        new_level = max(1, min(6, len(m.group(1)) + offset))
        out.append(md[pos:m.start()])
        out.append('#' * new_level + ' ' + m.group(2))
        pos = m.end()
    out.append(md[pos:])
    return ''.join(out)


def top_heading_count(md: str) -> int:
    """Number of headings at the shallowest level of md."""
    levels = [len(m.group(1)) for m in heading_matches(md or '')]
    return levels.count(min(levels)) if levels else 0
