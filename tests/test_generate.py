import json
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

from standard_gen.generate import generate, load_standards
from standard_gen.plugin import SpecgenBuildPlugin


def _std(std_id, prefix=None, title=None):
    return {
        'id': std_id,
        'prefix': prefix or f'test.{std_id}.',
        'title': title or f'Standard {std_id} & co',
        'doc-number': '24-001',
        'base-uri': 'http://example.com/spec',
        'req-uri-template': 'http://example.com/req/{class}',
        'conf-uri-template': 'http://example.com/conf/{class}',
        'clauses': [],
    }


class _TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_standards(self, text):
        path = self.root / 'standards.yaml'
        path.write_text(textwrap.dedent(text), encoding='utf-8')
        return path


class LoadStandardsTest(_TmpDirCase):
    def test_valid_list_and_mapping(self):
        path = self.write_standards('''
            - {id: a, prefix: x.a., title: A, base-uri: 'http://e.org/a'}
            - {id: b_2-c, prefix: x.b., title: B, base-uri: 'http://e.org/b'}
        ''')
        self.assertEqual([s['id'] for s in load_standards(path)], ['a', 'b_2-c'])
        path = self.write_standards('''
            standards:
              - {id: a, prefix: x.a., title: A, base-uri: 'http://e.org/a'}
        ''')
        self.assertEqual(len(load_standards(path)), 1)

    def test_missing_id(self):
        path = self.write_standards('- {prefix: x.a., title: A}')
        with self.assertRaisesRegex(ValueError, "missing required 'id'"):
            load_standards(path)

    def test_duplicate_id(self):
        path = self.write_standards('''
            - {id: a, prefix: x.a., title: A, base-uri: u}
            - {id: a, prefix: x.b., title: B, base-uri: u}
        ''')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            load_standards(path)

    def test_unsafe_id(self):
        path = self.write_standards('- {id: ../evil, prefix: x., title: A, base-uri: u}')
        with self.assertRaisesRegex(ValueError, 'invalid id'):
            load_standards(path)

    def test_missing_title_prefix_and_base_uri(self):
        for entry, key in (('{id: a, prefix: x., base-uri: u}', 'title'), ('{id: a, title: A, base-uri: u}', 'prefix'),
                           ('{id: a, prefix: x., title: A}', 'base-uri')):
            path = self.write_standards(f'- {entry}')
            with self.assertRaisesRegex(ValueError, key):
                load_standards(path)

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_standards(self.root / 'nope.yaml')


class GenerateTest(_TmpDirCase):
    register = {'bblocks': []}

    def test_one_folder_per_standard_and_index(self):
        build = self.root / 'standards'
        out = generate(self.register, [_std('a'), _std('b')], source_dir=self.root, build_dir=build)
        self.assertEqual(out, build / 'index.html')
        self.assertTrue((build / 'a' / 'index.html').is_file())
        self.assertTrue((build / 'b' / 'index.html').is_file())
        index = out.read_text(encoding='utf-8')
        self.assertIn('The following standards are available', index)
        self.assertIn('href="a/index.html"', index)
        self.assertIn('href="b/index.html"', index)
        self.assertIn('Standard a &amp; co', index)  # escaped
        self.assertIn('24-001', index)  # subtitle

    def test_single_standard_still_gets_folder_and_index(self):
        build = self.root / 'standards'
        generate(self.register, [_std('only')], source_dir=self.root, build_dir=build)
        self.assertTrue((build / 'only' / 'index.html').is_file())
        self.assertIn('href="only/index.html"', (build / 'index.html').read_text(encoding='utf-8'))

    def test_only_filter(self):
        build = self.root / 'standards'
        generate(self.register, [_std('a'), _std('b')], source_dir=self.root, build_dir=build,
                 only=['b'])
        self.assertFalse((build / 'a').exists())
        self.assertTrue((build / 'b' / 'index.html').is_file())

    def test_only_unknown_id(self):
        with self.assertRaisesRegex(ValueError, 'Unknown standard id'):
            generate(self.register, [_std('a')], source_dir=self.root,
                     build_dir=self.root / 'standards', only=['zzz'])

    def test_failure_aborts_without_index(self):
        build = self.root / 'standards'
        broken = _std('b')
        del broken['base-uri']
        with self.assertRaises(KeyError):
            generate(self.register, [_std('a'), broken], source_dir=self.root, build_dir=build)
        self.assertFalse((build / 'index.html').exists())

    def test_stale_cleanup_only_touches_marked_folders(self):
        build = self.root / 'standards'
        generate(self.register, [_std('old'), _std('keep')], source_dir=self.root, build_dir=build)
        (build / 'handmade').mkdir()
        (build / 'handmade' / 'x.txt').write_text('mine')
        generate(self.register, [_std('keep')], source_dir=self.root, build_dir=build)
        self.assertFalse((build / 'old').exists())
        self.assertTrue((build / 'keep').exists())
        self.assertTrue((build / 'handmade' / 'x.txt').exists())

    def test_build_dir_outside_root_is_allowed(self):
        with tempfile.TemporaryDirectory() as other:
            generate(self.register, [_std('a')], source_dir=self.root, build_dir=Path(other) / 'out')
            self.assertTrue((Path(other) / 'out' / 'a' / 'index.html').is_file())

    def test_build_dir_containing_root_is_rejected(self):
        for bad in (self.root, self.root.parent):
            with self.assertRaisesRegex(ValueError, 'contains the repository root'):
                generate(self.register, [_std('a')], source_dir=self.root, build_dir=bad)


