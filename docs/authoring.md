# Authoring a standard with bblocks-specgen

This guide explains how to structure a repository so that `bblocks-specgen` can render it as
an OGC standards document. For installation and CLI/plugin options, see the
[README](../README.md).

## The idea

A standard is a **set of OGC Blocks** (bblocks) plus one file, `standards.yaml`, at the root
of the repository:

- each **clause** of the document is a bblock (its `description.md` is the clause text);
- a bblock that also carries a `requirements.yaml` is a **requirements class**;
- terms and references are bblocks carrying `terms.yaml` / `references.yaml`;
- `standards.yaml` gives the document metadata and lists which blocks make up the standard,
  in which order.

Nothing in `standards.yaml` is read by `bblocks-postprocess`; specgen reads it directly, and
reads the block sources from the repository (or, for blocks outside it, from their published
URLs).

```
my-standard/
├── bblocks-config.yaml        # identifier-prefix, plugins.build (see README)
├── standards.yaml             # document metadata + clause list
└── _sources/
    ├── clauses/
    │   ├── scope/            { bblock.json, description.md }
    │   ├── terms/            { bblock.json, description.md?, terms.yaml }
    │   └── references/       { bblock.json, description.md?, references.yaml }
    └── requirements/
        └── core/             { bblock.json, description.md, requirements.yaml,
                                examples.yaml, tests.yaml, … }
```

## `standards.yaml`

A list of standards, or a mapping with a top-level `standards` list. One repository can hold
several standards; each is written to `<build-dir>/<id>/index.html`.

```yaml
standards:
  - id: part1                       # required; letters, digits, _ and - only (output folder)
    prefix: my.std.part1            # required; identifier prefix of the blocks in this standard
    title: "My Standard - Part 1: Core"      # required
    base-uri: http://www.opengis.net/spec/my-std-1/1.0    # required

    req-uri-template:  "{base-uri}/{status}req/{id}"      # optional; these are the defaults
    conf-uri-template: "{base-uri}/{status}conf/{id}"
    class-id-prefix: my.std.part1.requirements.           # optional, see "Class ids"

    doc-number: OGC 24-001           # cover page
    type: Standard                   # default: Standard
    status: Draft                    # default: Draft
    version: "1.0.0"
    pub-date: null
    editors:
      - {name: Jane Doe, affiliation: Example Org}
    wg: Example SWG
    keywords: [OGC API, example]
    abstract: |
      What this standard specifies.
    boilerplate:
      copyright-year: 2026
      patent-policy: OGC RF          # adds the patent-policy warning when "OGC RF"
      license: OGC Document Notice   # adds the license agreement text when present

    clauses:                         # optional; document order, see below
      - my.std.part1.clauses.scope
      - {auto: conformance}
      - my.std.part1.clauses.terms
      - my.std.part1.requirements.core
      - {auto: annex-a}
      - my.std.part1.annexes.history
```

### URI templates

`{base-uri}` is the standard's `base-uri`; `{id}` is the requirement or class id;
`{status}` is `draft/` when `status` is `Draft` (case-insensitive) and empty otherwise. With
the defaults, requirements class `core` gets `…/1.0/draft/req/core` and conformance class
`…/1.0/draft/conf/core`.

### `clauses`

Entries are block identifiers (or `{bblock: <identifier>}`), in document order. Only blocks
whose identifier starts with `prefix` are considered; a missing block is a warning.

- `{auto: conformance}` — a conformance clause with a table of all requirements classes in
  the standard. It is numbered like any other clause, so put it where you want it.
- `{auto: annex-a}` — the Abstract Test Suite annex, generated from the requirements.
- **Annexes:** every prose, terms or references clause listed *after* `{auto: annex-a}` is
  numbered as an annex (B, C, …) instead of a numbered clause.

If `clauses` is omitted, blocks with a `clause-index` number in `bblock.json` are used, sorted
by it, with the conformance clause after the first block and the ATS annex last. Prefer an
explicit list: it keeps document structure in one place.

## How a block becomes part of the document

Which role each block gets is decided from its contents:

