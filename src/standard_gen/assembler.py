from __future__ import annotations

import logging
import re

from .models import (
    StandardDocument, StandardMetadata, Section,
    ProseClause, TermsClause, ReferencesClause, ReqClass,
    ConformanceClause, AnnexA, ClauseEntry, AbstractTest, TestRequirementRef,
)
from .resolver import Resolver, _slugify

logger = logging.getLogger(__name__)

_ANNEX_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

_REQUIREMENTS_MARKER = '<!-- requirements -->'

# Placement markers that can appear on their own line in a description.md:
#   <!-- requirements-class -->           the requirements class table
#   <!-- requirement: <id> -->            a statement of this class (also
#   <!-- recommendation: <id> -->         recommendation / permission)
#   <!-- permission: <id> -->
#   <!-- conformance-class: <bblock> -->  (Annex A block) a conformance class
#   <!-- abstract-test: <id> -->          (Annex A block) an abstract test
PLACEMENT_RE = re.compile(
    r'<!--\s*(requirements-class|requirement|recommendation|permission|'
    r'conformance-class|abstract-test)(?:\s*:\s*(\S+?))?\s*-->')


def placements(md: str) -> list[tuple[str, str | None]]:
    return [(m.group(1), m.group(2)) for m in PLACEMENT_RE.finditer(md or '')]