class PluginTest(_TmpDirCase):
    def test_config_validation(self):
        SpecgenBuildPlugin()
        SpecgenBuildPlugin({})
        SpecgenBuildPlugin({'build-dir': 'out', 'only': ['a'], 'extra-registers': []})
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            SpecgenBuildPlugin({'build_dir': 'out'})
        with self.assertRaises(TypeError):
            SpecgenBuildPlugin({'only': 'a'})
        with self.assertRaises(TypeError):
            SpecgenBuildPlugin({'build-dir': 3})

    def test_after_register_uses_root_dir_defaults_and_indexes_standards(self):
        self.write_standards('''
            - {id: a, prefix: x.a., title: A, base-uri: http://e/, req-uri-template: r, conf-uri-template: c, clauses: []}
        ''')
        register = {'bblocks': [], 'baseURL': 'http://e/site/'}
        result = SpecgenBuildPlugin().after_register(register, {'rootDir': str(self.root)})
        self.assertTrue((self.root / 'standards' / 'a' / 'index.html').is_file())
        self.assertTrue((self.root / 'standards' / 'index.html').is_file())
        self.assertEqual(result['standards'], [{
            'id': 'a', 'title': 'A', 'status': 'Draft',
            'path': 'standards/a', 'url': 'http://e/site/standards/a/',
        }])
        self.assertNotIn('standards', register)
        self.assertEqual(result['bblocks'], [])

    def test_after_register_without_base_url_or_outside_root_has_no_url(self):
        self.write_standards('''
            - {id: a, prefix: x.a., title: A, base-uri: http://e/, req-uri-template: r, conf-uri-template: c, clauses: []}
        ''')
        result = SpecgenBuildPlugin().after_register({'bblocks': []}, {'rootDir': str(self.root)})
        self.assertEqual(result['standards'][0]['path'], 'standards/a')
        self.assertNotIn('url', result['standards'][0])
        with tempfile.TemporaryDirectory() as other:
            result = SpecgenBuildPlugin({'build-dir': other}).after_register(
                {'bblocks': [], 'baseURL': 'http://e/'}, {'rootDir': str(self.root)})
        self.assertEqual(set(result['standards'][0]), {'id', 'title', 'status'})

    def test_after_register_custom_standards_file(self):
        (self.root / 'conf').mkdir()
        (self.root / 'conf' / 's.yaml').write_text(
            '- {id: z, prefix: x.z., title: Z, base-uri: http://e/, '
            'req-uri-template: r, conf-uri-template: c, clauses: []}\n')
        SpecgenBuildPlugin({'standards-file': 'conf/s.yaml', 'build-dir': 'out'}).after_register(
            {'bblocks': []}, {'rootDir': str(self.root)})
        self.assertTrue((self.root / 'out' / 'z' / 'index.html').is_file())

    write_standards = _TmpDirCase.write_standards


if __name__ == '__main__':
    unittest.main()


class RequirementsDetectionTest(_TmpDirCase):
    BASE = 'http://example.com/src/'

    def _generate(self, blocks, files, annex=False):
        for rel, text in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        register = {
            'baseURL': self.BASE,
            'bblocks': [
                {'itemIdentifier': ident, 'name': ident, 'sourceFiles': f'{self.BASE}{ident}/',
                 **extra}
                for ident, extra in blocks.items()
            ],
        }
        std = _std('s', prefix='test.s.')
        std['req-uri-template'] = 'http://example.com/req/{id}'
        std['conf-uri-template'] = 'http://example.com/conf/{id}'
        std['clauses'] = [{'bblock': i} for i in blocks] + ([{'auto': 'annex-a'}] if annex else [])
        out = generate(register, [std], source_dir=self.root, build_dir=self.root / 'out')
        report = json.loads((out.parent / 's' / 'report.json').read_text())
        html = (out.parent / 's' / 'index.html').read_text()
        return {b['bblock']: b for b in report['blocks']}, html

    def test_model_block_with_requirements_is_a_class(self):
        report, html = self._generate(
            {'test.s.co': {'itemClass': 'model'}, 'test.s.intro': {'itemClass': 'clause'}},
            {
                'test.s.co/requirements.yaml': (
                    'requirements:\n  - {id: r1, statement: do it}\n'),
                'test.s.co/description.md': 'Co.',
                'test.s.intro/description.md': 'Intro.',
            },
        )
        self.assertEqual(report['test.s.co']['role'], 'requirements-class')
        self.assertEqual(report['test.s.intro']['role'], 'prose')
        self.assertIn('http://example.com/conf/co', html)

    def test_figure_link_in_requirement_resolves_in_ats_annex(self):
        with self.assertNoLogs('standard_gen.figures', level='WARNING'):
            report, html = self._generate(
                {'test.s.m': {'itemClass': 'model'}},
                {
                    'test.s.m/requirements.yaml': (
                        'requirements:\n  - {id: r1, statement: "Use [](assets/a.png)."}\n'),
                    'test.s.m/description.md': '![Cap](assets/a.png)\n',
                    'test.s.m/assets/a.png': 'x',
                },
                annex=True,
            )
        self.assertEqual(html.count('class="figure-ref">Figure 1</a>'), 2)

    def test_ontology_tables_for_model_block_without_schema(self):
        ttl = (
            '@prefix ex: <http://example.com/o#> .\n'
            '@prefix owl: <http://www.w3.org/2002/07/owl#> .\n'
            '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
            '@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n'
            'ex:Thing a owl:Class ; rdfs:label "Thing"@en ; skos:definition "A thing."@en .\n'
            'ex:Gadget a owl:Class ; rdfs:label "Gadget"@fr, "Gadget EN"@en ;\n'
            '  rdfs:subClassOf ex:Thing, [ a owl:Restriction ] ; skos:definition "A gadget."@en .\n'
            'ex:partOf a owl:ObjectProperty ; rdfs:label "part of"@en ;\n'
            '  rdfs:domain ex:Gadget ; rdfs:range ex:Thing ; skos:definition "Containment."@en .\n'
        )
        (self.root / 'onto.ttl').write_text(ttl, encoding='utf-8')
        report, html = self._generate(
            {'test.s.m': {'itemClass': 'model', 'ontology': f'{self.BASE}onto.ttl'}},
            {
                'test.s.m/requirements.yaml': (
                    'requirements:\n  - {id: r1, statement: x, applies-to: {bblock: test.s.m, terms: [ex:Gadget, partOf]}}\n'),
            },
        )
        self.assertIn('ontology-table', html)
        self.assertIn('<code>ex:Gadget</code>', html)
        self.assertIn('Gadget EN', html)
        self.assertIn('A gadget.', html)
        self.assertIn('<code>ex:partOf</code>', html)
        self.assertIn('Containment.', html)
        self.assertNotIn('A thing.', html)

    def test_class_id_explicit_and_default(self):
        report, html = self._generate(
            {'test.s.a.b': {'itemClass': 'model'}, 'test.s.c': {'itemClass': 'model'}},
            {
                'test.s.a.b/requirements.yaml': 'requirements:\n  - {id: r1, statement: x}\n',
                'test.s.c/requirements.yaml': (
                    'class-id: custom\nrequirements:\n  - {id: r2, statement: y}\n'),
            },
        )
        self.assertIn('/conf/a/b', html)
        self.assertIn('/conf/custom', html)


