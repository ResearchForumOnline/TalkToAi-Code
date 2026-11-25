"""Effort levels, hardware-based model settings and the hardware readers. No hardware is changed."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auto_tune
import effort
import hardware_probe

GIB = auto_tune.GIB
SMALL_CARD = {'ram_bytes': 16 * GIB, 'vram_bytes': 6 * GIB, 'cpu_physical': 6,
              'gpus': [{'name': 'NVIDIA GeForce RTX 3060 Laptop GPU', 'vram_bytes': 6 * GIB, 'vendor': 'nvidia'}]}
NO_CARD = {'ram_bytes': 8 * GIB, 'vram_bytes': 0, 'cpu_physical': 4, 'gpus': []}
MAC = {'ram_bytes': 32 * GIB, 'vram_bytes': int(32 * GIB * .66), 'unified_memory': True,
       'gpus': [{'name': 'Apple silicon', 'vram_bytes': int(32 * GIB * .66), 'vendor': 'apple'}]}


class EffortTests(unittest.TestCase):
    def test_levels_scale_budgets_and_unknown_values_keep_the_earlier_limits(self):
        low, high, top = (effort.effort_profile(name) for name in ('low', 'high', 'max'))
        self.assertEqual(effort.effort_profile(' HIGH ').name, 'high')
        for unknown in (None, '', 'extreme', 3):
            self.assertIs(effort.effort_profile(unknown), effort.STANDARD)
        for field in ('steps', 'output_tokens', 'draft_tokens', 'draft_attempts', 'draft_continuations', 'command_seconds',
                      'chat_tokens', 'chat_continuations'):
            with self.subTest(field=field):
                self.assertLessEqual(getattr(low, field), getattr(high, field))
                self.assertLessEqual(getattr(high, field), getattr(top, field))
        self.assertEqual((effort.STANDARD.output_tokens, effort.STANDARD.draft_tokens, effort.STANDARD.chat_continuations), (1536, 3072, 0))
        self.assertEqual(effort.effort_from({'effort': 'max'}).name, 'max')

    def test_steps_and_command_time(self):
        high = effort.PROFILES['high']
        self.assertEqual(effort.step_budget(high), 32)
        self.assertEqual(effort.step_budget(high, 8), 8)
        self.assertEqual(effort.step_budget(high, 999), 64)
        self.assertEqual(effort.command_seconds(effort.STANDARD, 'pytest -q'), 180)
        for slow in ('pip install numpy', 'cmake --build build', 'make', 'winget install --id X', 'git clone https://x'):
            with self.subTest(command=slow):
                self.assertGreaterEqual(effort.command_seconds(effort.PROFILES['low'], slow), 900)
        self.assertEqual(effort.command_seconds(effort.PROFILES['max'], 'npm install'), 1800)


class AutoTuneTests(unittest.TestCase):
    def test_a_small_model_on_a_small_card_gets_more_context_at_higher_effort(self):
        openzero = int(1.1 * GIB)
        low = auto_tune.plan(SMALL_CARD, model='openzero', model_bytes=openzero, effort='low')
        high = auto_tune.plan(SMALL_CARD, model='openzero', model_bytes=openzero, effort='high')
        top = auto_tune.plan(SMALL_CARD, model='openzero', model_bytes=openzero, effort='max')
        self.assertEqual((low.num_ctx, low.placement), (8192, 'gpu'))
        self.assertEqual(high.num_ctx, 32768)
        # 64K would need about 7 GB for its context alone, more than this card has.
        self.assertEqual(top.num_ctx, 32768)
        self.assertIn('fits on the graphics card', top.summary())
        self.assertEqual(auto_tune.plan(MAC, model_bytes=openzero, effort='max').num_ctx, 65536)

    def test_models_that_do_not_fit_say_so_instead_of_guessing(self):
        large = auto_tune.plan(SMALL_CARD, model='qwen3.8:27b', model_bytes=18 * GIB, effort='max')
        self.assertEqual((large.num_ctx, large.placement), (8192, 'too_large'))
        self.assertIn('A smaller model is the reliable choice.', large.summary())
        self.assertEqual(large.options['top_k'], 20)
        shared = auto_tune.plan(SMALL_CARD, model='qwen3.5:9b', model_bytes=int(6.6 * GIB), effort='max')
        self.assertEqual(shared.placement, 'split')
        self.assertLessEqual(shared.num_ctx, 32768)
        self.assertIn('waits for them instead of timing out', shared.summary())
        unknown = auto_tune.plan({}, model_bytes=GIB, configured_ctx=16384)
        self.assertEqual((unknown.num_ctx, unknown.placement), (16384, 'unknown'))
        self.assertEqual(auto_tune.plan(MAC, model_bytes=int(6.6 * GIB), effort='max').placement, 'gpu')

    def test_suggestions_and_warnings_match_the_plan(self):
        self.assertEqual([item['id'] for item in auto_tune.suggest_models(SMALL_CARD)],
                         ['qwen3.5:4b', 'hf.co/shafire/Zero-Gemma4-E4B-OpenZero-GGUF', 'qwen3.5:9b'])
        self.assertEqual(len(auto_tune.suggest_models({})), len(auto_tune.LOCAL_MODELS))
        self.assertIn('likely to load very slowly or not at all', auto_tune.memory_check(18 * GIB, SMALL_CARD))
        self.assertEqual(auto_tune.memory_check(int(3.4 * GIB), SMALL_CARD), '')
        self.assertEqual(auto_tune.memory_check(GIB, {}), '')

    def test_only_known_local_options_in_their_ranges_are_passed_on(self):
        supplied = {'ollama_options': {'num_thread': 6, 'top_p': 0.95, 'top_k': 20.5, 'stop': ['x'], 'num_gpu': True,
                                       'min_p': 2, 'num_ctx': 99}}
        self.assertEqual(auto_tune.local_options(supplied), {'num_thread': 6, 'top_p': 0.95})
        self.assertEqual(auto_tune.local_options({'ollama_options': 'num_thread=6'}), {})
        self.assertEqual(auto_tune.sampling_preset('gemma4:e4b'), {})
        self.assertEqual(auto_tune.sampling_preset('hf.co/user/Qwen3.8-27B-GGUF')['top_p'], 0.95)

    def test_api_context_and_server_environment(self):
        self.assertEqual(auto_tune.api_context('https://api.openai.com/v1', 8192), 16384)
        self.assertEqual(auto_tune.api_context('https://api.anthropic.com/v1', 8192, effort='max'), 32768)
        self.assertEqual(auto_tune.api_context('https://api.groq.com/openai/v1', 8192), 8192)
        self.assertEqual(auto_tune.api_context('https://api.openai.com/v1', 8192, effort='low'), 8192)
        self.assertEqual(auto_tune.api_context('https://api.openai.com/v1', 8192, self_hosted=True), 8192)
        self.assertEqual(auto_tune.server_environment(SMALL_CARD, {}), {'OLLAMA_FLASH_ATTENTION': '1', 'OLLAMA_KV_CACHE_TYPE': 'q8_0'})
        self.assertEqual(auto_tune.server_environment(SMALL_CARD, {'OLLAMA_KV_CACHE_TYPE': 'f16'}), {'OLLAMA_FLASH_ATTENTION': '1'})
        self.assertEqual(auto_tune.server_environment(NO_CARD, {}), {})

    def test_the_app_started_server_gets_tuning_but_the_users_settings_win(self):
        import routing
        launched = []

        class Process:
            def poll(self):
                return None

        def popen(command, **options):
            launched.append(options['env'])
            return Process()

        with patch.object(routing, 'SERVER_ENVIRONMENT', {'OLLAMA_FLASH_ATTENTION': '1', 'OLLAMA_KV_CACHE_TYPE': 'q8_0', 'PATH': 'x'}), \
                patch.object(routing, '_version', side_effect=[None, '0.20.0']), \
                patch.object(routing, '_local_port_in_use', return_value=False), \
                patch.object(routing, 'ollama_executable', return_value='ollama'), \
                patch.object(routing, 'ollama_store', return_value='models'), \
                patch.object(routing.subprocess, 'Popen', side_effect=popen), \
                patch.dict(os.environ, {'OLLAMA_KV_CACHE_TYPE': 'f16'}):
            routing.require_local_runtime()
        self.assertEqual(launched[0]['OLLAMA_FLASH_ATTENTION'], '1')
        self.assertEqual(launched[0]['OLLAMA_KV_CACHE_TYPE'], 'f16')
        self.assertEqual(launched[0].get('PATH'), os.environ.get('PATH'))   # only the two tuning settings are ever added


class HardwareProbeTests(unittest.TestCase):
    def test_readers_parse_the_systems_answers(self):
        cards = hardware_probe.parse_nvidia('NVIDIA GeForce RTX 3060, 6144\nbroken line\nNVIDIA RTX 4090, 24564\n')
        self.assertEqual([(card['name'], card['vram_bytes']) for card in cards],
                         [('NVIDIA GeForce RTX 3060', 6 * GIB), ('NVIDIA RTX 4090', 24564 * 1024 * 1024)])
        two_threads_per_core = ''.join(f'processor\t: {n}\nphysical id\t: 0\ncore id\t: {n // 2}\n\n' for n in range(8))
        self.assertEqual(hardware_probe.parse_cpuinfo(two_threads_per_core), 4)
        self.assertEqual(hardware_probe.parse_cpuinfo('processor\t: 0\n'), 0)

    def test_description_never_invents_values(self):
        self.assertEqual(hardware_probe.describe(SMALL_CARD),
                         '6 GB graphics memory (NVIDIA GeForce RTX 3060 Laptop GPU) · 16 GB memory · 6 processor cores')
        self.assertEqual(hardware_probe.describe(NO_CARD), 'no graphics card detected · 8 GB memory · 4 processor cores')
        self.assertEqual(hardware_probe.describe({}), 'no graphics card detected')

    def test_a_recent_snapshot_is_reused_and_a_damaged_one_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            fresh = hardware_probe.cached(folder)
            self.assertTrue(Path(folder, 'hardware.json').is_file())
            with patch.object(hardware_probe, 'probe', side_effect=AssertionError('should reuse')):
                self.assertEqual(hardware_probe.cached(folder), fresh)
            Path(folder, 'hardware.json').write_text('{broken', encoding='utf-8')
            with patch.object(hardware_probe, 'probe', return_value=dict(NO_CARD, platform='x', probed_at='2026-01-01T00:00:00+00:00')):
                self.assertEqual(hardware_probe.cached(folder)['ram_bytes'], 8 * GIB)
            self.assertEqual(json.loads(Path(folder, 'hardware.json').read_text())['ram_bytes'], 8 * GIB)


if __name__ == '__main__':
    unittest.main()