| The block… | Role |
|---|---|
| has `itemClass: terms` | terms and definitions clause (`terms.yaml`) |
| has `itemClass: references` | references clause (`references.yaml`) |
| has a `requirements.yaml` | requirements class — **whatever its `itemClass`** (so a `model` block can carry requirements) |
| none of the above | prose clause (`description.md`) |

After a run, `<build-dir>/<id>/report.json` lists every block that went in, the role it got
and why. Check it when something is not rendered the way you expect. A block with
`itemClass: clause` that also has a `requirements.yaml`, or `itemClass: requirements-class`
without one, produces a warning.

The block's `name` (from `bblock.json`) is the clause title. `normative: false` in
`bblock.json` marks a prose clause as informative.

## Prose clauses: `description.md`

Plain Markdown. Notes:

- **Headings** are shifted so that the shallowest heading in the file becomes `###` (the
  clause title itself is the `##`), and appear in the table of contents. Write the file as
  you would normally, starting with `#` or `##`.
- **Links to other blocks:** `[text](bblocks://<identifier>)` links to the block's clause if it
  is in this document, or to its page in the viewer if it comes from an imported register.
- **Figures:** see below.
- The Markdown dialect is CommonMark plus tables.

### Figures

A captioned image alone in its paragraph is a numbered figure:

```markdown
![Core classes of the model](assets/core.svg)
```

renders as `Figure 3 — Core classes of the model` below the image ("Figure A.1" in annexes).
Captions may use inline Markdown. An image without a caption, or inside running text, is a
plain image.

To refer to a figure, link to the image path; the link text defaults to "Figure N":

```markdown
The classes are shown in [](assets/core.svg), and [this diagram](assets/core.svg) too.
```

Details:

- Paths are relative to the block's own source directory. Local images are copied to
  `<id>/assets/<block>/`; a missing image aborts the run. Remote URLs work too (numbered and
  referenceable, not copied). Absolute URLs under the register's `baseURL` are mapped to the
  local file.
- **An image is shown once**, where first included. Repeats (same or other block) are
  omitted; references point to the first.
- To give a figure a stable anchor (or to say which of two images wins), add an id:
  `![Caption](assets/core.svg){#fig-core}` and refer to it with `[](#fig-core)`.
- References may point forward. A link to an image that is not a figure produces a warning.
- Prefer SVG over PNG when you have both.

## Terms: `terms.yaml`

```yaml
terms:
  - term: coordinate operation
    definition: >
      Process for changing coordinates in a source CRS to coordinates in a target CRS.
    sources:                       # optional
      - title: ISO 19111:2019
        uri: https://www.iso.org/standard/74039.html
    note: Optional note.           # optional; shown as an editor's note

abbreviated-terms:                 # optional; abbreviation: expansion
  CRS: coordinate reference system
```

Terms are sorted alphabetically. `description.md` in the same block, if present, is shown as
the introduction of the clause.

## References: `references.yaml`

```yaml
normative:
  - title: "OpenAPI Specification 3.0"      # required
    doc-id: OAS 3.0                          # sort key and citation label
    uri: http://spec.openapis.org/oas/v3.0.4
    date: "2020"
informative:
  - title: "…"
    doc-id: …
```

`description.md`, if present, is shown before the lists.

## Requirements classes: `requirements.yaml`

```yaml
class-id: core                  # optional, see "Class ids"
standardization-target: Web API # optional

depends-on:                     # optional: other conformance classes this one requires
  - bblock: my.std.part1.requirements.common     # a class in this standard
  - uri: bblocks://some.other.block               # resolved to the block's name and link
  - uri: http://example.org/spec/conf/x           # any URI
    title: Some other class

requirements:
  - id: core/landing-page       # required; the URI is derived as <req-uri-template> with this id
    level: SHALL                # default SHALL; also SHOULD, MAY, SHALL NOT, SHOULD NOT
    statement: >                # Markdown
      The server SHALL support the HTTP GET operation at the path `/`.
    condition: …                # optional; shown with the requirement
    note: …                     # optional
    applies-to:                 # optional
      bblock: my.std.api.paths.LandingPage
      json-path: $.properties.links   # optional, default $; selects the schema node
    tested-by:                  # optional; ids or file names from the block's tests.yaml
      - landing-page-success
    import-examples: 2          # optional: pull up to N examples from the applies-to block
```