class FiguresTest(_TmpDirCase):
    BASE = 'http://example.com/src/'

    def _generate(self, descriptions, clauses=None):
        blocks = []
        for ident, text in descriptions.items():
            d = self.root / ident
            d.mkdir(parents=True, exist_ok=True)
            (d / 'description.md').write_text(text, encoding='utf-8')
            (d / 'assets').mkdir(exist_ok=True)
            (d / 'assets' / 'a.png').write_bytes(b'PNG-' + ident.encode())
            blocks.append({'itemIdentifier': ident, 'name': ident,
                           'sourceFiles': f'{self.BASE}{ident}/'})
        register = {'baseURL': self.BASE, 'bblocks': blocks}
        std = _std('s', prefix='test.s.')
        std['clauses'] = clauses or [{'bblock': b['itemIdentifier']} for b in blocks]
        out = generate(register, [std], source_dir=self.root, build_dir=self.root / 'out')
        return out.parent / 's'

    def test_caption_numbering_assets_and_forward_reference(self):
        out = self._generate({
            'test.s.one': 'See [](#fig-two) and [the first](assets/a.png).\n\n'
                          '![First *diagram*](assets/a.png)\n',
            'test.s.two': '![Second](assets/a.png){#fig-two}\n\n![](assets/a.png)\n',
        })
        html = (out / 'index.html').read_text()
        self.assertIn('<figcaption>Figure 1 — First <em>diagram</em></figcaption>', html)
        self.assertIn('<figcaption>Figure 2 — Second</figcaption>', html)
        self.assertIn('<a href="#fig-two" class="figure-ref">Figure 2</a>', html)
        self.assertIn('<a href="#fig-a" class="figure-ref">the first</a>', html)
        # uncaptioned image: plain img, not a figure
        self.assertEqual(html.count('<figure '), 2)
        self.assertIn('src="assets/test-s-one/a.png"', html)
        self.assertEqual((out / 'assets/test-s-one/a.png').read_bytes(), b'PNG-test.s.one')
        self.assertEqual((out / 'assets/test-s-two/a.png').read_bytes(), b'PNG-test.s.two')

    def test_missing_image_aborts(self):
        with self.assertRaises(FileNotFoundError):
            self._generate({'test.s.one': '![Cap](assets/missing.png)\n'})

    def test_annex_figures_are_lettered(self):
        out = self._generate(
            {'test.s.body': '![Body](assets/a.png)\n', 'test.s.annex': '![Extra](assets/a.png)\n'},
            clauses=[{'bblock': 'test.s.body'}, {'auto': 'annex-a'}, {'bblock': 'test.s.annex'}],
        )
        html = (out / 'index.html').read_text()
        self.assertIn('Figure 1 — Body', html)
        self.assertIn('Figure B.1 — Extra', html)

    def test_repeated_image_is_shown_once_where_first_included(self):
        out = self._generate({
            'test.s.one': '![Shared](assets/a.png)\n\n![Shared again](assets/a.png)\n\n'
                          'See [](assets/a.png).\n',
        })
        html = (out / 'index.html').read_text()
        self.assertEqual(html.count('<figure '), 1)
        self.assertIn('Figure 1 — Shared<', html)
        self.assertNotIn('Shared again', html)
        self.assertIn('<a href="#fig-a" class="figure-ref">Figure 1</a>', html)

    def test_remote_images_are_figures_but_not_copied(self):
        out = self._generate({
            'test.s.one': '![Remote](https://example.org/img/r.png)\n\nSee [](https://example.org/img/r.png).\n',
        })
        html = (out / 'index.html').read_text()
        self.assertIn('<img src="https://example.org/img/r.png"', html)
        self.assertIn('Figure 1 — Remote', html)
        self.assertIn('<a href="#fig-r" class="figure-ref">Figure 1</a>', html)
        self.assertFalse((out / 'assets' / 'test-s-one' / 'r.png').exists())

    def test_absolute_url_under_base_url_maps_to_local_file(self):
        out = self._generate({
            'test.s.one': f'![Abs]({self.BASE}test.s.one/assets/a.png)\n\n[](assets/a.png)\n',
        })
        html = (out / 'index.html').read_text()
        self.assertIn('src="assets/test-s-one/a.png"', html)
        self.assertIn('Figure 1</a>', html)
        self.assertTrue((out / 'assets/test-s-one/a.png').is_file())

    def test_stale_assets_are_removed_on_rerun(self):
        out = self._generate({'test.s.one': '![One](assets/a.png)\n'})
        self.assertTrue((out / 'assets/test-s-one/a.png').is_file())
        (out / 'assets' / 'test-s-one' / 'stale.png').write_bytes(b'x')
        out = self._generate({'test.s.one': 'No images now.\n'})
        self.assertFalse((out / 'assets').exists())

    def test_imported_example_images_resolve_against_source_block(self):
        (self.root / 'test.s.model/assets').mkdir(parents=True)
        (self.root / 'test.s.model/assets/m.png').write_bytes(b'MODEL')
        (self.root / 'test.s.model/json-full.json').write_text(json.dumps({
            'examples': [{'title': 'Ex', 'content': '![From model](assets/m.png)'}],
        }), encoding='utf-8')
        (self.root / 'test.s.cls').mkdir()
        (self.root / 'test.s.cls/requirements.yaml').write_text(
            'requirements:\n  - id: r1\n    statement: x\n'
            '    applies-to: {bblock: test.s.model}\n    import-examples: 1\n',
            encoding='utf-8')
        register = {'baseURL': self.BASE, 'bblocks': [
            {'itemIdentifier': 'test.s.model', 'name': 'Model',
             'sourceFiles': f'{self.BASE}test.s.model/',
             'documentation': {'json-full': {'url': f'{self.BASE}test.s.model/json-full.json'}}},
            {'itemIdentifier': 'test.s.cls', 'name': 'Cls',
             'sourceFiles': f'{self.BASE}test.s.cls/'},
        ]}
        std = _std('s', prefix='test.s.')
        std['req-uri-template'] = 'http://example.com/req/{id}'
        std['conf-uri-template'] = 'http://example.com/conf/{id}'
        std['clauses'] = [{'bblock': 'test.s.cls'}]
        out = generate(register, [std], source_dir=self.root,
                       build_dir=self.root / 'out').parent / 's'
        html = (out / 'index.html').read_text()
        self.assertIn('Figure 1 — From model', html)
        self.assertEqual((out / 'assets/test-s-model/m.png').read_bytes(), b'MODEL')


