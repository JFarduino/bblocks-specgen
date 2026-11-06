from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class AppliesTo:
    bblock: str
    json_path: Optional[str] = None
    terms: list[str] = field(default_factory=list)
    # False: only link the block, without the property/ontology table
    table: bool = True


# Kinds of normative statement (ModSpec), with the URI path segment and the
# HTML anchor prefix of each.
STATEMENT_KINDS = {
    'requirement': {'label': 'Requirement', 'segment': 'req', 'anchor': 'req'},
    'recommendation': {'label': 'Recommendation', 'segment': 'rec', 'anchor': 'rec'},
    'permission': {'label': 'Permission', 'segment': 'per', 'anchor': 'per'},
}


@dataclass
class TestResource:
    url: str
    id: Optional[str] = None
    method: Optional[str] = None
    require_fail: bool = False


@dataclass
class Requirement:
    id: str           # e.g. "core/process-list-op"
    uri: str          # full derived URI
    level: str        # SHALL / SHOULD / MAY / SHALL NOT / SHOULD NOT
    statement: str
    condition: Optional[str] = None
    applies_to: Optional[AppliesTo] = None
    tested_by: list[str] = field(default_factory=list)
    note: Optional[str] = None
    test_resources: list[TestResource] = field(default_factory=list)
    import_examples: int = 0
    examples: list['Example'] = field(default_factory=list)
    # 'requirement', 'recommendation' or 'permission' (see STATEMENT_KINDS)
    kind: str = 'requirement'
    # Lettered parts (A, B, C...) of the statement, Markdown each; shown
    # after 'statement' (which then acts as an introduction), if any.
    parts: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    # Assigned by the Assembler
    label: str = ''          # "Requirement 3", "Permission 6/1"...
    display_id: str = ''     # identifier as shown (URI, or relative to base-uri)

    @property
    def id_slug(self) -> str:
        return self.id.replace('/', '-').replace(' ', '-')

    @property
    def kind_label(self) -> str:
        return STATEMENT_KINDS.get(self.kind, STATEMENT_KINDS['requirement'])['label']

    @property
    def anchor(self) -> str:
        prefix = STATEMENT_KINDS.get(self.kind, STATEMENT_KINDS['requirement'])['anchor']
        return f'{prefix}-{self.id_slug}'


@dataclass
class TestRequirementRef:
    """A requirement referenced by an abstract test."""
    text: str                      # identifier shown (as written, or the resolved one)
    anchor: Optional[str] = None   # in-document anchor when it could be resolved
    label: str = ''                # "Requirement 6" when resolved and no explicit text


@dataclass
class AbstractTest:
    """An explicitly authored abstract test (abstract-tests in requirements.yaml)."""
    id: str
    uri: str
    purpose: str = ''
    method: str = ''
    # [{'ref': id / identifier / URI, 'text': optional text shown instead}]
    requirement_refs: list[dict] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    tested_by: list[str] = field(default_factory=list)
    test_resources: list[TestResource] = field(default_factory=list)
    bblock_id: Optional[str] = None
    # Assigned by the Assembler
    label: str = ''
    display_id: str = ''
    requirements: list[TestRequirementRef] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        return 'ats-' + self.id.replace('/', '-').replace(' ', '-')


@dataclass
class ATSEntry:
    uri: str
    req_uri: str
    req_id_slug: str
    test_purpose: str
    test_resources: list[TestResource] = field(default_factory=list)
    # Block whose requirement this tests; relative links/images in test_purpose
    # (a copy of the requirement statement) resolve against it.
    bblock_id: Optional[str] = None


@dataclass
class Dependency:
    uri: str
    title: Optional[str] = None


@dataclass
class Snippet:
    language: str
    code: str


@dataclass
class Example:
    title: Optional[str]
    content: str
    snippets: list[Snippet] = field(default_factory=list)
    # Block the example comes from: relative links and images in 'content'
    # are relative to its source directory (may differ from the block
    # that displays it, for imported examples).
    source_bblock_id: Optional[str] = None


