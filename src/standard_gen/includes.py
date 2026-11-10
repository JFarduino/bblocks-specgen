"""
File includes in Markdown: a fenced code block whose info string has an
``include=`` option gets its content from a file, so that a schema (or any
text file) published by a building block can be shown in the document without
being copied into the Markdown source::

    ```yaml include=bblocks://ogc.api.common.part1.schemas.link
    ```

The block is followed by a link to the building block that defines the file:
the block named by a ``bblocks://`` include, the block given with ``bblock=``,
or the block whose schema is the included URL.
"""
from __future__ import annotations

import html
import logging
import re
from typing import TYPE_CHECKING, Optional
from urllib.parse import urljoin

from markdown_it.renderer import RendererHTML

if TYPE_CHECKING:  # pragma: no cover
    from .resolver import Resolver

logger = logging.getLogger(__name__)

_SCHEME_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.-]*:|//)')
_BBLOCKS = 'bblocks://'
_SCHEMA_VARIANTS = ('source', 'annotated')
_NO_BBLOCK = 'none'


class IncludeError(ValueError):
    """An include= fence that cannot be resolved; aborts the run."""


def parse_info(info: str) -> tuple[str, dict[str, str]]:
    """
    Split a fence info string into (language, options).

    `yaml include=bblocks://x schema=annotated` → ('yaml', {'include': ..., 'schema': ...}).
    The language is the first word, unless that word is itself an option.
    """
    lang, opts = '', {}
    for i, word in enumerate(info.split()):
        key, sep, value = word.partition('=')
        if sep:
            opts[key] = value
        elif i == 0:
            lang = word
    return lang, opts


def include_url(target: str, opts: dict[str, str], lang: str,
                bblock_id: Optional[str], resolver: 'Resolver') -> tuple[str, Optional[str]]:
    """
    (URL of the file an include= option points at, identifier of the building
    block that defines it, or None).
    """
    if target.startswith(_BBLOCKS):
        identifier = target[len(_BBLOCKS):]
        bb = resolver.get_bblock(identifier)
        if not bb:
            raise IncludeError(f"unknown building block '{identifier}'")
        variant = opts.get('schema', 'source')
        if variant not in _SCHEMA_VARIANTS:
            raise IncludeError(
                f"schema={variant}: expected one of {', '.join(_SCHEMA_VARIANTS)}")
        if variant == 'source':
            url = bb.get('sourceSchema')
        else:
            schemas = bb.get('schema') or {}
            first, second = (('application/json', 'application/yaml') if lang == 'json'
                             else ('application/yaml', 'application/json'))
            url = schemas.get(first) or schemas.get(second)
        if not url:
            raise IncludeError(f"'{identifier}' has no {variant} schema in the register")
        return url, identifier

    if _SCHEME_RE.match(target):
        return target, resolver.find_bblock_by_schema_url(target)

    bb = resolver.get_bblock(bblock_id) if bblock_id else None
    if not bb or not bb.get('sourceFiles'):
        raise IncludeError(
            f"relative path '{target}' needs a block with sourceFiles to resolve against")
    return urljoin(bb['sourceFiles'].rstrip('/') + '/', target), None


def defining_bblock(opts: dict[str, str], found: Optional[str],
                    resolver: 'Resolver') -> Optional[str]:
    """The building block to link to: bblock= if given (none: no link), else found."""
    explicit = opts.get('bblock')
    if explicit is None:
        return found
    if explicit == _NO_BBLOCK:
        return None
    if explicit.startswith(_BBLOCKS):
        explicit = explicit[len(_BBLOCKS):]
    if not resolver.get_bblock(explicit):
        raise IncludeError(f"bblock={explicit}: unknown building block")
    return explicit


def includes_rule(state) -> None:
    """
    markdown-it core rule: fill fenced code blocks that carry an include= option
    with the content of the file it points at.

    The fence is filled after parsing, so the included text is never seen by the
    Markdown parser (headings, `#` comments, etc. stay verbatim).
    """
    env = state.env
    resolver: Optional['Resolver'] = env.get('resolver')
    if resolver is None:
        return
    bblock_id = env.get('bblock')

    for tok in state.tokens:
        if tok.type != 'fence':
            continue
        lang, opts = parse_info(tok.info)
        target = opts.get('include')
        if not target:
            continue
        try:
            url, found = include_url(target, opts, lang, bblock_id, resolver)
            source_bblock = defining_bblock(opts, found, resolver)
            content = resolver.fetch(url)
        except IncludeError as e:
            raise IncludeError(f"{bblock_id}: include={target}: {e}") from None
        except Exception as e:
            raise IncludeError(f"{bblock_id}: include={target}: could not read it: {e}") from e
        if tok.content.strip():
            logger.warning("%s: include=%s replaces the content of its code block",
                           bblock_id, target)
        tok.content = content if content.endswith('\n') else content + '\n'
        tok.info = lang
        tok.meta = {**(tok.meta or {}), 'include': url, 'include_bblock': source_bblock}


def render_fence(self, tokens, idx, options, env) -> str:
    """Default fence rendering, followed by a link to the block defining an included file."""
    out = RendererHTML.fence(self, tokens, idx, options, env)
    identifier = (tokens[idx].meta or {}).get('include_bblock')
    if identifier:
        ident = html.escape(identifier, quote=True)
        # bblocks:// links are resolved (in-document anchor or viewer URL) after rendering
        out += (f'<p class="include-source">Building block: '
                f'<a href="bblocks://{ident}">{ident}</a></p>\n')
    return out
