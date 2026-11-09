from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from .figures import FigureRegistry, ASSETS_DIR, make_markdown
from .ontology import ontology_tables
from .models import StandardDocument, SubSection
from .resolver import Resolver, _slugify
from .assembler import placements
from .headings import heading_matches, normalize_headings

logger = logging.getLogger(__name__)

_md = make_markdown()

_REQUIREMENTS_MARKER = '<!-- requirements -->'

# Matches rendered heading tags without existing id attribute
_HEADING_TAG_RE = re.compile(r'<(h[1-6])>(.*?)</\1>', re.DOTALL)
# Matches rendered heading tags once they have an id
_ID_HEADING_TAG_RE = re.compile(r'<h([1-6]) id="([^"]*)">(.*?)</h\1>', re.DOTALL)
# Placement markers, as passed through by markdown-it (an HTML block)
_PLACEMENT_HTML_RE = re.compile(
    r'(?:<p>)?<!--\s*(requirements-class|requirement|recommendation|permission|'
    r'conformance-class|abstract-test)(?:\s*:\s*(\S+?))?\s*-->(?:</p>)?\n?')


def _render_markdown(text: str, env: Optional[dict] = None) -> str:
    if not text:
        return ''
    return _md.render(text, env if env is not None else {})


def _normalize_headings(md_text: str, target_min: int = 3) -> str:
    """Shift all headings so the shallowest level maps to target_min."""
    return normalize_headings(md_text, target_min)


def _extract_headings(normalized_md: str) -> list[tuple[int, str, str]]:
    """Return [(level, title, slug), ...] from normalized markdown."""
    return [
        (len(m.group(1)), m.group(2).strip(), _slugify(m.group(2).strip()))
        for m in heading_matches(normalized_md)
    ]


def _inject_heading_ids(html: str) -> str:
    """Add id attributes to <hN> tags that don't already have one."""
    def replacer(m: re.Match) -> str:
        tag, content = m.group(1), m.group(2)
        text = re.sub(r'<[^>]+>', '', content).strip()
        slug = _slugify(text)
        return f'<{tag} id="{slug}">{content}</{tag}>'
    return _HEADING_TAG_RE.sub(replacer, html)


def _split_intro(text: str) -> str:
    idx = text.find(_REQUIREMENTS_MARKER)
    return text[:idx].strip() if idx >= 0 else text


def _split_outro(text: str) -> str:
    idx = text.find(_REQUIREMENTS_MARKER)
    return text[idx + len(_REQUIREMENTS_MARKER):].strip() if idx >= 0 else ''


class Numbering:
    """
    Hierarchical numbers for the subclauses of one section ("6.1", "6.1.2"),
    the first heading level below the section title (h3 below an h2) being
    the first. Preface subclauses are lettered: "IV.A", "IV.A.1".
    """

    def __init__(self, prefix: str, base_level: int = 2, letters: bool = False):
        self.prefix = prefix
        self._base_level = base_level
        self._letters = letters
        self._counters: list[int] = []

    def next(self, level: int) -> str:
        depth = max(1, level - self._base_level)
        while len(self._counters) < depth:
            self._counters.append(0)
        del self._counters[depth:]
        self._counters[depth - 1] += 1
        parts = [str(c) for c in self._counters]
        if self._letters:
            parts[0] = _letter(self._counters[0])
        return '.'.join([self.prefix] + parts)


def _letter(n: int) -> str:
    return 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[n - 1] if 1 <= n <= 26 else str(n)


def _section_numbering(section) -> Optional[Numbering]:
    """Subclause numbering of a section in the 'ogc' layout (None: unnumbered)."""
    if not section.number:
        return None
    return Numbering(section.number, base_level=section.level,
                     letters=section.kind == 'preface')