class ClassIdPrefixTest(_TmpDirCase):
    BASE = 'http://example.com/src/'

    def test_class_id_prefix_is_stripped(self):
        for rel, text in {
            'test.s.requirements.core/requirements.yaml':
                'requirements:\n  - {id: r1, statement: x}\n',
            'test.s.other/requirements.yaml':
                'requirements:\n  - {id: r2, statement: y}\n',
        }.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        register = {'baseURL': self.BASE, 'bblocks': [
            {'itemIdentifier': i, 'name': i, 'sourceFiles': f'{self.BASE}{i}/'}
            for i in ('test.s.requirements.core', 'test.s.other')]}
        std = _std('s', prefix='test.s.')
        std['req-uri-template'] = 'http://example.com/req/{id}'
        std['conf-uri-template'] = 'http://example.com/conf/{id}'
        std['class-id-prefix'] = 'test.s.requirements.'
        std['clauses'] = [{'bblock': 'test.s.requirements.core'}, {'bblock': 'test.s.other'}]
        out = generate(register, [std], source_dir=self.root, build_dir=self.root / 'out')
        html = (out.parent / 's' / 'index.html').read_text()
        self.assertIn('/conf/core"', html)
        self.assertIn('/conf/other"', html)

    def test_class_id_prefix_must_start_with_prefix(self):
        path = self.write_standards("""
            - {id: a, prefix: x.a., title: A, base-uri: u, class-id-prefix: y.b.requirements.}
        """)
        with self.assertRaises(ValueError):
            load_standards(path)