def _roman(n: int) -> str:
    out = ''
    for value, sym in ((1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'),
                       (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')):
        while n >= value:
            out += sym
            n -= value
    return out


class Assembler:
    """
    Takes loader output and produces a fully numbered StandardDocument.
    Preface clauses get roman numerals, clauses are numbered sequentially,
    annexes are lettered; then statements and abstract tests are labelled.
    """

    def __init__(self, metadata: StandardMetadata, clauses: list, resolver: Resolver):
        self._metadata = metadata
        self._clauses = [c if isinstance(c, ClauseEntry) else ClauseEntry(content=c) for c in clauses]
        self._resolver = resolver

    def _anchor(self, default: str, number: str, title: str) -> str:
        if self._metadata.ogc_layout:
            return 'sec-' + (number if number else _slugify(title))
        return default

    def assemble(self) -> StandardDocument:
        doc = StandardDocument(metadata=self._metadata)
        clause_num = 0
        annex_num = 0
        preface_num = 0
        in_annexes = False

        for entry in self._clauses:
            content = entry.content
            if entry.preface:
                preface_num += 1
                number = _roman(preface_num) if entry.numbered else ''
                title = entry.title or getattr(content, 'name', '')
                doc.sections.append(Section(
                    number=number, title=title,
                    anchor=self._anchor(f'preface-{preface_num}', number, title),
                    content=content, kind='preface',
                    normative=getattr(content, 'normative', True),
                ))
                continue

            if isinstance(content, AnnexA):
                in_annexes = True
                letter = _ANNEX_LETTERS[annex_num]
                annex_num += 1
                title = entry.title or 'Abstract Test Suite (Normative)'
                if entry.title is None and self._metadata.ogc_layout:
                    title = 'Abstract Test Suite'
                doc.sections.append(Section(
                    number=letter, title=title,
                    anchor=self._anchor('annex-a', letter, title),
                    content=content, kind='annex',
                ))
                continue

            if not entry.numbered:
                title = entry.title or getattr(content, 'name', '')
                doc.sections.append(Section(
                    number='', title=title,
                    anchor=self._anchor(f'clause-{_slugify(title)}', '', title),
                    content=content, kind='annex' if in_annexes else 'clause',
                    normative=getattr(content, 'normative', True),
                ))
                continue

            if isinstance(content, ConformanceClause):
                clause_num += 1
                number = str(clause_num)
                title = entry.title or 'Conformance'
                doc.sections.append(Section(
                    number=number, title=title,
                    anchor=self._anchor('conformance', number, title),
                    content=content,
                ))
            elif isinstance(content, ReqClass):
                clause_num += 1
                number = str(clause_num)
                title = entry.title or content.name
                doc.sections.append(Section(
                    number=number, title=title,
                    anchor=self._anchor(f'clause-{clause_num}', number, title),
                    content=content,
                ))
            elif isinstance(content, (ProseClause, TermsClause, ReferencesClause)):
                title = entry.title or content.name
                if in_annexes:
                    letter = _ANNEX_LETTERS[annex_num]
                    annex_num += 1
                    doc.sections.append(Section(
                        number=letter, title=title,
                        anchor=self._anchor(f'annex-{letter.lower()}', letter, title),
                        content=content, kind='annex',
                        normative=getattr(content, 'normative', True),
                    ))
                else:
                    clause_num += 1
                    number = str(clause_num)
                    anchor = _well_known_anchor(content) or f'clause-{clause_num}'
                    doc.sections.append(Section(
                        number=number, title=title,
                        anchor=self._anchor(anchor, number, title),
                        content=content,
                        normative=getattr(content, 'normative', True),
                    ))
            else:
                logger.warning("Unknown clause type: %s", type(content))

        self._label_statements(doc)
        self._label_abstract_tests(doc)
        return doc

    # ------------------------------------------------------------------
    # Statements
    # ------------------------------------------------------------------

    def _label_statements(self, doc: StandardDocument) -> None:
        """
        Order each class's statements as they are displayed (placement
        markers in description.md, the remaining ones at the
        <!-- requirements --> marker), then label them: document-wide per
        kind in the 'ogc' layout ("Requirement 12"), per clause otherwise
        ("Requirement 5/3").
        """
        global_counts: dict[str, int] = {}
        class_num = 0
        for section in doc.sections:
            rc = section.content
            if not isinstance(rc, ReqClass):
                continue
            class_num += 1
            rc.label = f'Requirements class {class_num}'
            for req in rc.requirements:
                req.display_id = self._resolver.display_id(req.uri)

            by_key = {(r.kind, r.id): r for r in rc.requirements}
            md = rc.description_md or ''
            idx = md.find(_REQUIREMENTS_MARKER)
            intro, outro = (md[:idx], md[idx:]) if idx >= 0 else (md, '')

            def placed(text):
                out = []
                for kind, ident in placements(text):
                    if kind in ('requirement', 'recommendation', 'permission'):
                        req = by_key.get((kind, ident))
                        if req is None:
                            logger.warning("%s: placement marker for unknown %s %r",
                                           rc.bblock_id, kind, ident)
                        elif req not in out:
                            out.append(req)
                return out

            first, last = placed(intro), placed(outro)
            placed_set = {id(r) for r in first + last}
            rc.unplaced = [r for r in rc.requirements if id(r) not in placed_set]
            rc.display_order = first + rc.unplaced + last

            local_counts: dict[str, int] = {}
            for req in rc.display_order:
                if self._metadata.ogc_layout:
                    n = global_counts[req.kind] = global_counts.get(req.kind, 0) + 1
                    req.label = f'{req.kind_label} {n}'
                else:
                    n = local_counts[req.kind] = local_counts.get(req.kind, 0) + 1
                    req.label = f'{req.kind_label} {section.number}/{n}'

    # ------------------------------------------------------------------
    # Abstract tests
    # ------------------------------------------------------------------

    def _label_abstract_tests(self, doc: StandardDocument) -> None:
        for section in doc.sections:
            annex = section.content
            if not isinstance(annex, AnnexA) or not annex.explicit:
                continue

            # Requirements win over recommendations/permissions sharing an id
            statements = {}
            for rc in annex.req_classes:
                for req in sorted(rc.requirements, key=lambda r: r.kind != 'requirement'):
                    for key in (req.uri, req.display_id, req.id):
                        statements.setdefault(key, req)

            classes_by_key = {}
            for rc in annex.req_classes:
                for key in (rc.bblock_id, rc.conf_class_uri):
                    classes_by_key[key] = rc
            tests_by_id: dict[str, tuple[AbstractTest, str | None]] = {}
            for t in annex.tests:
                tests_by_id[t.id] = (t, None)
            for rc in annex.req_classes:
                for t in rc.abstract_tests:
                    if t.id in tests_by_id:
                        logger.warning("Duplicate abstract test id %r", t.id)
                    tests_by_id[t.id] = (t, rc.bblock_id)

            # Display order: markers in the annex description, then each
            # unplaced class (with its unplaced tests), then unplaced annex tests.
            order: list[AbstractTest] = []
            placed_classes = []
            placed_tests = set()
            for kind, ident in placements(annex.description_md):
                if kind == 'conformance-class':
                    rc = classes_by_key.get(ident)
                    if rc is None:
                        logger.warning("Annex A: placement marker for unknown conformance class %r", ident)
                    elif rc not in placed_classes:
                        placed_classes.append(rc)
                elif kind == 'abstract-test':
                    found = tests_by_id.get(ident)
                    if found is None:
                        logger.warning("Annex A: placement marker for unknown abstract test %r", ident)
                    elif ident not in placed_tests:
                        placed_tests.add(ident)
                        order.append(found[0])

            annex.unplaced_classes = [rc for rc in annex.req_classes if rc not in placed_classes]
            annex.unplaced_tests = {}
            for t in annex.tests:
                if t.id not in placed_tests:
                    annex.unplaced_tests.setdefault(None, []).append(t)
            for rc in annex.req_classes:
                for t in rc.abstract_tests:
                    if t.id not in placed_tests:
                        annex.unplaced_tests.setdefault(rc.bblock_id, []).append(t)

            # Tests of a placed class that are not themselves placed follow
            # the class table; unplaced classes come after the description.
            full_order = list(order)
            for rc in placed_classes + annex.unplaced_classes:
                full_order += annex.unplaced_tests.get(rc.bblock_id, [])
            full_order += annex.unplaced_tests.get(None, [])

            n = 0
            seen = set()
            letter = section.number
            for t in self._display_sequence(annex, placed_classes):
                if id(t) in seen:
                    continue
                seen.add(id(t))
                n += 1
                t.label = f'Abstract test {letter}.{n}' if letter else f'Abstract test {n}'
            for t in full_order:
                if id(t) not in seen:
                    seen.add(id(t))
                    n += 1
                    t.label = f'Abstract test {letter}.{n}' if letter else f'Abstract test {n}'

            for i, rc in enumerate(placed_classes + annex.unplaced_classes, 1):
                rc.conf_label = f'Conformance class {letter}.{i}' if letter else f'Conformance class {i}'

            for t, _ in tests_by_id.values():
                t.display_id = self._resolver.display_id(t.uri)
                t.requirements = []
                base = self._metadata.base_uri.rstrip('/')
                for entry in t.requirement_refs:
                    ref, text = entry['ref'], entry.get('text')
                    req = statements.get(ref) or (statements.get(base + ref) if ref.startswith('/') else None)
                    if req is None:
                        logger.warning("Abstract test %r: requirement %r not found", t.id, ref)
                        t.requirements.append(TestRequirementRef(text=text or ref))
                    else:
                        t.requirements.append(TestRequirementRef(
                            text=text or req.display_id, anchor=req.anchor,
                            label='' if text else req.label))

    @staticmethod
    def _display_sequence(annex: AnnexA, placed_classes: list) -> list[AbstractTest]:
        """
        Abstract tests in the order they appear in the rendered annex: walk
        the description markers; a conformance-class marker is followed by
        that class's tests that are not placed elsewhere.
        """
        tests_by_id = {t.id: t for t in annex.tests}
        for rc in annex.req_classes:
            for t in rc.abstract_tests:
                tests_by_id[t.id] = t
        classes = {}
        for rc in annex.req_classes:
            classes[rc.bblock_id] = rc
            classes[rc.conf_class_uri] = rc
        seq = []
        for kind, ident in placements(annex.description_md):
            if kind == 'abstract-test' and ident in tests_by_id:
                seq.append(tests_by_id[ident])
            elif kind == 'conformance-class' and ident in classes:
                seq.extend(annex.unplaced_tests.get(classes[ident].bblock_id, []))
        return seq


def _well_known_anchor(content) -> str | None:
    """Use stable anchors for well-known clause types."""
    if isinstance(content, TermsClause):
        return 'terms'
    if isinstance(content, ReferencesClause):
        return 'references'
    return None
