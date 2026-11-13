import json
import unittest
from pathlib import Path


class AnalysisInputsTests(unittest.TestCase):
    def load_data(self):
        return json.loads((Path(__file__).resolve().parents[1] / "fixtures/analysis/input.json").read_text())

    def test_empty_stratum(self):
        data = self.load_data()
        self.assertTrue(any(row['stratum'] == 'new' for row in data['frame']))

    def test_retest_chain(self):
        data = self.load_data()
        self.assertTrue(any('supersedes' in row for row in data['tests']))

    def test_unequal_probability(self):
        data = self.load_data()
        self.assertTrue(len({row['conditional_inclusion_probability'] for row in data['draws']}) == 2)

    def test_source_versions_and_cutoff(self):
        data = self.load_data()
        self.assertEqual(data["schema_version"], 1)
        self.assertTrue(data["source_versions"])
        self.assertIn("+00:00", data["as_of"])