@dataclass
class ReqClass:
    bblock_id: str
    name: str
    req_class_uri: str
    conf_class_uri: str
    standardization_target: str
    description_md: str
    requirements: list[Requirement] = field(default_factory=list)
    depends_on: list[Dependency] = field(default_factory=list)
    test_resources: list[TestResource] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    # Title of the requirements class table (default: the block name)
    title: str = ''
    obligation: str = 'requirement'
    abstract_tests: list[AbstractTest] = field(default_factory=list)
    conf_title: str = ''
    conf_subject: str = ''
    # Assigned by the Assembler
    label: str = ''               # "Requirements class 1"
    conf_label: str = ''          # "Conformance class A.1"
    display_order: list[Requirement] = field(default_factory=list)
    unplaced: list[Requirement] = field(default_factory=list)

    @property
    def conf_anchor(self) -> str:
        return 'conf-' + re.sub(r'[^A-Za-z0-9]+', '-', self.conf_class_uri.rstrip('/').split('/conf/')[-1]).strip('-')


@dataclass
class Term:
    term: str
    definition: str
    sources: list[dict] = field(default_factory=list)
    note: Optional[str] = None


@dataclass
class Reference:
    title: str
    doc_id: Optional[str] = None
    uri: Optional[str] = None
    date: Optional[str] = None
    # Full citation (Markdown); replaces the doc-id/title/date rendering
    citation: Optional[str] = None


@dataclass
class ProseClause:
    bblock_id: str
    name: str
    description_md: str
    normative: bool = True


@dataclass
class TermsClause:
    bblock_id: str
    name: str
    terms: list[Term] = field(default_factory=list)
    abbreviated_terms: dict[str, str] = field(default_factory=dict)
    preamble_md: str = ""


@dataclass
class ReferencesClause:
    bblock_id: str
    name: str
    normative: list[Reference] = field(default_factory=list)
    informative: list[Reference] = field(default_factory=list)
    preamble_md: str = ""
    sort: bool = True


@dataclass
class ConformanceClause:
    req_classes: list[ReqClass] = field(default_factory=list)


@dataclass
class AnnexA:
    entries: list[ATSEntry] = field(default_factory=list)
    # Explicit mode: the classes' abstract-tests (plus the annex block's own
    # tests), laid out by the annex block's description.md if one is given.
    explicit: bool = False
    req_classes: list[ReqClass] = field(default_factory=list)
    tests: list[AbstractTest] = field(default_factory=list)
    description_md: str = ''
    bblock_id: Optional[str] = None
    # Assigned by the Assembler: classes and tests not placed by a marker
    unplaced_classes: list[ReqClass] = field(default_factory=list)
    unplaced_tests: dict = field(default_factory=dict)   # class bblock_id (or None) -> [AbstractTest]


@dataclass
class ClauseEntry:
    """A clause as listed in standards.yaml, with its per-entry options."""
    content: Any
    title: Optional[str] = None      # overrides the block name as clause title
    numbered: bool = True
    preface: bool = False


@dataclass
class SubSection:
    title: str
    anchor: str
    level: int  # normalized heading level (3 = top-level below the section h2)
    number: str = ''


@dataclass
class Section:
    number: str       # "1", "2", "A", "III", or "" when unnumbered
    title: str
    anchor: str
    content: Any      # one of the clause types above
    subsections: list['SubSection'] = field(default_factory=list)
    kind: str = 'clause'   # 'preface', 'clause' or 'annex'
    normative: bool = True


@dataclass
class StandardMetadata:
    title: str
    doc_number: str
    doc_type: str
    status: str
    version: str
    pub_date: Optional[str]
    base_uri: str
    req_uri_template: str
    conf_uri_template: str
    editors: list[dict]
    wg: str
    keywords: list[str]
    abstract: str
    boilerplate: dict
    rec_uri_template: str = '{base-uri}/{status}rec/{id}'
    per_uri_template: str = '{base-uri}/{status}per/{id}'
    ats_uri_template: str = '{base-uri}/{status}conf/{id}'
    # 'classic' (default) or 'ogc': ModSpec tables, document-wide statement
    # numbering and numbered subclauses, as in published OGC documents.
    layout: str = 'classic'
    external_id: str = ''
    doc_subtype: str = ''
    submission_date: Optional[str] = None
    approval_date: Optional[str] = None
    # Preface entries make the cover skip the abstract/keywords they render
    preface_autos: list[str] = field(default_factory=list)

    @property
    def ogc_layout(self) -> bool:
        return self.layout == 'ogc'


@dataclass
class StandardDocument:
    metadata: StandardMetadata
    sections: list[Section] = field(default_factory=list)
