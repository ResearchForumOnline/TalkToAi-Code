import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from routing_evaluation import audit_routing_evaluation


def sample():
    labels = [True, True, False, False, True]
    baseline = [('allow', .1), ('review', .8), ('review', .7), ('defer', .4), ('allow', .2)]
    candidate = [('review', .9), ('allow', .2), ('allow', .1), ('defer', .4), ('defer', .6)]
    return {
        'schema': 'talktoai.routing-evaluation.v1',
        'dataset': 'synthetic project fixture',
        'split': 'held_out',
        'event_definition': 'Review required under fixture rubric v1',
        'rubric_version': 'fixture-v1',
        'baseline_version': 'fixture-old',
        'candidate_version': 'fixture-new',
        'cases': [{'id': f'case-{i}', 'family': 'fixture-A' if i <= 3 else 'fixture-B',
                   'actual_review_required': label,
                   'baseline': {'route': before[0], 'predicted_review_probability': before[1]},
                   'candidate': {'route': after[0], 'predicted_review_probability': after[1]}}
                  for i, (label, before, after) in enumerate(zip(labels, baseline, candidate), 1)]}


class RoutingEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / 'evaluation.json'

    def write(self, document):
        self.path.write_text(json.dumps(document), encoding='utf-8')

    def audit(self):
        return json.loads(audit_routing_evaluation(self.root, 'evaluation.json'))

    def test_paired_counts_rates_and_forecast_scores(self):
        self.write(sample())
        initial = self.path.read_bytes()
        result = self.audit()
        self.assertEqual(self.path.read_bytes(), initial)
        self.assertEqual(result['source']['sha256'], hashlib.sha256(initial).hexdigest())
        self.assertEqual(result['cases'], 5)
        self.assertEqual(result['review_required'], 3)
        self.assertEqual(result['baseline']['counts']['false_allow'], 2)
        self.assertEqual(result['candidate']['counts']['false_allow'], 1)
        self.assertEqual(result['candidate']['counts']['defer_review_required'], 1)
        self.assertEqual(result['baseline']['rates']['false_allow_among_review_required'], .666667)
        self.assertEqual(result['candidate']['rates']['coverage'], .6)
        self.assertEqual(result['paired_changes']['prevented_false_allows'], 2)
        self.assertEqual(result['paired_changes']['introduced_false_allows'], 1)
        self.assertEqual(result['baseline']['forecast']['brier_score'], .428)
        self.assertEqual(result['candidate']['forecast']['brier_score'], .196)
        self.assertIn('Candidate introduces false allows', ' '.join(result['warnings']))
        self.assertEqual(result['by_family']['fixture-A']['cases'], 3)

    def test_without_probability_forecasts_reports_no_score(self):
        document = sample()
        for case in document['cases']:
            del case['baseline']['predicted_review_probability']
            del case['candidate']['predicted_review_probability']
        document['split'] = 'development'
        self.write(document)
        result = self.audit()
        self.assertEqual(result['baseline']['forecast']['status'], 'no_probabilities_reported')
        self.assertTrue(any('not declared held out' in warning for warning in result['warnings']))

    def test_partial_scores_rejected_instead_of_cherry_picking(self):
        document = sample()
        del document['cases'][0]['candidate']['predicted_review_probability']
        self.write(document)
        with self.assertRaisesRegex(ValueError, 'every baseline and candidate'):
            self.audit()

    def test_duplicate_cases_and_json_keys_rejected(self):
        document = sample()
        document['cases'][1]['id'] = document['cases'][0]['id']
        self.write(document)
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.audit()
        self.path.write_text('{"schema":"x","schema":"y"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON field'):
            self.audit()

    def test_nonfinite_and_boolean_probabilities_rejected(self):
        for value in (float('nan'), float('inf'), True, -0.1, 1.1):
            document = sample()
            document['cases'][0]['baseline']['predicted_review_probability'] = value
            self.write(document)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.audit()

    def test_private_or_external_file_is_not_read(self):
        self.write(sample())
        with self.assertRaises(ValueError):
            audit_routing_evaluation(self.root, '../evaluation.json')
        (self.root / 'secrets').mkdir()
        (self.root / 'secrets' / 'evaluation.json').write_bytes(self.path.read_bytes())
        with self.assertRaises(ValueError):
            audit_routing_evaluation(self.root, 'secrets/evaluation.json')

    def test_bounded_cases_and_one_class_warning(self):
        document = sample()
        document['cases'] = document['cases'] * 201
        for i, case in enumerate(document['cases']):
            case['id'] = str(i)
        self.write(document)
        with self.assertRaisesRegex(ValueError, '1 to 1,000'):
            self.audit()
        document = sample()
        document['cases'] = document['cases'][:1]
        self.write(document)
        result = self.audit()
        self.assertIsNone(result['candidate']['rates']['false_review_among_ordinary'])
        self.assertTrue(any('Only one outcome class' in warning for warning in result['warnings']))


if __name__ == '__main__':
    unittest.main()