class OgcLayoutTest(_TmpDirCase):
    """Statement kinds, placement markers, explicit abstract tests, preface, 'ogc' layout."""
    BASE = 'http://example.com/src/'

    def _generate(self, files, std_extra, blocks):
        for rel, text in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text), encoding='utf-8')
        register = {'baseURL': self.BASE, 'bblocks': [
            {'itemIdentifier': i, 'name': i.split('.')[-1].title(), 'sourceFiles': f'{self.BASE}{i}/', **extra}
            for i, extra in blocks.items()]}
        std = _std('s', prefix='test.s.')
        std.update({
            'base-uri': 'https://example.com/spec/1.0',
            'req-uri-template': '{base-uri}/req/{id}',
            'rec-uri-template': '{base-uri}/rec/{id}',
            'per-uri-template': '{base-uri}/per/{id}',
            'conf-uri-template': '{base-uri}/conf/{id}',
            'ats-uri-template': '{base-uri}/conf/{id}',
        })
        std.update(std_extra)
        out = generate(register, [std], source_dir=self.root, build_dir=self.root / 'out')
        return (out.parent / 's' / 'index.html').read_text()

    FILES = {
        'test.s.core/requirements.yaml': """
            class-id: core
            title: Requirements Class 'Core'
            conformance-class: {title: Core}
            requirements:
              - {id: core/a, statement: The server SHALL do A.}
              - id: core/b
                parts: [First part SHALL hold., Second part SHALL hold.]
              - {id: core/a, type: permission, statement: The server MAY do A differently.}
              - {id: core/r, type: recommendation, statement: The server SHOULD do R.}
            abstract-tests:
              - id: core/test-a
                requirement: core/a
                purpose: Check A.
                method: "1. Do A."
              - id: core/test-b
                requirement: {ref: core/b, text: /req/core/bee}
                purpose: Check B.
              - id: core/test-x
                requirement: /req/core/missing
                purpose: Dangling.
            """,
        'test.s.core/description.md': """
            # Overview

            <!-- requirements-class -->

            Intro.

            # Doing A

            <!-- requirement: core/a -->

            <!-- permission: core/a -->

            ## Details

            <!-- requirement: core/b -->
            """,
        'test.s.ats/description.md': """
            # Introduction

            <!-- abstract-test: core/general -->

            # Conformance Class Core

            <!-- conformance-class: test.s.core -->

            <!-- abstract-test: core/test-b -->
            """,
        'test.s.ats/abstract-tests.yaml': """
            abstract-tests:
              - {id: core/general, uri: "https://example.com/spec/1.0/req/core/general", purpose: General.}
            """,
        'test.s.sec/description.md': 'Be careful.\n',
    }
    BLOCKS = {'test.s.core': {'itemClass': 'requirements-class'}, 'test.s.ats': {'itemClass': 'clause'},
              'test.s.sec': {'itemClass': 'clause'}}

    def _std(self, layout):
        return {
            'layout': layout,
            'abstract': 'The *abstract*.',
            'keywords': ['k1', 'k2'],
            'preface': [{'auto': 'abstract'}, {'auto': 'keywords'}, {'bblock': 'test.s.sec'}],
            'clauses': [{'bblock': 'test.s.core', 'title': 'Requirements Class “Core”'},
                        {'auto': 'annex-a', 'bblock': 'test.s.ats'}],
        }

    def test_ogc_layout(self):
        html = self._generate(self.FILES, self._std('ogc'), self.BLOCKS)
        # preface with roman numerals, abstract rendered as Markdown and not on the cover
        self.assertIn('<h2>I. Abstract</h2>', html)
        self.assertIn('The <em>abstract</em>.', html)
        self.assertIn('<h2>II. Keywords</h2>', html)
        self.assertIn('<h2>III. Sec</h2>', html)
        self.assertNotIn('doc-abstract', html)
        # clause title override and numbered subclauses
        self.assertIn('<h2>1. Requirements Class “Core”</h2>', html)
        self.assertIn('<h3 id="sec-1.1">1.1. Overview</h3>', html)
        self.assertIn('<h4 id="sec-1.2.1">1.2.1. Details</h4>', html)
        # statements: document-wide labels per kind, kind-specific URIs and anchors
        self.assertIn('Requirements class 1: Requirements Class &#39;Core&#39;', html.replace("'", '&#39;'))
        self.assertIn('<th colspan="2">Requirement 1</th>', html)
        self.assertIn('<th colspan="2">Permission 1</th>', html)
        self.assertIn('<code>/per/core/a</code>', html)
        self.assertIn('id="per-core-a"', html)
        self.assertIn('<th>B</th><td><p>Second part SHALL hold.</p>', html)
        # the class table is placed by its marker, after the Overview heading
        self.assertLess(html.index('sec-1.1'), html.index('Requirements class 1:'))
        # the unplaced recommendation goes under an auto "Requirements" subclause
        self.assertIn('<h3 id="sec-1.3">1.3. Requirements</h3>', html)
        self.assertIn('<th colspan="2">Recommendation 1</th>', html)
        # explicit Annex A: annex test first, then the class table, then placed and unplaced tests
        self.assertIn('<h2>Annex A (normative) Abstract Test Suite</h2>', html)
        self.assertIn('<code>/req/core/general</code>', html)
        self.assertIn('Conformance class A.1: Core', html)
        self.assertIn('<th colspan="2">Abstract test A.1</th></tr></thead>\n      <tbody>\n        '
                      '<tr><th>Identifier</th><td><code>/req/core/general</code>', html)
        self.assertLess(html.index('Abstract test A.1</th>'), html.index('Conformance class A.1'))
        self.assertIn('Requirement 1: <code>/req/core/a</code>', html.replace('<a href="#req-core-a">', '').replace('</a>', ''))
        self.assertIn('<a href="#req-core-b">/req/core/bee</a>', html)
        self.assertIn('/req/core/missing', html)
        self.assertNotIn('<!--', html.split('<main>')[1])
        # the layout's styles are a file of their own
        self.assertIn('<link rel="stylesheet" href="ogc-layout.css">', html)
        self.assertTrue((self.root / 'out' / 's' / 'ogc-layout.css').is_file())

    def test_toc_depth_and_reference_anchors(self):
        files = {
            'test.s.terms/terms.yaml': """
                terms:
                  - {term: Alpha, definition: The first.}
                """,
            'test.s.refs/references.yaml': """
                normative:
                  - {doc-id: OGC 1, anchor: ogc-one, citation: "Some *citation*."}
                  - {doc-id: OGC 2, anchor: ogc-two, title: Plain entry}
                  - {doc-id: OGC 3, title: No anchor}
                """,
            'test.s.text/description.md': """
                # One

                ## Two

                ### Three

                #### Four

                See [OGC 1](#ogc-one).
                """,
        }
        blocks = {'test.s.terms': {'itemClass': 'terms'}, 'test.s.refs': {'itemClass': 'references'},
                  'test.s.text': {'itemClass': 'clause'}}
        clauses = {'clauses': ['test.s.terms', 'test.s.refs', 'test.s.text']}
        html = self._generate(files, {'layout': 'ogc', **clauses}, blocks)
        toc = html.split('<nav id="toc">')[1].split('</nav>')[0]
        # the table of contents stops at four numbers, and skips the terms
        self.assertIn('<li class="toc-l5"><a href="#sec-3.1.1.1">3.1.1.1. Three</a></li>', toc)
        self.assertNotIn('Four', toc)
        self.assertIn('<h6 id="sec-3.1.1.1.1">3.1.1.1.1. Four</h6>', html)
        # a clause of terms only: the terms are its subclauses, not listed in the contents
        self.assertNotIn('Terms and definitions', toc)
        self.assertNotIn('Alpha', toc)
        self.assertIn('<h3 id="sec-1.1">1.1. Alpha</h3>', html)
        # with abbreviated terms too: "Terms and definitions" and "Abbreviated terms" group them
        grouped = dict(files)
        grouped['test.s.terms/terms.yaml'] = files['test.s.terms/terms.yaml'] + "abbreviated-terms: {ABC: A B C}\n"
        html = self._generate(grouped, {'layout': 'ogc', **clauses}, blocks)
        toc = html.split('<nav id="toc">')[1].split('</nav>')[0]
        self.assertIn('1.1. Terms and definitions', toc)
        self.assertIn('1.2. Abbreviated terms', toc)
        self.assertNotIn('Alpha', toc)
        self.assertIn('<h4 id="sec-1.1.1">1.1.1. Alpha</h4>', html)
        # references get their anchor in both layouts, and only if they have one
        for layout in ('ogc', 'classic'):
            html = self._generate(files, {'layout': layout, **clauses}, blocks)
            self.assertIn('<div class="reference-entry citation" id="ogc-one">', html)
            self.assertIn('<div class="reference-entry" id="ogc-two">', html)
            self.assertIn('<a href="#ogc-one">OGC 1</a>', html)
            self.assertEqual(html.count('<div class="reference-entry">'), 1)

    def test_classic_output_unaffected_by_ogc_layout(self):
        """Documents in the classic layout must keep their exact output."""
        html = self._generate(self.FILES, self._std('classic'), self.BLOCKS)
        out = self.root / 'out' / 's'
        self.assertTrue(html.startswith('<!DOCTYPE html>\n<html lang="en">\n'))
        self.assertNotIn('ogc-layout.css', html)
        self.assertFalse((out / 'ogc-layout.css').exists())
        self.assertNotIn('layout-ogc', (out / 'ogc-standard.css').read_text())

    def test_new_sources_in_classic_layout(self):
        html = self._generate(self.FILES, self._std('classic'), self.BLOCKS)
        self.assertIn('Requirement 1/1', html)
        self.assertIn('Permission 1/1', html)
        self.assertIn('https://example.com/spec/1.0/per/core/a', html)
        self.assertIn('<ol class="req-parts" type="A">', html)
        self.assertIn('class="req-class-box"', html)
        self.assertIn('Abstract test A.1', html)

    def test_subclauses_statements_elsewhere_and_modspec_options(self):
        files = {
            'test.s.group/description.md': """
                # Overview

                Group text.

                ```yaml
                # not a heading
                a: 1
                ```
                """,
            'test.s.core/requirements.yaml': """
                class-id: core
                conformance-class:
                  title: Core
                  prerequisites: ["[Other class](#conf-other)"]
                requirements:
                  - {id: core/a, statement: The server SHALL do A.}
                  - id: core/c
                    label: /label/c
                    conditions: The server is busy.
                    description: Some description.
                    parts: [It SHALL wait.]
                  - {id: core/annexed, statement: The server SHALL be testable.}
                  - {id: core/pre, type: recommendation, statement: The server SHOULD be safe.}
                abstract-tests: []
                """,
            'test.s.core/description.md': """
                # Doing

                <!-- requirements-class -->

                <!-- requirement: core/a -->

                <!-- requirement: core/c -->
                """,
            'test.s.other/requirements.yaml': """
                class-id: other
                depends-on:
                  - {uri: "#bb-test-s-core", title: /req/core}
                requirements:
                  - {id: other/x, statement: The server SHALL do X.}
                abstract-tests: []
                """,
            'test.s.sec/description.md': """
                # Part one

                <!-- recommendation: core/pre -->
                """,
            'test.s.ats/description.md': """
                # Introduction

                <!-- requirement: core/annexed -->

                # Conformance Class Core

                <!-- conformance-class: test.s.core -->
                """,
        }
        blocks = {'test.s.group': {'itemClass': 'clause'}, 'test.s.core': {'itemClass': 'requirements-class'},
                  'test.s.other': {'itemClass': 'requirements-class'}, 'test.s.sec': {'itemClass': 'clause'},
                  'test.s.ats': {'itemClass': 'clause'}}
        std = {
            'layout': 'ogc',
            'modspec': {'statement-label': 'Statement', 'singular-labels': True, 'obligation-row': False},
            'preface': [{'bblock': 'test.s.sec'}],
            'clauses': [
                {'bblock': 'test.s.group', 'title': 'Group', 'subclauses': [
                    {'bblock': 'test.s.core', 'title': 'Class Core'},
                    {'bblock': 'test.s.other', 'title': 'Class Other'}]},
                {'auto': 'annex-a', 'bblock': 'test.s.ats'},
            ],
        }
        html = self._generate(files, std, blocks)
        toc = html.split('<nav id="toc">')[1].split('</nav>')[0]
        # subclauses are numbered after the parent's own subclauses, one level deeper
        self.assertIn('<h2>1. Group</h2>', html)
        self.assertIn('<h3 id="sec-1.1">1.1. Overview</h3>', html)
        self.assertIn('<h3>1.2. Class Core</h3>', html)
        self.assertIn('<h4 id="sec-1.2.1">1.2.1. Doing</h4>', html)
        self.assertIn('<h3>1.3. Class Other</h3>', html)
        self.assertIn('<h4 id="sec-1.3.1">1.3.1. Requirements</h4>', html)
        self.assertIn('<li class="toc-l3"><a href="#sec-1.2">1.2. Class Core</a></li>', toc)
        self.assertEqual(toc.count('href="#sec-1.2"'), 1)
        # a '#' line in a code block is not a heading
        self.assertNotIn('not a heading</h', html)
        self.assertNotIn('not a heading', toc)
        # preface subclauses are lettered
        self.assertIn('<h3 id="sec-I.A">I.A. Part one</h3>', html)
        # statements placed in the preface and in Annex A, numbered where shown
        self.assertLess(html.index('<th colspan="2">Recommendation 1</th>'), html.index('<h3>1.2. Class Core'))
        self.assertIn('<th colspan="2">Requirement 1</th></tr></thead>\n      <tbody>\n        '
                      '<tr><th>Identifier</th><td><code>/req/core/a</code>', html)
        self.assertIn('<code>/req/other/x</code>', html.split('Requirement 3</th>')[1][:200])
        self.assertIn('<code>/req/core/annexed</code>', html.split('Requirement A.1</th>')[1][:200])
        self.assertLess(html.index('Annex A (normative)'), html.index('Requirement A.1</th>'))
        # ModSpec options and rows
        self.assertIn('<tr><th>Statement</th><td><p>The server SHALL do A.</p>', html)
        self.assertIn('<tr><th>Label</th><td><p>/label/c</p>\n</td></tr>', html)
        self.assertIn('<tr><th>Conditions</th><td><p>The server is busy.</p>\n</td></tr>', html)
        self.assertIn('<tr><th>Description</th><td><p>Some description.</p>\n</td></tr>', html)
        self.assertNotIn('Obligation', html)
        self.assertIn('<th>Normative statements</th>', html)
        self.assertIn('<th>Normative statement</th>', html)
        self.assertIn('<tr><th>Prerequisite</th><td>\n          <a href="#bb-test-s-core">/req/core</a>', html)
        self.assertIn('<tr><th>Prerequisite</th><td>\n          <p><a href="#conf-other">Other class</a></p>', html)
        # 'abstract-tests: []': no tests generated for the class
        self.assertNotIn('Abstract test', html)
        self.assertIn('Conformance class A.1: Core', html)

    def test_unknown_layout_and_statement_type(self):
        with self.assertRaises(ValueError):
            self._generate(self.FILES, {**self._std('ogc'), 'layout': 'fancy'}, self.BLOCKS)
        with self.assertRaises(ValueError):
            self._generate(self.FILES, {**self._std('ogc'), 'modspec': {'colour': 'red'}}, self.BLOCKS)
        files = dict(self.FILES)
        files['test.s.core/requirements.yaml'] = 'requirements:\n  - {id: x, type: wish, statement: y}\n'
        with self.assertRaises(ValueError):
            self._generate(files, self._std('ogc'), self.BLOCKS)


