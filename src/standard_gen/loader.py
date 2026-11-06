from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
import requests

from .models import (
    StandardMetadata, ReqClass, Requirement, AppliesTo, Dependency,
    TestResource, ProseClause, TermsClause, ReferencesClause,
    Term, Reference, ConformanceClause, AnnexA, Example, Snippet,
    AbstractTest, ClauseEntry, STATEMENT_KINDS,
)
from .resolver import Resolver

logger = logging.getLogger(__name__)


class Loader:
    """
    Takes an already-parsed compiled register plus one standard's definition
    (an entry from standards.yaml, see generate.load_standards()) and produces
    the raw data objects that Assembler will turn into a StandardDocument.

    Pure in-memory transform - no file/network I/O of its own beyond what
    Resolver.fetch() does while walking clauses (local source files or
    remote bblock resources). Register/extra-register loading (from disk,
    or from a build-plugin hook payload) is the caller's job - see
    generate.generate() for the shared entry point CLI and the build plugin
    both use.
    """

    def __init__(
        self,
        register: dict,
        standard: dict,
        source_dir: Path,
        extra_registers: list[dict] = (),
    ):
        self._register = register
        self._standard = standard
        self._source_dir = source_dir
        self._extra_registers = list(extra_registers)
        # One entry per block considered for the document, filled by load()
        # and written out by generate() as report.json.
        self.report: list[dict] = []
        self._class_ids: dict[str, str] = {}

    def load(self) -> tuple[StandardMetadata, list, Resolver]:
        """
        Returns (metadata, clause_data_list, resolver).
        """
        local_register = self._register
        source_dir = self._source_dir

        std = self._standard
        metadata = _parse_metadata(std)

        imported_bblocks = self._load_imported_registers(local_register)
        for reg in self._extra_registers:
            imported_bblocks.extend(reg.get('bblocks', []))

        resolver = Resolver(metadata, local_register, imported_bblocks, source_dir)

        prefix = std['prefix']
        local_by_id = {
            bb['itemIdentifier']: bb
            for bb in local_register.get('bblocks', [])
            if bb['itemIdentifier'].startswith(prefix)
        }

        clauses = []
        req_classes = []  # collected for auto clauses

        clause_entries = std.get('clauses') or _clauses_from_index(local_by_id)
        entries = ([(e, True) for e in std.get('preface') or []]
                   + [(e, False) for e in clause_entries])

        for entry, preface in entries:
            opts = entry if isinstance(entry, dict) else {}
            clause_opts = {
                'title': opts.get('title'),
                'numbered': opts.get('numbered', True),
                'preface': preface,
            }
            if 'auto' in opts:
                clauses.append(({'auto': opts['auto'], 'bblock': opts.get('bblock')}, clause_opts))
                continue

            bblock_id = opts['bblock'] if opts else entry
            bb = local_by_id.get(bblock_id)
            if not bb:
                logger.warning("Clause bblock not found in register: %s", bblock_id)
                self.report.append({'bblock': bblock_id, 'role': None, 'reason': 'not found in register'})
                continue

            role, reason = self._detect_role(bb, resolver)
            logger.info("%s: %s (%s)", bb['itemIdentifier'], role, reason)
            content = self._load_clause(bb, role, resolver)
            self.report.append({
                'bblock': bb['itemIdentifier'],
                'itemClass': bb.get('itemClass'),
                'role': role,
                'reason': reason,
                'loaded': content is not None,
            })
            if content is None:
                continue

            clauses.append((content, clause_opts))
            if isinstance(content, ReqClass):
                req_classes.append(content)

        # Resolve auto entries now that req_classes is complete
        resolved = []
        for entry, clause_opts in clauses:
            if isinstance(entry, dict) and 'auto' in entry:
                auto_type = entry['auto']
                if auto_type == 'conformance':
                    content = ConformanceClause(req_classes=req_classes)
                elif auto_type == 'annex-a':
                    annex_bb = None
                    if entry.get('bblock'):
                        annex_bb = local_by_id.get(entry['bblock'])
                        if not annex_bb:
                            logger.warning("Annex A bblock not found in register: %s", entry['bblock'])
                        else:
                            self.report.append({
                                'bblock': annex_bb['itemIdentifier'],
                                'itemClass': annex_bb.get('itemClass'),
                                'role': 'annex-a',
                                'reason': "used by {auto: annex-a}",
                                'loaded': True,
                            })
                    content = self._build_annex_a(req_classes, resolver, annex_bb)
                elif auto_type in ('abstract', 'keywords'):
                    content = self._auto_prose(auto_type, metadata)
                else:
                    logger.warning("Unknown auto clause type: %s", auto_type)
                    continue
            else:
                content = entry
            resolved.append(ClauseEntry(content=content, **clause_opts))

        return metadata, resolved, resolver

    @staticmethod
    def _auto_prose(auto_type: str, metadata: StandardMetadata) -> ProseClause:
        """Preface clauses generated from the standard's metadata."""
        if auto_type == 'abstract':
            return ProseClause(bblock_id=None, name='Abstract', description_md=metadata.abstract or '')
        keywords = ', '.join(str(k) for k in metadata.keywords)
        return ProseClause(
            bblock_id=None, name='Keywords',
            description_md=('The following are keywords to be used by search engines and '
                            f'document catalogues.\n\n{keywords}\n'),
        )

    # ------------------------------------------------------------------
    # Clause loading
    # ------------------------------------------------------------------

    def _detect_role(self, bb: dict, resolver: Resolver) -> tuple[str, str]:
        """
        Decide how a block takes part in the document, returning (role, reason).

        'terms' and 'references' item classes are specific kinds of block and
        win outright. Any other block that ships a requirements.yaml is a
        requirements class, whatever its itemClass (e.g. a 'model' block);
        otherwise it is a prose clause.
        """
        item_class = bb.get('itemClass')
        if item_class in ('terms', 'references'):
            return item_class, f"itemClass '{item_class}'"
        if self._has_requirements(bb, resolver):
            if item_class == 'clause':
                logger.warning(
                    "%s has itemClass 'clause' but also a requirements.yaml; "
                    "treating it as a requirements class", bb['itemIdentifier'])
            return 'requirements-class', 'requirements.yaml found'
        if item_class == 'requirements-class':
            logger.warning("%s has itemClass 'requirements-class' but no requirements.yaml",
                           bb['itemIdentifier'])
            return 'prose', "itemClass 'requirements-class' but no requirements.yaml"
        return 'prose', 'no requirements.yaml'

    @staticmethod
    def _source_file_url(bb: dict, name: str) -> str:
        return bb['sourceFiles'].rstrip('/') + '/' + name

    def _requirements_url(self, bb: dict) -> str:
        return self._source_file_url(bb, 'requirements.yaml')

    def _has_requirements(self, bb: dict, resolver: Resolver) -> bool:
        url = self._requirements_url(bb)
        local = resolver.local_path(url)
        if local is not None:
            return local.is_file()
        try:
            resolver.fetch(url)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return False
            raise
        return True

    def _load_clause(self, bb: dict, role: str, resolver: Resolver):
        if role == 'terms':
            return self._load_terms_clause(bb, resolver)
        elif role == 'references':
            return self._load_references_clause(bb, resolver)
        elif role == 'requirements-class':
            return self._load_req_class(bb, resolver)
        return self._load_prose_clause(bb, resolver)

    def _load_prose_clause(self, bb: dict, resolver: Resolver) -> ProseClause:
        desc_url = bb['sourceFiles'].rstrip('/') + '/description.md'
        try:
            description_md = resolver.fetch(desc_url)
        except Exception as e:
            logger.warning("Could not fetch description.md for %s: %s", bb['itemIdentifier'], e)
            description_md = ''
        return ProseClause(
            bblock_id=bb['itemIdentifier'],
            name=bb['name'],
            description_md=description_md,
            normative=bb.get('normative', True),
        )

    def _load_terms_clause(self, bb: dict, resolver: Resolver) -> TermsClause:
        terms_data = {}
        try:
            terms_data = yaml.safe_load(resolver.fetch(self._source_file_url(bb, 'terms.yaml'))) or {}
        except Exception as e:
            logger.warning("Could not fetch terms.yaml for %s: %s", bb['itemIdentifier'], e)

        desc_url = bb['sourceFiles'].rstrip('/') + '/description.md'
        preamble = ''
        try:
            preamble = resolver.fetch(desc_url)
        except Exception:
            pass

        terms = [
            Term(
                term=t['term'],
                definition=t.get('definition', ''),
                sources=t.get('sources', []),
                note=t.get('note'),
            )
            for t in terms_data.get('terms', [])
        ]
        if terms_data.get('sort', True):
            terms.sort(key=lambda t: t.term.lower())

        return TermsClause(
            bblock_id=bb['itemIdentifier'],
            name=bb['name'],
            terms=terms,
            abbreviated_terms=terms_data.get('abbreviated-terms', {}),
            preamble_md=preamble,
        )

    def _load_references_clause(self, bb: dict, resolver: Resolver) -> ReferencesClause:
        refs_data = {}
        try:
            refs_data = yaml.safe_load(resolver.fetch(self._source_file_url(bb, 'references.yaml'))) or {}
        except Exception as e:
            logger.warning("Could not fetch references.yaml for %s: %s", bb['itemIdentifier'], e)

        desc_url = bb['sourceFiles'].rstrip('/') + '/description.md'
        preamble = ''
        try:
            preamble = resolver.fetch(desc_url)
        except Exception:
            pass

        def parse_refs(entries):
            return [
                Reference(
                    title=r.get('title', ''),
                    doc_id=r.get('doc-id'),
                    uri=r.get('uri'),
                    date=r.get('date'),
                    citation=r.get('citation'),
                )
                for r in (entries or [])
            ]

        return ReferencesClause(
            bblock_id=bb['itemIdentifier'],
            name=bb['name'],
            normative=parse_refs(refs_data.get('normative', [])),
            informative=parse_refs(refs_data.get('informative', [])),
            preamble_md=preamble,
            sort=refs_data.get('sort', True),
        )

    def _load_req_class(self, bb: dict, resolver: Resolver) -> Optional[ReqClass]:
        req_url = self._requirements_url(bb)
        try:
            req_data = yaml.safe_load(resolver.fetch(req_url)) or {}
        except Exception as e:
            logger.error("Could not fetch requirements.yaml for %s: %s", bb['itemIdentifier'], e)
            return None

        desc_url = bb['sourceFiles'].rstrip('/') + '/description.md'
        description_md = ''
        try:
            description_md = resolver.fetch(desc_url)
        except Exception:
            pass

        class_id = self._class_id(bb, resolver, req_data)
        req_class_uri = bb.get('req-class-uri') or resolver.req_uri(class_id)
        conf_class_uri = bb.get('conformance-class-uri') or resolver.conf_uri(class_id)

        # Test resources from compiled register metadata
        test_resources = [
            TestResource(
                url=tr['url'],
                id=tr.get('id'),
                method=tr.get('method'),
                require_fail=tr.get('require-fail', False),
            )
            for tr in bb.get('testResources', [])
            if 'url' in tr
        ]

        # Dependencies
        depends_on = []
        for dep in req_data.get('depends-on', []):
            if 'uri' in dep:
                raw_uri = dep['uri']
                if raw_uri.startswith('bblocks://'):
                    identifier = raw_uri[len('bblocks://'):]
                    dep_bb = resolver.get_bblock(identifier)
                    title = dep.get('title') or (dep_bb['name'] if dep_bb else identifier)
                    resolved_uri = resolver.resolve_bblocks_link(identifier)
                    depends_on.append(Dependency(uri=resolved_uri, title=title))
                else:
                    depends_on.append(Dependency(uri=raw_uri, title=dep.get('title')))
            elif 'bblock' in dep:
                dep_bb = resolver.get_bblock(dep['bblock'])
                dep_name = dep_bb['name'] if dep_bb else dep['bblock']
                dep_uri = resolver.conf_uri(self._class_id(dep_bb or {'itemIdentifier': dep['bblock']}, resolver))
                depends_on.append(Dependency(uri=dep_uri, title=dep_name))

        # Requirements (and recommendations / permissions)
        requirements = []
        std_target = req_data.get('standardization-target', bb.get('standardization-target', ''))
        seen_ids = set()
        for req_entry in req_data.get('requirements', []):
            req_id = req_entry['id']
            kind = req_entry.get('type', 'requirement')
            if kind not in STATEMENT_KINDS:
                raise ValueError(
                    f"{bb['itemIdentifier']}: requirement {req_id!r} has unknown type {kind!r} "
                    f"(expected one of {', '.join(STATEMENT_KINDS)})")
            if (kind, req_id) in seen_ids:
                raise ValueError(f"{bb['itemIdentifier']}: duplicate {kind} id {req_id!r}")
            seen_ids.add((kind, req_id))
            req_uri = req_entry.get('uri') or resolver.statement_uri(kind, req_id)
            default_level = {'recommendation': 'SHOULD', 'permission': 'MAY'}.get(kind, 'SHALL')

            applies_to = None
            if at := req_entry.get('applies-to'):
                applies_to = AppliesTo(
                    bblock=at.get('bblock', ''),
                    json_path=at.get('json-path'),
                    terms=[str(t) for t in at.get('terms') or []],
                    table=bool(at.get('table', True)),
                )

            req = Requirement(
                id=req_id,
                uri=req_uri,
                level=req_entry.get('level', default_level),
                statement=req_entry.get('statement', ''),
                condition=req_entry.get('condition'),
                applies_to=applies_to,
                tested_by=req_entry.get('tested-by', []),
                note=req_entry.get('note'),
                import_examples=int(req_entry.get('import-examples', 0)),
                kind=kind,
                parts=[str(p) for p in _as_list(req_entry.get('parts'))],
                prerequisites=[str(p) for p in _as_list(req_entry.get('prerequisites'))],
            )
            # Resolve tested-by to actual TestResource objects
            req.test_resources = resolver.resolve_tested_by(req.tested_by, test_resources)
            requirements.append(req)

        self._attach_imported_examples(requirements, resolver)
        examples = self._load_examples(bb, resolver)

        conf = req_data.get('conformance-class') or {}
        abstract_tests = self._parse_abstract_tests(
            req_data.get('abstract-tests', []), bb['itemIdentifier'], resolver, test_resources)

        return ReqClass(
            bblock_id=bb['itemIdentifier'],
            name=bb['name'],
            req_class_uri=req_class_uri,
            conf_class_uri=conf.get('uri') or conf_class_uri,
            standardization_target=std_target,
            description_md=description_md,
            requirements=requirements,
            depends_on=depends_on,
            test_resources=test_resources,
            examples=examples,
            title=req_data.get('title') or bb['name'],
            obligation=req_data.get('obligation', 'requirement'),
            abstract_tests=abstract_tests,
            conf_title=conf.get('title') or bb['name'],
            conf_subject=conf.get('subject', ''),
        )

    @staticmethod
    def _parse_abstract_tests(entries, bblock_id, resolver: Resolver,
                              test_resources: list[TestResource]) -> list[AbstractTest]:
        tests = []
        seen = set()
        for entry in entries or []:
            test_id = entry['id']
            if test_id in seen:
                raise ValueError(f"{bblock_id}: duplicate abstract test id {test_id!r}")
            seen.add(test_id)
            tested_by = _as_list(entry.get('tested-by'))
            tests.append(AbstractTest(
                id=test_id,
                uri=entry.get('uri') or resolver.abstract_test_uri(test_id),
                purpose=entry.get('purpose', ''),
                method=entry.get('method', ''),
                requirement_refs=[
                    {'ref': str(r.get('ref', '')), 'text': r.get('text')} if isinstance(r, dict)
                    else {'ref': str(r), 'text': None}
                    for r in _as_list(entry.get('requirement'))
                ],
                prerequisites=[str(p) for p in _as_list(entry.get('prerequisites'))],
                tested_by=tested_by,
                # Unlike requirements, an abstract test lists no test resources
                # unless it names them.
                test_resources=resolver.resolve_tested_by(tested_by, test_resources) if tested_by else [],
                bblock_id=bblock_id,
            ))
        return tests

    # ------------------------------------------------------------------
    # Examples loading
    # ------------------------------------------------------------------

    def _load_examples(self, bb: dict, resolver: Resolver) -> list[Example]:
        json_full_url = bb.get('documentation', {}).get('json-full', {}).get('url')
        if not json_full_url:
            return []
        try:
            import json as _json
            full = _json.loads(resolver.fetch(json_full_url))
        except Exception as e:
            logger.warning("Could not fetch json-full for %s: %s", bb['itemIdentifier'], e)
            return []

        examples = []
        for ex in full.get('examples', []):
            snippets = [
                Snippet(language=s.get('language', 'text'), code=s.get('code', ''))
                for s in ex.get('snippets', [])
                if s.get('code')
            ]
            examples.append(Example(
                title=ex.get('title'),
                content=ex.get('content', ''),
                snippets=snippets,
                source_bblock_id=bb['itemIdentifier'],
            ))
        return examples

    def _attach_imported_examples(self, requirements: list, resolver: Resolver) -> None:
        """
        For each requirement with import-examples > 0 and an applies-to bblock,
        fetch up to that many examples from the bblock's json-full and attach
        them directly to the requirement. Each source bblock is fetched at most once.
        """
        import json as _json
        cache: dict[str, list] = {}

        for req in requirements:
            if not req.import_examples or not req.applies_to:
                continue
            identifier = req.applies_to.bblock

            if identifier not in cache:
                bb = resolver.get_bblock(identifier)
                if not bb:
                    logger.warning("import-examples: bblock not found: %s", identifier)
                    cache[identifier] = []
                    continue

                json_full_url = bb.get('documentation', {}).get('json-full', {}).get('url')
                if not json_full_url:
                    logger.debug("import-examples: no json-full for %s", identifier)
                    cache[identifier] = []
                    continue

                try:
                    full = _json.loads(resolver.fetch(json_full_url))
                except Exception as e:
                    logger.warning("import-examples: could not fetch json-full for %s: %s", identifier, e)
                    cache[identifier] = []
                    continue

                cache[identifier] = full.get('examples', [])

            for ex in cache[identifier][:req.import_examples]:
                snippets = [
                    Snippet(language=s.get('language', 'text'), code=s.get('code', ''))
                    for s in ex.get('snippets', [])
                    if s.get('code')
                ]
                req.examples.append(Example(
                    title=ex.get('title'),
                    content=ex.get('content', ''),
                    snippets=snippets,
                    source_bblock_id=identifier,
                ))

    # ------------------------------------------------------------------
    # Annex A generation
    # ------------------------------------------------------------------

    def _build_annex_a(self, req_classes: list[ReqClass], resolver: Resolver,
                       annex_bb: Optional[dict] = None) -> AnnexA:
        """
        Explicit mode (any class defines abstract-tests, or the annex has a
        block): the annex lists conformance classes and their abstract tests,
        laid out by the annex block's description.md. Classes without
        abstract-tests then get one generated test per requirement.
        Otherwise: one entry per requirement (the original behaviour).
        """
        annex_tests: list[AbstractTest] = []
        annex_md = ''
        if annex_bb is not None:
            try:
                annex_md = resolver.fetch(self._source_file_url(annex_bb, 'description.md'))
            except Exception as e:
                logger.warning("Could not fetch description.md for %s: %s", annex_bb['itemIdentifier'], e)
            url = self._source_file_url(annex_bb, 'abstract-tests.yaml')
            data = None
            local = resolver.local_path(url)
            if local is None or local.is_file():
                try:
                    data = yaml.safe_load(resolver.fetch(url)) or {}
                except requests.HTTPError:
                    data = None
            if data:
                annex_tests = self._parse_abstract_tests(
                    data.get('abstract-tests', []), annex_bb['itemIdentifier'], resolver, [])

        if annex_bb is not None or any(rc.abstract_tests for rc in req_classes):
            for rc in req_classes:
                if not rc.abstract_tests:
                    rc.abstract_tests = [
                        AbstractTest(
                            id=req.id,
                            uri=resolver.ats_uri(req.uri),
                            purpose=req.statement or '\n\n'.join(req.parts),
                            requirement_refs=[{'ref': req.uri, 'text': None}],
                            test_resources=req.test_resources,
                            bblock_id=rc.bblock_id,
                        )
                        for req in rc.requirements if req.kind == 'requirement'
                    ]
            return AnnexA(
                explicit=True,
                req_classes=req_classes,
                tests=annex_tests,
                description_md=annex_md,
                bblock_id=annex_bb['itemIdentifier'] if annex_bb else None,
            )

        from .models import ATSEntry
        entries = []
        for rc in req_classes:
            for req in rc.requirements:
                if req.kind != 'requirement':
                    continue
                entries.append(ATSEntry(
                    uri=resolver.ats_uri(req.uri),
                    req_uri=req.uri,
                    req_id_slug=req.id_slug,
                    test_purpose=req.statement,
                    test_resources=req.test_resources,
                    bblock_id=rc.bblock_id,
                ))
        return AnnexA(entries=entries)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _class_id(self, bb: dict, resolver: Resolver, req_data: Optional[dict] = None) -> str:
        """
        Requirements class id (the path segment in /req/<id> and /conf/<id>).

        Taken from 'class-id' in the block's requirements.yaml; if absent, the
        block identifier with the standard's prefix stripped (dots become '/').
        If the standard sets 'class-id-prefix' (a full identifier prefix that
        starts with 'prefix'), that is stripped instead, so blocks can be
        grouped as <prefix>.requirements.core while the class id is 'core'.
        Blocks outside the standard that can't be read fall back to the last
        identifier segment.
        """
        identifier = bb['itemIdentifier']
        if identifier in self._class_ids:
            return self._class_ids[identifier]

        if req_data is None and 'sourceFiles' in bb:
            try:
                req_data = yaml.safe_load(resolver.fetch(self._requirements_url(bb))) or {}
            except Exception:
                req_data = {}
        class_id = (req_data or {}).get('class-id')
        if not class_id:
            prefix = self._standard['prefix'].rstrip('.') + '.'
            roots = [prefix]
            if class_prefix := self._standard.get('class-id-prefix'):
                roots.insert(0, class_prefix.rstrip('.') + '.')
            class_id = next(
                (identifier[len(r):].replace('.', '/') for r in roots
                 if identifier.startswith(r) and len(identifier) > len(r)),
                identifier.split('.')[-1])
        self._class_ids[identifier] = class_id
        return class_id

    def _load_imported_registers(self, local_register: dict) -> list[dict]:
        bblocks = []
        for url in local_register.get('imports', []):
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                reg = resp.json()
                bblocks.extend(reg.get('bblocks', []))
                logger.debug("Loaded %d bblocks from %s", len(reg.get('bblocks', [])), url)
            except Exception as e:
                logger.warning("Could not load imported register %s: %s", url, e)
        return bblocks


