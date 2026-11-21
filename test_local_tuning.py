"""The model tuner must earn a preference edit with verified runtime evidence."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app_preferences import AppPreferenceManager
from local_tuning import Phase, apply_retention, measure_retention, undo_retention
from model_library import DownloadCancelled


class RetentionTuningTests(unittest.TestCase):
    def setUp(self):
        self.cancel = threading.Event()
        self.notify = Mock()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = {'num_ctx': 8192}
        self.save = Mock()
        self.settings = AppPreferenceManager(self.config, self.save, Path(folder.name) / 'audit.jsonl')
        route = {'model': 'fixture:latest', 'url': 'http://127.0.0.1:11434'}
        for key, result in [('require_local_runtime', '0.40'), ('choose_route', route),
                            ('verify_local_model', None), ('running_probe', {'state':'reachable', 'models':[]})]:
            handle = patch('local_tuning.' + key, return_value=result)
            setattr(self, key, handle.start())
            self.addCleanup(handle.stop)

    def observations(self, **warmer):
        first = {'outcome':'verified', 'preview':'5', 'digest':'a' * 64, 'load_seconds':8.2,
                 'num_ctx':4096, 'was_loaded':False}
        second = dict(first, outcome='different', preview='2 + 3 = 5', was_loaded=True, load_seconds=.2)
        second.update(warmer)
        return [first, second]

    def trial(self, observations):
        with patch('local_tuning.measure_local_reply', side_effect=observations) as measure:
            trial = measure_retention(self.config, 'local_large', self.cancel, self.notify)
        for call in measure.call_args_list:
            self.assertEqual({'keep_alive':'60m', 'num_ctx':8192}, call.kwargs)
        return trial

    def test_matched_correct_cold_warm_replies_allow_audited_change_and_undo(self):
        trial = self.trial(self.observations())
        self.assertEqual(Phase.BOUNDARY, trial.phase)
        self.assertEqual('4h', trial.candidate)
        self.save.assert_not_called()
        applied, record = apply_retention(trial, self.settings, self.cancel)
        self.assertEqual(Phase.EMERGING, applied.phase)
        self.assertEqual('4h', self.config['ollama_keep_alive'])
        self.assertEqual(record['change_id'], self.settings.history()[-1]['id'])
        self.assertEqual('local-tuning', self.settings.history()[-1]['origin'])
        repeated, result = apply_retention(trial, self.settings, self.cancel)
        self.assertEqual(Phase.BOUNDARY, repeated.phase)
        self.assertIsNone(result)
        self.save.assert_called_once()
        undo_retention(self.settings)
        self.assertNotIn('ollama_keep_alive', self.config)
        with self.assertRaisesRegex(ValueError, 'no recent'):
            undo_retention(self.settings)
        self.assertEqual(2, self.save.call_count)

    def test_unverified_or_unmatched_pairs_never_apply(self):
        cases = ({'preview':'5 or 6'}, {'digest':'b'*64}, {'num_ctx':8192}, {'was_loaded':False},
                 {'load_seconds':float('inf')}, {'load_seconds':8.1}, {'outcome':'incomplete'})
        for changes in cases:
            with self.subTest(changes=changes):
                trial = self.trial(self.observations(**changes))
                self.assertIsNone(trial.candidate)
                self.assertEqual((trial, None), apply_retention(trial, self.settings, self.cancel))
        self.save.assert_not_called()

    def test_cancel_or_new_setting_cannot_be_overwritten(self):
        trial = self.trial(self.observations())
        self.config['ollama_keep_alive'] = '24h'
        self.assertIsNone(apply_retention(trial, self.settings, self.cancel)[1])
        self.cancel.set()
        with self.assertRaises(DownloadCancelled):
            apply_retention(trial, self.settings, self.cancel)
        self.save.assert_not_called()

    def test_tuning_does_not_replace_any_existing_runner(self):
        for model in ('fixture:latest', 'another:latest'):
            with self.subTest(model=model):
                self.running_probe.return_value = {'state':'reachable', 'models':[{'name':model}]}
                with patch('local_tuning.measure_local_reply') as measure:
                    trial = measure_retention(self.config, 'auto', self.cancel, self.notify)
                measure.assert_not_called()
                self.assertIsNone(trial.candidate)
        self.choose_route.assert_called_with(self.config, 'auto', include_server=False)

    def test_refuses_a_cloud_alias_before_sending_any_fixture(self):
        self.verify_local_model.side_effect = ValueError('Cloud-backed model')
        with patch('local_tuning.measure_local_reply') as measure:
            with self.assertRaisesRegex(ValueError, 'Cloud-backed'):
                measure_retention(self.config, 'provider', self.cancel, self.notify)
        measure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