class _FakeResponse:
    def __init__(self, body: bytes, content_type='application/x-yaml'):
        self._body = body
        self.headers = {'content-type': content_type}
        self.encoding = None

    def raise_for_status(self):
        pass

    @property
    def text(self):
        return self._body.decode(self.encoding or 'latin-1')


class IncludesTest(_TmpDirCase):
    BASE = 'http://example.com/src/'

    def _generate(self, description, schema_files=None, extra_registers=()):
        for rel, text in {
            'test.s.one/description.md': description,
            'test.s.one/examples/x.json': '{"a": 1}\n',
            'test.s.model/schema.yaml': '# Link schema\ntype: object\ndescription: a <b>link</b>\n',
            'annotated/model.yaml': 'x-jsonld-id: annotated-yaml\n',
            'annotated/model.json': 'annotated-json\n',
            **(schema_files or {}),
        }.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        register = {'baseURL': self.BASE, 'bblocks': [
            {'itemIdentifier': 'test.s.one', 'name': 'One', 'sourceFiles': f'{self.BASE}test.s.one/'},
            {'itemIdentifier': 'test.s.model', 'name': 'Model',
             'sourceFiles': f'{self.BASE}test.s.model/',
             'sourceSchema': f'{self.BASE}test.s.model/schema.yaml',
             'schema': {'application/yaml': f'{self.BASE}annotated/model.yaml',
                        'application/json': f'{self.BASE}annotated/model.json'}},
            {'itemIdentifier': 'test.s.noschema', 'name': 'No schema',
             'sourceFiles': f'{self.BASE}test.s.noschema/'},
        ]}
        std = _std('s', prefix='test.s.')
        std['clauses'] = [{'bblock': 'test.s.one'}]
        out = generate(register, [std], source_dir=self.root, build_dir=self.root / 'out',
                       extra_registers=list(extra_registers))
        return (out.parent / 's' / 'index.html').read_text()

    def test_source_schema_is_included_verbatim(self):
        html = self._generate('## Links\n\n```yaml include=bblocks://test.s.model\n```\n')
        self.assertIn('<pre><code class="language-yaml"># Link schema\ntype: object\n'
                      'description: a &lt;b&gt;link&lt;/b&gt;\n</code></pre>', html)
        # included text is not Markdown: its '#' comment is not a heading
        self.assertNotIn('id="link-schema"', html)
        self.assertNotIn('include=', html)
        # followed by a link to the block that defines it (local: in-document anchor)
        self.assertIn('</code></pre>\n<p class="include-source">Building block: '
                      '<a href="#bb-test-s-model">test.s.model</a></p>', html)

    def test_link_to_the_defining_block(self):
        html = self._generate(
            'A\n\n```yaml include=http://example.com/src/annotated/model.yaml\n```\n\n'
            'B\n\n```json include=examples/x.json\n```\n\n'
            'C\n\n```json include=examples/x.json bblock=test.s.model\n```\n\n'
            'D\n\n```yaml include=bblocks://test.s.model bblock=none\n```\n')
        link = '<p class="include-source">Building block: <a href="#bb-test-s-model">test.s.model</a></p>'
        # A: a URL is matched to the block whose (annotated or source) schema it is
        self.assertIn('annotated-yaml\n</code></pre>\n' + link, html)
        # B: no block known for a relative file; C: given with bblock=
        self.assertEqual(html.count('{&quot;a&quot;: 1}\n</code></pre>\n' + link), 1)
        # D: bblock=none drops the link
        self.assertEqual(html.count('class="include-source"'), 2)

    def test_annotated_schema_follows_the_fence_language(self):
        html = self._generate('```yaml include=bblocks://test.s.model schema=annotated\n```\n\n'
                              '```json include=bblocks://test.s.model schema=annotated\n```\n')
        self.assertIn('<code class="language-yaml">x-jsonld-id: annotated-yaml\n</code>', html)
        self.assertIn('<code class="language-json">annotated-json\n</code>', html)

    def test_relative_path_resolves_against_the_block(self):
        html = self._generate('```json include=examples/x.json\n```\n')
        self.assertIn('<code class="language-json">{&quot;a&quot;: 1}\n</code>', html)

    def test_fence_without_language(self):
        html = self._generate('```include=examples/x.json\n```\n')
        self.assertIn('<pre><code>{&quot;a&quot;: 1}\n</code></pre>', html)

    def test_existing_content_is_replaced_with_a_warning(self):
        with self.assertLogs('standard_gen.includes', level='WARNING'):
            html = self._generate('```yaml include=bblocks://test.s.model\nstale copy\n```\n')
        self.assertNotIn('stale copy', html)
        self.assertIn('# Link schema', html)

    def test_unresolvable_includes_abort(self):
        for text, message in (
            ('```yaml include=bblocks://test.s.nope\n```\n', 'unknown building block'),
            ('```yaml include=bblocks://test.s.noschema\n```\n', 'no source schema'),
            ('```yaml include=bblocks://test.s.model schema=resolved\n```\n', 'expected one of'),
            ('```yaml include=examples/missing.yaml\n```\n', 'could not read it'),
            ('```json include=examples/x.json bblock=test.s.nope\n```\n', 'unknown building block'),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                self._generate(text)

    def test_remote_schema_of_an_imported_block_is_fetched_once_as_utf8(self):
        imported = {'bblocks': [{
            'itemIdentifier': 'ext.link', 'name': 'Link',
            'sourceSchema': 'https://schemas.example.org/link.yaml',
            'documentation': {'bblocks-viewer': {'url': 'https://viewer.example.org/ext.link'}}}]}
        with unittest.mock.patch('standard_gen.resolver.requests.get',
                                 return_value=_FakeResponse('title: Autònoma\n'.encode())) as get:
            html = self._generate('```yaml include=bblocks://ext.link\n```\n',
                                  extra_registers=[imported])
        self.assertIn('title: Autònoma\n', html)
        # imported block: linked to its page in the viewer
        self.assertIn('<a href="https://viewer.example.org/ext.link">ext.link</a>', html)
        get.assert_called_once_with('https://schemas.example.org/link.yaml', timeout=30)