def _clauses_from_index(local_by_id: dict) -> list:
    """
    Fallback clause ordering when no explicit 'clauses' list is in standards.yaml.
    Sorts BBs by clause-index, wraps with conformance after first entry and annex-a at end.
    """
    sortable = [
        (bb.get('clause-index', 999), bb['itemIdentifier'])
        for bb in local_by_id.values()
        if bb.get('clause-index') is not None
    ]
    sortable.sort()

    entries = [{'bblock': ident} for _, ident in sortable]

    # Insert conformance after position 1 (i.e. after scope), append annex-a at end
    if len(entries) > 1:
        entries.insert(1, {'auto': 'conformance'})
    entries.append({'auto': 'annex-a'})
    return entries


def _load_yaml(path: Path) -> dict:
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


_LAYOUTS = ('classic', 'ogc')


def _parse_metadata(std: dict) -> StandardMetadata:
    layout = std.get('layout', 'classic')
    if layout not in _LAYOUTS:
        raise ValueError(f"standard {std.get('id')!r}: unknown layout {layout!r} "
                         f"(expected one of {', '.join(_LAYOUTS)})")
    return StandardMetadata(
        title=std['title'],
        doc_number=std.get('doc-number', ''),
        doc_type=std.get('type', 'Standard'),
        status=std.get('status', 'Draft'),
        version=std.get('version', ''),
        pub_date=std.get('pub-date'),
        base_uri=std['base-uri'],
        req_uri_template=std.get('req-uri-template', '{base-uri}/{status}req/{id}'),
        conf_uri_template=std.get('conf-uri-template', '{base-uri}/{status}conf/{id}'),
        editors=std.get('editors', []),
        wg=std.get('wg', ''),
        keywords=std.get('keywords', []),
        abstract=std.get('abstract', ''),
        boilerplate=std.get('boilerplate', {}),
        rec_uri_template=std.get('rec-uri-template', '{base-uri}/{status}rec/{id}'),
        per_uri_template=std.get('per-uri-template', '{base-uri}/{status}per/{id}'),
        ats_uri_template=std.get('ats-uri-template', '{base-uri}/{status}conf/{id}'),
        layout=layout,
        external_id=std.get('external-id', ''),
        doc_subtype=std.get('doc-subtype', ''),
        submission_date=std.get('submission-date'),
        approval_date=std.get('approval-date'),
        preface_autos=[e['auto'] for e in std.get('preface') or []
                       if isinstance(e, dict) and 'auto' in e],
    )


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]
