"""Behavioral tests on the retained blank template, using fictional text only."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from lxml import etree as E

SPEC = importlib.util.spec_from_file_location('p11', Path(__file__).with_name('p11_fields.py'))
p11 = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(p11)
SOURCE = Path(__file__).resolve().parents[1] / 'assets/UNU-P11_Personal-History-Form.docx'


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'filled.docx'
        self.inventory = p11.inspect(SOURCE)

    def op(self, row, kind, value, **kw):
        f = next(f for f in self.inventory['fields'] if f['row'] == row and f['kind'] == kind)
        return dict(xpath=f['xpath'], kind=kind, expected=f['expected'], value=value, source_ref='Fictional test fixture', **kw)

    def manifest(self, *ops):
        return dict(source_sha256=self.inventory['source_sha256'], operations=list(ops))

    def test_transfer_native_fields_and_protected_xml(self):
        ops = [self.op(44, 'text', 'Coordinated office activities.\nMaintained records.', word_limit=300),
               self.op(124, 'text', 'This is a fictional motivation test.', word_limit=600),
               self.op(15, 'dropdown', 'Confident'), self.op(6, 'checkbox', True)]
        report = p11.apply(SOURCE, self.manifest(*ops), self.out)
        self.assertTrue(report['unrelated_parts_byte_identical'])
        with ZipFile(SOURCE) as a, ZipFile(self.out) as b:
            before = E.fromstring(a.read(p11.PART)); after = E.fromstring(b.read(p11.PART))
            # Mask only intended field-result state, then compare the entire XML tree.
            for op in ops:
                old = before.xpath(op['xpath'], namespaces=p11.NS)[0]
                new = after.xpath(op['xpath'], namespaces=p11.NS)[0]
                if op['kind'] == 'text':
                    old_runs, new_runs = p11.legacy_result(old), p11.legacy_result(new)
                    self.assertEqual(len(old_runs), len(new_runs))
                    for x, y in zip(old_runs, new_runs):
                        self.assertEqual(E.tostring(x.find('w:rPr', p11.NS)), E.tostring(y.find('w:rPr', p11.NS)))
                        y.getparent().replace(y, copy.deepcopy(x))
                new.getparent().replace(new, copy.deepcopy(old))
            self.assertEqual(E.tostring(before, method='c14n'), E.tostring(after, method='c14n'))

    def test_failures_leave_no_output(self):
        cases = [self.manifest(self.op(44, 'text', 'word '*301)),
                 self.manifest(self.op(124, 'text', 'word '*601)),
                 self.manifest(self.op(44, 'text', '123456', char_limit=5)),
                 self.manifest(self.op(15, 'dropdown', 'High')),
                 self.manifest(self.op(127, 'text', '2026-09-19'))]
        bad = self.manifest(self.op(44, 'text', 'Example')); bad['source_sha256'] = 'stale'; cases.append(bad)
        bad = self.manifest(self.op(44, 'text', 'Example')); bad['operations'][0]['expected'] = 'wrong'; cases.append(bad)
        bad = self.manifest(self.op(44, 'text', 'Example')); bad['operations'] *= 2; cases.append(bad)
        for manifest in cases:
            with self.subTest(manifest=manifest):
                with self.assertRaises(ValueError):
                    p11.apply(SOURCE, manifest, self.out)
                self.assertFalse(self.out.exists())

    def test_certification_remains_protected_after_label_case_changes(self):
        for label in ('I Certify', 'i certify', 'Signature', 'signature'):
            with self.subTest(label=label):
                source = Path(self.tmp.name) / 'revised.docx'
                with ZipFile(SOURCE) as original, ZipFile(source, 'w') as revised:
                    root = E.fromstring(original.read(p11.PART))
                    row = root.findall('.//w:tr', p11.NS)[127]
                    for text in row.findall('.//w:t', p11.NS):
                        text.text = (text.text or '').replace('I certify', 'Declaration').replace('SIGNATURE', 'Applicant')
                    row.find('.//w:t', p11.NS).text = label
                    for entry in original.infolist():
                        revised.writestr(entry, E.tostring(root) if entry.filename == p11.PART else original.read(entry.filename))
                self.inventory = p11.inspect(source)
                self.out = Path(self.tmp.name) / f'filled-{label}.docx'
                with self.assertRaisesRegex(ValueError, 'certification is protected'):
                    p11.apply(source, self.manifest(self.op(127, 'text', '2026-09-19')), self.out)
                self.assertFalse(self.out.exists())

    def test_refuses_overwrite(self):
        self.out.write_bytes(b'keep this')
        with self.assertRaises(ValueError):
            p11.apply(SOURCE, self.manifest(self.op(44, 'text', 'Example')), self.out)
        self.assertEqual(self.out.read_bytes(), b'keep this')


if __name__ == '__main__':
    unittest.main()
