"""Synthetic regression tests for template fidelity and field coverage."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree as ET
import fill_ehf as ehf


def applicant(count=1):
    jobs = []
    for i in range(count):
        year = 2025-i*2
        jobs.append({"from_date": f"01/01/{year}",
                     "to_date": "Present" if i == 0 else f"31/12/{year+1}",
                     "job_title": f"Synthetic Officer {i+1}", "employer": f"Example Employer {i+1}",
                     "location": "Nairobi, Kenya", "annual_salary": "USD 60,000 gross per year",
                     "direct_reports": "2", "un_grade": "Not applicable",
                     "responsibilities": [f"Responsibility {i+1}: Supported the synthetic programme."],
                     "achievements": [f"Achievement {i+1}: Produced a synthetic review report."]})
    return {"first_name": "Élodie", "last_name": "Example", "jobs": jobs}


class FillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="unesco-ehf-test-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self, data, **kwargs):
        out = self.root / "filled.docx"
        report = ehf.fill(ehf.TEMPLATE, data, out, **kwargs)
        with ZipFile(out) as archive:
            xml = ET.fromstring(archive.read("word/document.xml"))
        return out, report, xml

    def test_concurrent_output_is_not_overwritten(self):
        output = self.root / "filled.docx"
        original_zip = ehf.ZipFile
        def racing_zip(path, mode="r", *args, **kwargs):
            if Path(path) == output and mode in {"w", "x"}:
                output.write_bytes(b"unrelated concurrent output")
            return original_zip(path, mode, *args, **kwargs)
        with patch.object(ehf, "ZipFile", side_effect=racing_zip):
            with self.assertRaises(FileExistsError):
                ehf.fill(ehf.TEMPLATE, applicant(), output)
        self.assertEqual(output.read_bytes(), b"unrelated concurrent output")

    def test_one_role_preserves_package_and_fields(self):
        before = hashlib.sha256(ehf.TEMPLATE.read_bytes()).hexdigest()
        out, report, xml = self.fill(applicant())
        self.assertEqual(report["jobCount"], 1)
        self.assertEqual(len(xml.findall(".//w:body/w:tbl", ehf.NS)), 2)
        self.assertEqual(len(xml.findall(".//w:ffData", ehf.NS)), 10)
        self.assertEqual(len(xml.findall(".//w:sectPr", ehf.NS)), 2)
        with ZipFile(ehf.TEMPLATE) as a, ZipFile(out) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for name in a.namelist():
                if name not in {"word/document.xml", "word/settings.xml"}:
                    self.assertEqual(a.read(name), b.read(name), name)
        self.assertIn("Élodie", "".join(xml.xpath("//w:t/text()", namespaces=ehf.NS)))
        self.assertEqual(hashlib.sha256(ehf.TEMPLATE.read_bytes()).hexdigest(), before)

    def test_six_roles_clone_pristine_block_and_retain_all_values(self):
        data = applicant(6)
        _, report, xml = self.fill(data)
        tables = xml.findall(".//w:body/w:tbl", ehf.NS)[1:]
        self.assertEqual(len(tables), 6)
        self.assertEqual(len(xml.findall(".//w:ffData", ehf.NS)), 50)
        for index, table in enumerate(tables):
            content = "".join(table.xpath(".//w:t/text()", namespaces=ehf.NS))
            self.assertIn(f"WORK EXPERIENCE {index+1}:", content)
            for value in data["jobs"][index].values():
                for text in value if isinstance(value, list) else [value]:
                    self.assertIn(text, content)
            self.assertEqual([len(row.findall("w:tc", ehf.NS)) for row in table.findall("w:tr", ehf.NS)], [1,3,3,2,2,2,1,1,1])
        cloned_ids = {n.get(ehf.q("id")) for n in tables[-1].findall(".//w:bookmarkStart", ehf.NS)}
        existing_ids = {n.get(ehf.q("id")) for t in tables[:-1] for n in t.findall(".//w:bookmarkStart", ehf.NS)}
        self.assertTrue(cloned_ids.isdisjoint(existing_ids))
        self.assertEqual(report["unresolvedFields"], [])

    def test_job_separator_sequences_preserve_native_paragraph_properties(self):
        def separators(document):
            body = document.find('w:body', ehf.NS)
            result = []
            for table in body.findall('w:tbl', ehf.NS)[1:]:
                sequence, node = [], table.getnext()
                while node is not None and node.tag not in (ehf.q('tbl'), ehf.q('sectPr')):
                    clean = deepcopy(node)
                    for item in clean.iter():
                        for attr in list(item.attrib):
                            if ET.QName(attr).localname in {'paraId', 'textId'}:
                                del item.attrib[attr]
                    sequence.append(ET.tostring(clean, method='c14n', exclusive=True))
                    node = node.getnext()
                result.append(sequence)
            return result
        with ZipFile(ehf.TEMPLATE) as archive:
            original = ET.fromstring(archive.read('word/document.xml'))
        expected = separators(original)
        self.assertEqual([len(s) for s in expected], [1, 2, 2, 2, 1])
        for count in (1, 5, 7):
            with self.subTest(jobs=count):
                out = self.root / f'separators-{count}.docx'
                ehf.fill(ehf.TEMPLATE, applicant(count), out)
                with ZipFile(out) as archive:
                    actual = ET.fromstring(archive.read('word/document.xml'))
                self.assertEqual(separators(actual),
                                 [expected[min(i, 4)] for i in range(count)])
                para_ids = actual.xpath('//@w14:paraId', namespaces={
                    'w14': 'http://schemas.microsoft.com/office/word/2010/wordml'})
                self.assertEqual(len(para_ids), len(set(para_ids)))

    def test_repository_placeholders_never_reach_a_clean_or_review_form(self):
        for placeholder in ('[Confirm title]', '[Placeholder title]',
                            '[User to Insert Specific Metric]', '[User to Insert Metric]',
                            '[Select one]'):
            for review_mode in (False, True):
                with self.subTest(placeholder=placeholder, review_mode=review_mode):
                    data = applicant()
                    data['jobs'][0]['achievements'] = ['Produced ' + placeholder + ' reports.']
                    with self.assertRaisesRegex(ValueError, 'unresolved placeholders'):
                        out = self.root / f'placeholder-{placeholder}-{review_mode}.docx'
                        ehf.fill(ehf.TEMPLATE, data, out, allow_incomplete=review_mode)
                    self.assertFalse(out.exists())

    def test_narratives_can_exceed_blank_paragraph_count(self):
        data = applicant()
        data["jobs"][0]["responsibilities"] = [f"Synthetic responsibility paragraph {i}." for i in range(20)]
        data["jobs"][0]["achievements"] = [f"Synthetic achievement paragraph {i}." for i in range(20)]
        _, _, xml = self.fill(data)
        content = "".join(xml.xpath("//w:t/text()", namespaces=ehf.NS))
        for key in ("responsibilities", "achievements"):
            for value in data["jobs"][0][key]:
                self.assertEqual(content.count(value), 1)

    def test_missing_fact_requires_review_mode(self):
        data = applicant()
        data["jobs"][0]["annual_salary"] = None
        with self.assertRaisesRegex(ValueError, "Unresolved fields"):
            self.fill(data)
        _, report, _ = self.fill(data, allow_incomplete=True)
        self.assertEqual(report["status"], "incomplete_review_draft")
        self.assertEqual(report["unresolvedFields"], ["jobs[1].annual_salary"])

    def test_invalid_dates_names_keys_and_placeholders_rejected(self):
        cases = []
        for key, value in [("from_date", "02/2025"), ("from_date", "31/02/2025"),
                           ("job_title", "[Confirm title]"), ("job_title", "Line1\nLine2")]:
            data = applicant()
            data["jobs"][0][key] = value
            cases.append(data)
        data = applicant(); data["first_name"] = "A"*21; cases.append(data)
        data = applicant(); data["first_name"] = "😀"*11; cases.append(data)
        data = applicant(); data["jobs"][0]["unrecognized"] = "value"; cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.fill(data, allow_incomplete=True)

    def test_chronology_warnings_not_silently_accepted(self):
        data = applicant(2)
        data["jobs"].reverse()
        with self.assertRaisesRegex(ValueError, "Chronology needs review"):
            self.fill(data)
        _, report, _ = self.fill(data, allow_incomplete=True)
        self.assertTrue(report["chronologyWarnings"])

    def test_no_template_or_output_overwrite(self):
        out, _, _ = self.fill(applicant())
        before = out.read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            ehf.fill(ehf.TEMPLATE, applicant(), out)
        self.assertEqual(out.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "differ from template"):
            ehf.fill(ehf.TEMPLATE, applicant(), ehf.TEMPLATE)
        different = self.root / "changed.docx"
        different.write_bytes(ehf.TEMPLATE.read_bytes()+b"changed")
        with self.assertRaisesRegex(ValueError, "hash differs"):
            ehf.fill(different, applicant(), self.root / "other.docx")


if __name__ == "__main__":
    unittest.main()