class SectionContext:
    """
    Per-section rendering state: subclause numbering (in the 'ogc' layout)
    and the placement of requirements classes, statements, conformance
    classes and abstract tests at markers found in the section's Markdown.
    """

    def __init__(self, renderer: 'Renderer', section):
        self.section = section
        self.numbering = _section_numbering(section) if renderer.ogc_layout else None
        self._renderer = renderer
        self.placed: set = set()

    def is_placed(self, obj) -> bool:
        return id(obj) in self.placed

    @property
    def level(self) -> int:
        """Heading level of the section title (h2, or deeper for a subclause)."""
        return self.section.level

    def heading(self, level: int, title: str, anchor: str) -> str:
        """An auto-generated heading (e.g. 'Requirements'), numbered like the others."""
        if self.numbering:
            num = self.numbering.next(level)
            return f'<h{level} id="sec-{num}">{num}. {title}</h{level}>'
        return f'<h{level} id="{anchor}">{title}</h{level}>'

    def number_headings(self, html: str) -> str:
        if not self.numbering:
            return html

        def repl(m: re.Match) -> str:
            level, content = int(m.group(1)), m.group(3)
            num = self.numbering.next(level)
            return f'<h{level} id="sec-{num}">{num}. {content}</h{level}>'
        return _ID_HEADING_TAG_RE.sub(repl, html)

    def place(self, kind: str, ident: Optional[str]) -> Optional[str]:
        return self._renderer.render_placement(self, kind, ident)


def _basename(url: str) -> str:
    return os.path.basename(url.rstrip('/'))


def _terms_markdown(content) -> str:
    """
    The terms clause as Markdown, for the 'ogc' layout: the preamble, then
    one subclause per term under "Terms and definitions", then "Abbreviated
    terms" (rendered, and numbered, like any other clause text).
    """
    out = [content.preamble_md.strip()] if content.preamble_md else []
    if content.terms:
        out.append('# Terms and definitions')
        for term in content.terms:
            out.append(f'## {term.term}')
            out.append(term.definition.strip())
            if term.sources:
                links = ', '.join(f"[{s.get('title', '')}]({s.get('uri', '#')})" for s in term.sources)
                out.append(f'[SOURCE: {links}]')
            if term.note:
                out.append(f'<div class="note">\n\n**Note:** {term.note.strip()}\n\n</div>')
    if content.abbreviated_terms:
        out.append('# Abbreviated terms')
        out.append('\n'.join(
            f'<dt>{abbr}</dt><dd>{expansion}</dd>'
            for abbr, expansion in content.abbreviated_terms.items()).join(
                ['<dl class="abbreviated-terms">\n', '\n</dl>']))
    return '\n\n'.join(out) + '\n'


def navigate_schema(schema: dict, json_path: str) -> Optional[dict]:
    """
    Simplified data JSONPath → schema location navigation.
    Supports $.prop, $.prop[*], $.prop[*].sub patterns.
    Does not resolve $ref.
    """
    path = (json_path or '$').strip()
    if path in ('$', '$.', ''):
        return schema
    if path.startswith('$.'):
        path = path[2:]
    elif path.startswith('$'):
        path = path[1:]

    parts = re.split(r'\.|\[(?:\*|\d+)\]\.?', path)
    parts = [p for p in parts if p]

    node = schema
    for part in parts:
        if node is None:
            return None
        if '$ref' in node:
            return None  # ref resolution not supported in PoC
        if 'properties' in node and part in node['properties']:
            node = node['properties'][part]
        elif node.get('type') == 'array' and 'items' in node:
            node = node['items']
            if 'properties' in node and part in node['properties']:
                node = node['properties'][part]
        else:
            return None
    return node


