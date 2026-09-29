"""Bounded catalog lookup tests with synthetic evidence-free records."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from validate_unops_fit import Catalog
from search_catalog import search


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog('synthetic', {
            'Health': {'catalog_no': '9', 'description': 'Public health systems.'},
            'Health financing': {'catalog_no': '51', 'description': 'Finance health systems.'},
            'Monitoring, Evaluation, and Learning': {'catalog_no': '100', 'description': 'Monitor projects.'},
        })

    def test_exact_first_and_bounded(self):
        result = search(self.catalog, 'Health', 1)
        self.assertEqual(result['matching_records'], 2)
        self.assertEqual(result['returned_records'], 1)
        self.assertEqual(result['skills'][0]['name'], 'Health')
        self.assertEqual(result['skills'][0]['description'], 'Public health systems.')

    def test_all_terms_and_description_search(self):
        self.assertEqual(search(self.catalog, 'finance systems')['skills'][0]['catalog_no'], '51')
        self.assertEqual(search(self.catalog, 'finance projects')['matching_records'], 0)

    def test_comma_label_retained(self):
        self.assertEqual(search(self.catalog, 'Monitoring, Evaluation, and Learning')['returned_records'], 1)

    def test_invalid_query_or_unbounded_limit(self):
        for query, limit in [('', 1), ('health', 0), ('health', 51)]:
            with self.assertRaises(ValueError):
                search(self.catalog, query, limit)
