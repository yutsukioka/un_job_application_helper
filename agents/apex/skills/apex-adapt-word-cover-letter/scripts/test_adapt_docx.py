"""Synthetic DOCX checks: preservation, guarded edits and concurrent outputs."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import adapt_docx as adapter


class AdaptDocxTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source, self.output = self.root / 'source.docx', self.root / 'output.docx'
        self.document = (f'<w:document xmlns:w="{adapter.NS["w"]}"><w:body>'
                         '<w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
                         '<w:r><w:rPr><w:b/></w:rPr><w:t>Original body</w:t></w:r></w:p>'
                         '<w:p><w:r><w:t>Protected contact</w:t></w:r></w:p>'
                         '</w:body></w:document>').encode()
        self.write_source()

    def write_source(self):
        with ZipFile(self.source, 'w') as z:
            z.writestr('word/document.xml', self.document)
            z.writestr('word/styles.xml', b'protected styles')
            z.writestr('word/header1.xml', b'protected header')

    def manifest(self):
        return {'source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                'operations': [{'type': 'replace_text', 'part': 'word/document.xml',
                    'xpath': '/w:document/w:body/w:p[1]', 'expected': 'Original body',
                    'replacement': 'Approved replacement'}]}

    def test_exact_replacement_preserves_protected_parts_and_format(self):
        before = self.source.read_bytes()
        result = adapter.adapt(self.source, self.manifest(), self.output)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(self.source.read_bytes(), before)
        with ZipFile(self.source) as source, ZipFile(self.output) as output:
            for name in ['word/styles.xml', 'word/header1.xml']:
                self.assertEqual(source.read(name), output.read(name))
            old = adapter.ET.fromstring(self.document)
            new = adapter.ET.fromstring(output.read('word/document.xml'))
            self.assertEqual(adapter.formatting(old.find('.//w:p', adapter.NS)),
                             adapter.formatting(new.find('.//w:p', adapter.NS)))
            self.assertEqual(new.xpath('//w:t/text()', namespaces=adapter.NS),
                             ['Approved replacement', 'Protected contact'])

    def test_cached_fields_and_stale_hashes_leave_no_output(self):
        manifest = self.manifest(); manifest['source_sha256'] = 'stale'
        with self.assertRaises(ValueError):
            adapter.adapt(self.source, manifest, self.output)
        self.document = self.document.replace(b'<w:t>Original body</w:t>',
            b'<w:fldChar w:fldCharType="begin"/><w:t>Original body</w:t>')
        self.write_source()
        with self.assertRaisesRegex(ValueError, 'fields'):
            adapter.adapt(self.source, self.manifest(), self.output)
        self.assertFalse(self.output.exists())

    def test_report_cannot_overwrite_inputs_or_existing_file(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(self.manifest()))
        existing = self.root / "existing.json"
        existing.write_bytes(b"keep this report")
        original = self.source.read_bytes()
        for report in (self.source, manifest, self.output, existing):
            with self.subTest(report=report.name):
                result = subprocess.run([sys.executable, adapter.__file__, str(self.source),
                    str(manifest), str(self.output), "--report", str(report)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual(existing.read_bytes(), b"keep this report")

    def test_output_created_during_edit_is_never_removed(self):
        original_zip = adapter.ZipFile
        def racing_zip(path, mode='r', *args, **kwargs):
            if Path(path) == self.output and mode == 'x':
                self.output.write_bytes(b'unrelated concurrent output')
            return original_zip(path, mode, *args, **kwargs)
        with patch.object(adapter, 'ZipFile', side_effect=racing_zip):
            with self.assertRaises(FileExistsError):
                adapter.adapt(self.source, self.manifest(), self.output)
        self.assertEqual(self.output.read_bytes(), b'unrelated concurrent output')


if __name__ == '__main__':
    unittest.main()
