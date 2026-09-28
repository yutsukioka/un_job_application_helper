"""Synthetic shortlist fixtures contain no candidate or real vacancy data."""
from argparse import Namespace
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from lxml import etree as E
import preserve_workbook as helper


def workbook_bytes():
    wb = E.Element(helper.T('workbook'), nsmap={None: helper.S, 'r': helper.R})
    sheets = E.SubElement(wb, helper.T('sheets'))
    rels = E.Element('{' + helper.P + '}Relationships')
    parts = {}
    examples = {
        'Plan': {'B9': 'Organization', 'E9': 'Status', 'B10': 'Example Org',
                 'C10': 'Example role', 'E10': 'In Progress', 'C11': 'Example roster', 'B11': 'Example Org'},
        'Vacancies': {'A2': 'Vacancies', 'O6': 'Vacancy ID', 'Q6': 'Time until close',
                      'B7': 'Example Org', 'C7': 'Example role', 'O7': '111'},
        'Rosters': {'A2': 'Roster calls', 'O6': 'Vacancy ID', 'Q6': 'Time until close',
                    'B7': 'Example Org', 'C7': 'Example roster', 'O7': '222'},
    }
    for index, (name, cells) in enumerate(examples.items(), 1):
        rid = f'rId{index}'
        E.SubElement(sheets, helper.T('sheet'), name=name, sheetId=str(index), **{'{'+helper.R+'}id': rid})
        E.SubElement(rels, '{'+helper.P+'}Relationship', Id=rid, Target=f'worksheets/sheet{index}.xml')
        root = E.Element(helper.T('worksheet'), nsmap={None: helper.S})
        data = E.SubElement(root, helper.T('sheetData')); rows = {}
        for addr, value in cells.items():
            number = str(helper.splitaddr(addr)[1])
            if number not in rows: rows[number] = E.SubElement(data, helper.T('row'), r=number)
            cell = E.SubElement(rows[number], helper.T('c'), r=addr, t='inlineStr')
            E.SubElement(E.SubElement(cell, helper.T('is')), helper.T('t')).text = value
        if name == 'Vacancies':
            links = E.SubElement(root, helper.T('hyperlinks'))
            E.SubElement(links, helper.T('hyperlink'), ref='J7', **{'{'+helper.R+'}id': 'joblink'})
            sheetrels = E.Element('{'+helper.P+'}Relationships')
            E.SubElement(sheetrels, '{'+helper.P+'}Relationship', Id='joblink',
                         Target='https://example.invalid/jobs/111', TargetMode='External')
            parts[f'xl/worksheets/_rels/sheet{index}.xml.rels'] = E.tostring(sheetrels)
        parts[f'xl/worksheets/sheet{index}.xml'] = E.tostring(root)
    parts['xl/workbook.xml'], parts['xl/_rels/workbook.xml.rels'] = E.tostring(wb), E.tostring(rels)
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as z:
        for name, data in parts.items(): z.writestr(name, data)
    return buffer.getvalue()


class WorkbookPreparationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source, self.curated = self.root / 'input.xlsx', self.root / 'curated.json'
        self.source.write_bytes(workbook_bytes())

    def prepare(self, records):
        self.curated.write_text(json.dumps(records))
        args = Namespace(source=str(self.source), curated=str(self.curated),
                         run_dir=str(self.root / 'run'), as_of='2026-09-28')
        helper.prepare(args)
        return json.loads((self.root / 'run/manifest.json').read_text())

    def test_review_joins_stable_identity_and_preserves_status(self):
        before = self.source.read_bytes()
        manifest = self.prepare([{'org': 'Example Org', 'id': '111', 'action': 'review',
                                 'fit': 'Conditional', 'gap': 'Confirm work authorization'}])
        row = manifest['records'][0]
        self.assertEqual((row['row'], row['plan_row'], row['priority']), (7, 10, 'P2'))
        self.assertEqual(manifest['existing_plan_rows'][0]['status'], 'In Progress')
        self.assertEqual(self.source.read_bytes(), before)

    def test_append_skips_existing_id_and_conflicting_review_is_rejected(self):
        self.assertEqual(len(self.prepare([{'org': 'Example Org', 'id': '111'}])['skipped']), 1)
        with self.assertRaisesRegex(ValueError, 'different source/input/date'):
            self.prepare([{'org': 'Example Org', 'id': '222', 'action': 'review'}])

    def test_url_cannot_override_disagreeing_organization_and_id(self):
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, 'conflicts with URL identity'):
            self.prepare([{'org': 'Different Agency', 'id': '999', 'action': 'refresh',
                           'title': 'Wrong target', 'apply_url': 'https://example.invalid/jobs/111'}])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.root / 'run/manifest.json').exists())

    def test_conditional_review_needs_visible_gap(self):
        with self.assertRaisesRegex(ValueError, 'visible, specific gap'):
            self.prepare([{'org': 'Example Org', 'id': '111', 'action': 'review', 'fit': 'Conditional'}])

    def test_date_only_cannot_be_promoted_to_a_precise_midnight(self):
        with self.assertRaisesRegex(ValueError, 'date-only'):
            self.prepare([{'org': 'Example Org', 'id': '111', 'action': 'refresh',
                           'date': '2026-10-05', 'date_precision': 'time'}])
        self.assertFalse((self.root / 'run/manifest.json').exists())
        self.assertEqual(helper.normalized_date('2026-10-05T00:00:00', 'time'),
                         ('2026-10-05T00:00:00', 'time'))

    def test_deadline_precision_and_timezone(self):
        self.assertEqual(helper.normalized_date('2026-10-05T20:30:00Z'), ('2026-10-05T23:30:00', 'time'))
        self.assertEqual(helper.normalized_date('2026-10-05'), ('2026-10-05', 'date'))
        with self.assertRaisesRegex(ValueError, 'discard'):
            helper.normalized_date('2026-10-05T20:30:00Z', 'date')


if __name__ == '__main__':
    unittest.main()