Notes:

- **Requirement ids** conventionally start with the class id (`core/landing-page`): the id is
  substituted verbatim into the URI template, so `core/landing-page` gives `…/req/core/landing-page`.
- **`applies-to`** links the requirement to a block, and, when that block has a JSON Schema,
  adds a property table (name, type, required, description) for the selected `json-path`.
- **`tested-by`**: matched, in order, against the `id` and then the file name (with or
  without extension) of the block's test resources (`tests.yaml`, as published in
  `register.json`). If omitted, all of the block's test resources are listed. Each
  requirement also yields an entry in the Abstract Test Suite annex.
- **Examples:** the block's own `examples.yaml` examples are listed after the requirements
  (they come from the block's `json-full` documentation). `import-examples` appends examples
  of *another* block under a requirement. Images in an example are resolved against the block
  the example comes from.
- **`description.md`** is the class introduction. To put text *after* the requirements
  instead, insert the line `<!-- requirements -->`: everything before it is shown above the
  requirements, everything after it below.
- `req-class-uri` and `conformance-class-uri` in `bblock.json` override the derived class URIs.

### Class ids

The class id is the segment in `…/req/<id>` and `…/conf/<id>`. It is, in order of
precedence:

1. `class-id` in the block's `requirements.yaml`;
2. the block identifier with `class-id-prefix` removed, if the standard sets one;
3. the block identifier with the standard's `prefix` removed;
4. (blocks outside the standard) the last segment of the identifier.

Dots become `/`. Use `class-id-prefix` to keep requirements grouped in the register without the
group leaking into URIs: with `prefix: my.std.part1` and
`class-id-prefix: my.std.part1.requirements.`, the block `my.std.part1.requirements.core` gets
the class id `core`. `class-id-prefix` must start with `prefix`.

### Recommendations, permissions and lettered parts

A `requirements.yaml` entry can also be a recommendation or a permission (ModSpec), and can be
split into lettered parts (A, B, C…), as in published OGC documents:

```yaml
requirements:
  - id: core/deploy-body              # → {req-uri-template}: …/req/core/deploy-body
    parts:
      - The body SHALL contain …
      - The `Content-Type` header SHALL …
  - id: core/deploy-body              # same id is fine for a different type
    type: permission                  # requirement (default) | recommendation | permission
    statement: A server MAY …         # → {per-uri-template}: …/per/core/deploy-body
  - id: cwl/multipart
    type: permission
    prerequisites: [RFC 2387 (The MIME Multipart/Related Content-type)]
    statement: Introduction shown as "Statement" when there are parts.
    parts: [A server MAY …, Parts SHOULD …]
```

A statement can also carry the ModSpec rows `label`, `conditions` and `description` (Markdown),
shown as "Label", "Conditions" and "Description" before its parts.

`level` defaults to SHALL, SHOULD and MAY respectively. URIs come from `rec-uri-template` and
`per-uri-template` (defaults `{base-uri}/{status}rec/{id}` and `{base-uri}/{status}per/{id}`).
`applies-to: {bblock: …, table: false}` links the block without adding its property table (e.g.
when the statement already quotes the schema). Other class-level keys: `title` (title of the
requirements class table, default: the block name), `obligation` (default `requirement`).

### Placing the class table and statements in the text

By default the class table comes first and all statements follow the intro text, under a
"Requirements" heading. Markers on their own line in `description.md` place them instead:

```markdown
## Overview
<!-- requirements-class -->
…
## Deploy
<!-- requirement: core/deploy-body -->
<!-- permission: core/deploy-body -->
<!-- recommendation: core/deploy-body-ogcapppkg -->
```

Statements without a marker still go under the "Requirements" heading (which is omitted when
every statement is placed). Statements are numbered in display order.

A statement marker can also be put in another clause: a prose clause, a preface clause or the
Annex A block (for example a recommendation of the "Job list" class shown in the security
considerations). The statement is then shown there instead, and numbered there: in Annex A it
gets an annex number ("Requirement A.1"). Its class table still lists it.

### Explicit abstract tests

Instead of one generated Annex A entry per requirement, a class can list its abstract tests:

```yaml
conformance-class:
  title: Deploy, Replace, Undeploy   # default: the block name
  subject: …                         # optional
abstract-tests:
  - id: dru/deploy-post-op           # → {ats-uri-template} (default {base-uri}/{status}conf/{id})
    requirement: deploy-replace-undeploy/deploy-post-op   # id, identifier (/req/…) or URI; or a list
    purpose: Validate that …
    method: |
      1. Issue an HTTP POST request …
    tested-by: [deploy-success]      # optional, from the block's tests.yaml
  - id: dru/other
    requirement: {ref: deploy-replace-undeploy/x, text: /req/shown/as/written}
```

`conformance-class` can also give `prerequisites` (Markdown, one per entry). `abstract-tests: []`
says that the class has no abstract tests: none are then generated for it.

A requirement reference that resolves is linked (and shown as "Requirement N: /req/…" in the
`ogc` layout unless `text` is given); one that does not is shown as written, with a warning.
To lay out the annex, give it a block: `{auto: annex-a, bblock: my.std.annexes.ats}`. Its
`description.md` is the annex text, with `<!-- conformance-class: <class block id> -->` and
`<!-- abstract-test: <id> -->` markers; an optional `abstract-tests.yaml` (`abstract-tests:` list)
next to it holds tests that belong to no conformance class. As soon as one class has
abstract tests (or the annex has a block), classes without any get one generated test per
requirement.

## Document layout

### Preface, clause titles and unnumbered clauses

```yaml
preface:                          # before clause 1, numbered I, II, III…
  - auto: abstract                # the 'abstract' (Markdown); no longer shown on the cover
  - auto: keywords
  - bblock: my.std.clauses.security
clauses:
  - bblock: my.std.requirements.core
    title: Requirements Class “Core”      # overrides the block name
  - bblock: my.std.annexes.bibliography
    numbered: false                       # e.g. a closing Bibliography
```

A clause can list `subclauses`, rendered as its subclauses after its own text: a group clause
"10" whose `description.md` has an "Overview" heading (10.1) gets 10.2, 10.3… for the listed
blocks, typically requirements classes:

```yaml
clauses:
  - bblock: my.std.clauses.encodings       # its description.md: "# Overview" -> 10.1
    subclauses:
      - bblock: my.std.requirements.json   # 10.2
        title: Requirements Class “JSON”
      - bblock: my.std.requirements.html   # 10.3
```

In `terms.yaml` and `references.yaml`, `sort: false` keeps the order of the file. A reference
can give its full `citation` (Markdown) instead of `doc-id`/`title`/`date`, and an `anchor` (the
HTML id of the entry) so that the text can link to it: `[OGC 18-062r2](#OAProc-1)`.

### `layout: ogc`

`layout: ogc` in a standard's entry renders it like a published OGC (Metanorma) document:

- statements, requirements classes, conformance classes and abstract tests as ModSpec tables
  (Identifier, Included in, A/B/C parts…), identifiers shown relative to `base-uri`
  (`/req/core/x`);
- statements numbered across the document per kind (Requirement 1…n, Permission 1…n);
- headings in clause text numbered (6.1, 6.1.2…), with `sec-<number>` anchors, so that
  `[Clause 6.2](#sec-6.2)` links work, preface headings lettered (IV.A); terms rendered as
  subclauses; a `#` line inside a fenced code block is not a heading;
- a table of contents listing subclauses down to 6.1.2.3, without the terms;
- annexes titled "Annex A (normative) …" (`normative: false` → informative);
- an OGC-style cover (`external-id`, `doc-subtype`, `submission-date`, `approval-date`,
  `pub-date`, version);
- its styles in a separate `ogc-layout.css`, copied and linked only for this layout, so that
  documents in the `classic` layout keep their exact output.

`modspec` adjusts the ModSpec tables to the document being reproduced:

```yaml
modspec:
  statement-label: Statement   # header of a statement without parts (default: A)
  singular-labels: true        # "Prerequisite", "Normative statement", "Conformance test"
                               # when there is only one (default: false)
  obligation-row: false        # no "Obligation" row in class tables (default: true)
```

`boilerplate.notices` (both layouts) replaces the default copyright/patent/license text with
your own titled Markdown blocks:

```yaml
boilerplate:
  copyright-year: 2019
  notices:
    - {title: Notice for Drafts, text: "This document is not an OGC Standard. …"}
```

The default layout (`classic`) is unchanged.

## Model blocks and ontology blocks as requirements classes

A requirements class is not tied to `itemClass: requirements-class`. A block of any
`itemClass` (for example `model`) that has a `requirements.yaml` becomes a requirements class
and keeps all of its normal bblock content (schema, ontology, examples, tests). Set
`class-id` explicitly when its identifier does not carry a meaningful class path.

### Ontology tables

A requirement with `applies-to: {bblock: <id>}` gets a property table built from the block's
JSON Schema. If the block has no schema properties but does have an `ontology` in the
register (an `ontology.ttl`), the requirement gets a "classes" table (subclass-of,
definition) and a "properties" table (domain, range, definition) instead, listing the terms
the ontology itself defines (named classes and object/datatype/annotation properties).
Labels and definitions prefer English. By default the tables list the whole ontology; add
`terms` to `applies-to` to list only some of it (local names, or CURIEs whose prefix is
ignored):

```yaml
applies-to:
  bblock: my.std.model
  terms: [CoordinateOperation, geosrs:SingleOperation]
```

## A minimal complete example

`bblocks-config.yaml` (excerpt):

```yaml
identifier-prefix: my.std.
plugins:
  build:
    - classes: [standard_gen.plugin.SpecgenBuildPlugin]
      pip: [bblocks-specgen @ git+https://github.com/ogcincubator/bblocks-specgen]
```

`standards.yaml`:

```yaml
standards:
  - id: core
    prefix: my.std.
    title: My Standard
    base-uri: http://www.opengis.net/spec/my-std/1.0
    doc-number: OGC 24-001
    clauses:
      - my.std.scope
      - {auto: conformance}
      - my.std.model
      - {auto: annex-a}
```

`_sources/scope/bblock.json` (`name`, `status`, `dateTimeAddition`, `itemClass` and `version` are
the only fields the bblocks schema requires; see the bblocks-postprocess docs for the rest):

```json
{ "name": "Scope", "itemClass": "clause", "status": "under-development",
  "dateTimeAddition": "2026-01-01T00:00:00Z", "version": "1.0" }
```

`_sources/scope/description.md`:

```markdown
This standard defines …
```

`_sources/model/bblock.json` — a model block that is also a requirements class:

```json
{ "name": "Model", "itemClass": "model", "status": "under-development",
  "dateTimeAddition": "2026-01-01T00:00:00Z", "version": "1.0" }
```

`_sources/model/requirements.yaml`:

```yaml
class-id: model
requirements:
  - id: model/classes
    statement: An implementation SHALL use the classes defined in [](assets/classes.svg).
```

`_sources/model/description.md`:

```markdown
## Classes

![Classes of the model](assets/classes.svg)
```

`_sources/model/assets/classes.svg` — the image must exist, or the run aborts:

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60">
  <rect x="10" y="10" width="180" height="40" fill="none" stroke="black"/>
  <text x="100" y="35" text-anchor="middle">Feature</text>
</svg>
```

Run `bblocks-postprocess` (which runs the plugin), then open
`standards/core/index.html`; `standards/core/report.json` shows how each block was used.

## Troubleshooting

- **A block shows up as prose but should be a requirements class** — check `report.json`:
  specgen did not find `requirements.yaml` next to the block's `bblock.json`.
- **URIs look like `/req/requirements/core`** — the class id defaults to the identifier minus
  `prefix`; set `class-id-prefix` or `class-id`.
- **The run aborts on an image** — the image path is relative to the *block's* source
  directory, not to the repository root or to `standards.yaml`.
- **Any failure aborts the whole run**; the top-level index is only written after every
  standard has rendered successfully.