class Renderer:
    def __init__(self, resolver: Resolver, templates_dir: Path):
        self._resolver = resolver
        self._figures: Optional[FigureRegistry] = None
        self._templates_dir = templates_dir
        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=False,
        )
        self._env.filters['slugify'] = _slugify
        self._env.filters['basename'] = _basename
        self._env.filters['split_intro'] = _split_intro
        self._env.filters['split_outro'] = _split_outro

        self._env.globals['render_markdown'] = self._render_and_resolve
        self._env.globals['get_viewer_url'] = resolver.get_bblock_viewer_url
        self._env.globals['get_property_table'] = self._get_property_table
        self._env.globals['get_ontology_table'] = self._get_ontology_table
        self._env.globals['section_ctx'] = lambda section: SectionContext(self, section)
        self._env.globals['section_title'] = self._section_title
        self._env.globals['terms_markdown'] = _terms_markdown
        self._env.globals['get_layout'] = lambda: 'ogc' if self.ogc_layout else 'classic'
        self._env.globals['has_placement'] = lambda text, kind: any(
            k == kind for k, _ in placements(text or ''))
        self._env.globals['modspec'] = lambda name: self._metadata.modspec_option(name)
        self.ogc_layout = False
        self._metadata = None
        self._owners: dict = {}

    def _render_and_resolve(self, text: str, bblock_id: Optional[str] = None,
                            ctx: Optional[SectionContext] = None) -> str:
        normalized = _normalize_headings(text, ctx.level + 1 if ctx is not None else 3)
        html = _render_markdown(normalized, {'figures': self._figures, 'bblock': bblock_id})
        html = _inject_heading_ids(html)
        if ctx is not None:
            html = ctx.number_headings(html)
            html = _PLACEMENT_HTML_RE.sub(
                lambda m: ctx.place(m.group(1), m.group(2)) or m.group(0), html)
        return self._resolver.resolve_bblocks_links_in_html(html)

    @property
    def _macros(self):
        return self._env.get_template('macros.html.j2').module

    def render_placement(self, ctx: SectionContext, kind: str, ident: Optional[str]) -> Optional[str]:
        """HTML for a placement marker, or None to leave the marker as is."""
        from .models import ReqClass, AnnexA
        content = ctx.section.content
        m = self._macros
        if isinstance(content, ReqClass):
            if kind == 'requirements-class':
                ctx.placed.add(id(content))
                return str(m.req_class_box(content, ctx))
            if kind in ('requirement', 'recommendation', 'permission'):
                req = next((r for r in content.requirements if r.kind == kind and r.id == ident), None)
                if req is None:
                    return ''
                return str(m.statement(req, content, ctx))
            return None
        if kind in ('requirement', 'recommendation', 'permission'):
            # A statement placed outside its class (a prose clause, Annex A)
            found = self._owners.get((kind, ident))
            return str(m.statement(found[0], found[1], ctx)) if found else ''
        if isinstance(content, AnnexA) and content.explicit:
            if kind == 'conformance-class':
                rc = next((c for c in content.req_classes
                           if ident in (c.bblock_id, c.conf_class_uri)), None)
                if rc is None:
                    return ''
                ctx.placed.add(id(rc))
                html = str(m.conf_class(rc, ctx))
                for t in content.unplaced_tests.get(rc.bblock_id, []):
                    html += str(m.abstract_test(t, ctx))
                return html
            if kind == 'abstract-test':
                tests = list(content.tests) + [t for rc in content.req_classes for t in rc.abstract_tests]
                t = next((t for t in tests if t.id == ident), None)
                return str(m.abstract_test(t, ctx)) if t else ''
        return None

    def _section_title(self, section, toc: bool = False) -> str:
        """Section heading text, or its table of contents entry."""
        from .models import AnnexA
        if not self.ogc_layout:
            if not section.number:
                return section.title
            if isinstance(section.content, AnnexA) and not toc:
                return f'{section.number} {section.title}'
            return f'{section.number}. {section.title}'
        if section.kind == 'annex' and section.number:
            status = 'normative' if section.normative else 'informative'
            return f'Annex {section.number} ({status}) {section.title}'
        if section.number:
            return f'{section.number}. {section.title}'
        return section.title

    def _get_property_table(self, applies_to) -> Optional[dict]:
        """Return {properties: [{name, type, format, ref, description}], required: set} or None."""
        if not applies_to or not applies_to.bblock:
            return None
        json_path = applies_to.json_path or '$'
        schema = self._resolver.get_bblock_schema(applies_to.bblock)
        if not schema:
            return None
        node = navigate_schema(schema, json_path)
        if not node or 'properties' not in node:
            return None

        required = set(node.get('required', []))
        props = []
        for name, prop in node['properties'].items():
            # Detect $ref at property level or inside items (for arrays)
            ref = prop.get('$ref')
            items = prop.get('items', {})
            items_ref = items.get('$ref') if isinstance(items, dict) else None
            type_str = prop.get('type', '—')
            props.append({
                'name': name,
                'type': type_str,
                'format': prop.get('format'),
                'ref': ref or items_ref,
                'description': prop.get('description', ''),
                'required': name in required,
            })
        return {'properties': props}

    def _get_ontology_table(self, applies_to) -> Optional[dict]:
        """Return {classes, properties} from the bblock's ontology, or None."""
        if not applies_to or not applies_to.bblock:
            return None
        turtle = self._resolver.get_bblock_ontology(applies_to.bblock)
        if not turtle:
            return None
        return ontology_tables(turtle, applies_to.terms or None)

    def _populate_subsections(self, doc: StandardDocument) -> None:
        """Extract top-level subheadings from clause markdown for ToC nesting."""
        from .models import ReqClass
        if self.ogc_layout:
            return self._populate_numbered_subsections(doc)
        for section in doc.sections:
            content = section.content
            md = getattr(content, 'description_md', '')
            normalized = _normalize_headings(md) if md else ''
            headings = _extract_headings(normalized) if normalized else []
            subsections = [
                SubSection(title=title, anchor=slug, level=level)
                for level, title, slug in headings
                if level == 3
            ]
            # Auto-inject "Requirements" subsection for req classes that have requirements
            if isinstance(content, ReqClass) and content.requirements:
                subsections.append(SubSection(
                    title='Requirements',
                    anchor=f'req-{section.anchor}-requirements',
                    level=3,
                ))
            section.subsections = subsections

    def _section_headings(self, section) -> list[tuple[int, str]]:
        """(level, title) of the subclause headings of a section, in display order."""
        from .models import ReqClass, ProseClause, TermsClause, ReferencesClause, AnnexA
        content = section.content

        def heads(md):
            return [(lvl, title) for lvl, title, _ in
                    _extract_headings(_normalize_headings(md, section.level + 1))] if md else []

        if isinstance(content, ReqClass):
            md = content.description_md or ''
            out = heads(_split_intro(md))
            if content.unplaced:
                out.append((section.level + 1, 'Requirements'))
            return out + heads(_split_outro(md))
        if isinstance(content, TermsClause):
            return heads(_terms_markdown(content))
        if isinstance(content, ReferencesClause):
            return heads(content.preamble_md)
        if isinstance(content, AnnexA):
            return heads(content.description_md) if content.explicit else []
        if isinstance(content, ProseClause):
            return heads(content.description_md)
        return []

    def _populate_numbered_subsections(self, doc: StandardDocument) -> None:
        from .models import TermsClause
        for section in doc.sections:
            numbering = _section_numbering(section)
            # Listed down to 6.1.2.3, but not the terms themselves (4.1.1...)
            toc_depth = 3 if isinstance(section.content, TermsClause) else 5
            subs = []
            for level, title in self._section_headings(section):
                if numbering:
                    num = numbering.next(level)
                    if level <= toc_depth:
                        subs.append(SubSection(title=title, anchor=f'sec-{num}', level=level, number=num))
                elif level == section.level + 1:
                    subs.append(SubSection(title=title, anchor=_slugify(title), level=level))
            section.subsections = subs

    @staticmethod
    def _nest_subclauses(doc: StandardDocument) -> None:
        """List each subclause, and its own subclauses, under its top-level clause."""
        root = None
        for section in doc.sections:
            if section.depth == 0:
                root = section
            elif root is not None:
                root.subsections.append(SubSection(
                    title=section.title, anchor=section.anchor,
                    level=section.level, number=section.number))
                root.subsections.extend(section.subsections)

    def render(self, doc: StandardDocument, build_dir: Path) -> None:
        from .assembler import statement_owners
        self.ogc_layout = doc.metadata.ogc_layout
        self._metadata = doc.metadata
        self._owners = statement_owners(doc)
        self._populate_subsections(doc)
        self._nest_subclauses(doc)

        build_dir.mkdir(parents=True, exist_ok=True)

        css_src = self._templates_dir / 'ogc-standard.css'
        shutil.copy(css_src, build_dir / 'ogc-standard.css')
        if self.ogc_layout:
            shutil.copy(self._templates_dir / 'ogc-layout.css', build_dir / 'ogc-layout.css')

        template = self._env.get_template('base.html.j2')

        # Two passes: the first numbers every figure in document order, the
        # second renders with all figures known (so forward references work).
        self._figures = FigureRegistry(self._resolver, {
            s.content.bblock_id: s.number
            for s in doc.sections if hasattr(s.content, 'bblock_id')
        })
        template.render(doc=doc)
        self._figures.freeze()
        html = template.render(doc=doc)

        assets_dir = build_dir / ASSETS_DIR
        if assets_dir.is_dir() and (build_dir / '.specgen').is_file():
            shutil.rmtree(assets_dir)
        self._figures.copy_assets(build_dir)

        out = build_dir / 'index.html'
        out.write_text(html, encoding='utf-8')
        logger.info("Written %s", out)
