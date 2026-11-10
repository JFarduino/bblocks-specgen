# bblocks-specgen

A Python CLI tool that generates OGC standards documents (HTML) from a compiled
[Building Blocks](https://github.com/opengeospatial/bblocks) register.

Instead of authoring standards in AsciiDoc/Metanorma, the source lives as Building Block
definitions (YAML, Markdown, JSON Schema). `bblocks-postprocess` compiles these into a
`register.json`, and `bblocks-specgen` renders that into a structured HTML document
following OGC conventions: front matter, scope, terms & definitions, normative clauses
with requirements, conformance class table, and an abstract test suite annex.

## Implemented

- [x] Document cover page — title, editors, document number, status, version, publication date
- [x] Boilerplate — copyright notice, patent policy, license statement
- [x] Prose clauses — rendered from `description.md` per Building Block
- [x] Terms & definitions clause — loaded from `terms.yaml`, sorted alphabetically
- [x] References clause — normative and informative, loaded from `references.yaml`
- [x] Requirements classes — loaded from `requirements.yaml`; rendered as numbered requirement boxes with level (SHALL/SHOULD/MAY), statement, condition, and tested-by links
- [x] Conformance class summary table — auto-generated from all requirements classes
- [x] Abstract Test Suite (Annex A) — auto-generated ATS entries per requirement
- [x] Examples — loaded from `documentation.json-full.url`; rendered with title, prose, and code snippets per requirements class
- [x] Requirements property table — fetches the bblock JSON Schema and renders a Property / Type+Format / Required / Description table inside each requirement box
- [x] Subheading support — headings in `description.md` are normalized to start at h3 and appear in the section table of contents
- [x] Clause ordering fallback — uses `clause-index` metadata when no explicit `clauses` list is given in `standards.yaml`
- [x] Multi-register support — `--extra-register` allows importing additional compiled registers for cross-register bblock resolution
- [x] Multiple standards per repo — each `standards.yaml` entry (required `id`) is generated to `<build-dir>/<id>/`, with an index page listing them
- [x] `import-examples: N` on a requirement — pulls up to N examples from the external bblock's `json-full`, appended after any locally defined examples; each source bblock is fetched at most once per requirements class
- [x] `bblocks://` URIs in `depends-on` — resolved to bblock name and viewer URL automatically from the imported register, no hand-typed title needed
- [x] Recommendations and permissions (`type:` in `requirements.yaml`), lettered parts (A, B, C…) and prerequisites, with their own URI templates
- [x] Placement markers in `description.md` for the requirements class table and individual statements
- [x] Explicit abstract tests and conformance classes (`abstract-tests:`), with an Annex A laid out by its own block
- [x] Preface clauses (I, II…), clause title overrides, unnumbered clauses, custom boilerplate notices
- [x] `layout: ogc` — ModSpec tables, document-wide statement numbering, numbered subclauses, OGC-style cover
- [x] Subclauses (`subclauses:` in `standards.yaml`), e.g. requirements classes grouped under a clause
- [x] Statements placed in any clause (preface, prose, Annex A, numbered "A.1" there), `modspec` table options, Label/Conditions/Description rows
- [x] File includes — a fenced code block with `include=bblocks://<id>` (or a URL / relative path) shows a block's schema or any file verbatim, fetched at build time instead of copied into `description.md`, and linked to the building block that defines it

## Pending

- [ ] Requirements property table — resolve `$ref` chains, handle `allOf`/`oneOf`, show enum values and constraints
- [ ] `requirements.json` — machine-readable requirements manifest output
- [ ] Subheading hierarchical numbering (e.g. 7.1, 7.1.1) in the `classic` layout (done in `layout: ogc`)
- [ ] Unit tests (`test_resolver.py`, `test_assembler.py`)
- [ ] PDF output via WeasyPrint

See [docs/authoring.md](docs/authoring.md) for the full authoring guide: `standards.yaml`,
`requirements.yaml`, `terms.yaml`, `references.yaml`, figures, includes, and a worked example.

## Usage as a bblocks-postprocess build plugin

The recommended way to use `bblocks-specgen` is as a
[build plugin](https://ogcincubator.github.io/bblocks-docs/create/build-plugins)
of `bblocks-postprocess`: the standards are generated as part of the normal register build,
with no separate step to run.

### Setup

1. Add a `standards.yaml` at the root of your Building Blocks repository defining the
   standards to generate (see the [authoring guide](docs/authoring.md#standardsyaml)). It is read directly
   by this tool, not by `bblocks-postprocess`.
2. Declare the plugin in the repository's `bblocks-config.yaml`. All `config` keys are
   optional (the values shown are the defaults):

```yaml
plugins:
  build:
    - classes: [standard_gen.plugin.SpecgenBuildPlugin]
      pip: [bblocks-specgen @ git+https://github.com/ogcincubator/bblocks-specgen]
      config:
        standards-file: standards.yaml   # relative to the repo root
        build-dir: standards             # relative to the repo root, or absolute
        only: []                         # standard ids to generate (default: all)
        extra-registers: []              # register.json URLs or paths to import
```

Unknown config keys or wrong types abort the run. `config` requires `bblocks-postprocess`
v1.1.8 or later.

### How it runs

`standard_gen.plugin.SpecgenBuildPlugin` fires at `after_register` — once, with the
assembled register just before it is written to `register.json` (and before semantic
uplift). The hook returns the register with a top-level `standards` list added (`id`,
`title`, `version`, `status`, `doc-number`, `path` relative to the repository root, and
`url` when the register has a `baseURL`), so the published `register.json` is an index of
the generated standards.

The CLI and the plugin both funnel through the same `standard_gen.generate.generate()`
entry point, so behavior is identical either way.

### Output

Each standard is written to `<build-dir>/<id>/index.html` (even when there is only one), and
`<build-dir>/index.html` lists the available standards. Each folder also gets a `report.json`
listing every block that went into the document, the role it was given and why. `<build-dir>` defaults to `standards`.
Any failure aborts the run without writing the top-level index. Folders of standards that no
longer exist are removed, but only if this tool created them (they contain a `.specgen` marker).

## Running as a standalone CLI

The same generation can be run by hand against an already-compiled `register.json`, which is
handy for iterating locally or for repositories that don't use the plugin. Unlike the plugin,
the CLI does not add the `standards` index to `register.json`.

### Installation

```bash
git clone https://github.com/ogcincubator/bblocks-specgen.git
cd bblocks-specgen
python -m venv venv
venv/bin/pip install -e .
```

### Usage

First, run `bblocks-postprocess` in your Building Blocks repository to produce a compiled
`register.json` (typically at `build-local/register.json`). The repository must also have a
`standards.yaml` at the root (see the [authoring guide](docs/authoring.md#standardsyaml)).

```bash
venv/bin/bblocks-specgen --register path/to/build-local/register.json
```

### Options

| Option | Plugin `config` key | Description |
|---|---|---|
| `--register REGISTER_JSON` | — | Path to compiled `register.json` **(required)** |
| `--standards-file STANDARDS_YAML` | `standards-file` | Standards definitions (default: `standards.yaml` in the repo root) |
| `--root-dir DIR` | — | Repo root (default: the parent of the `register.json` directory) |
| `--only ID` | `only` | Generate only this standard id (repeatable; default: all) |
| `--build-dir DIR` | `build-dir` | Output directory (default: `standards`) |
| `--extra-register REGISTER_JSON` | `extra-registers` | Additional compiled `register.json` files to import for cross-register bblock resolution (repeatable) |
| `-v`, `--verbose` | — | Enable debug logging |

### Example

```bash
# From the root of an ogcapi-processes-standard-as-bblocks checkout:
bblocks-postprocess   # produces build-local/register.json

bblocks-specgen --register build-local/register.json

open standards/index.html
```
