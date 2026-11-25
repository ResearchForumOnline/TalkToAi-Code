"""A requested file or 3D scene is delivered, or the user is told exactly why not."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_core as core
import scene_kit
from agent_core import SceneBuildError


def response(content='', calls=None, reason='stop'):
    message = {'content': content}
    if calls is not None:
        message['tool_calls'] = calls
    return {'message': message, 'done': True, 'done_reason': reason}


def call(name, **arguments):
    return {'function': {'name': name, 'arguments': arguments}}


def draft(content, path='scene.py'):
    return json.dumps({'path': path, 'content': content})


GOOD = 'import bpy\nbpy.ops.mesh.primitive_uv_sphere_add()\n'
SCENE_REQUEST = 'create an artistic design 3d model winter scene in nottingham england'


class Harness:
    def run_sequence(self, root, sequence, *, text=SCENE_REQUEST, effort='high', blender='blender-fixture',
                     build=None, rounds=16, performance=None, act=True, provider=None):
        payloads, events = [], []

        def stream(url, payload, cancel):
            payloads.append(copy.deepcopy(payload))
            if not sequence:
                raise AssertionError('Unexpected additional model request')
            return iter([sequence.pop(0)])

        tools = core.ProjectTools(root, act)
        settings = dict(performance or {})
        if effort:
            settings['effort'] = effort
        patches = [patch.object(core, 'stream_chat', side_effect=stream), patch.object(core, 'model_supports_vision', return_value=False),
                   patch.object(core, 'DESKTOP_ACCESS', False), patch.object(core, 'ACTIVE_REMOTE_ALLOWED', False),
                   patch.object(core, 'REMOTE_PILOT', False), patch.object(core, 'ACTIVE_PROVIDER', provider),
                   patch('engine_discovery.find_blender', return_value=blender),
                   patch('engine_discovery.discover_blender', return_value=blender)]
        if build is not None:
            patches.append(patch.object(tools, 'build_blender_scene', side_effect=build))
        for item in patches:
            item.start()
        try:
            core._run_agent('fixture', 'fixture', [{'role': 'user', 'content': text}], root, act, threading.Event(),
                            lambda kind, value: events.append((kind, value)), rounds, settings, tools, keep_going=True)
        finally:
            for item in reversed(patches):
                item.stop()
        return payloads, events

    @staticmethod
    def replies(events):
        return [value['content'] for kind, value in events if kind == 'message' and value.get('role') == 'assistant']

    def blender(self, root, accept=lambda source: True):
        built = []

        def build(blender, source, timeout=180):
            built.append(source)
            verdict = accept(source)
            if verdict is not True:
                raise SceneBuildError(verdict)
            scene = Path(root, f'blender-scene-{len(built)}.blend')
            scene.write_bytes(b'BLENDER fixture')
            return scene

        return built, build


class ScenePipelineTests(Harness, unittest.TestCase):
    def test_syntax_error_reports_line_and_caret_then_the_correction_builds(self):
        broken = 'import bpy\nbpy.ops.mesh.primitive_cube_add(\nbpy.ops.mesh.primitive_uv_sphere_add()\n'
        with tempfile.TemporaryDirectory() as root:
            built, build = self.blender(root)
            payloads, events = self.run_sequence(root, [response(draft(broken)), response(draft(GOOD))], build=build)
        feedback = payloads[1]['messages'][-1]['content']
        self.assertIn('Python syntax error in scene.py, line 2, column 32', feedback)
        self.assertIn('bpy.ops.mesh.primitive_cube_add(', feedback)
        self.assertIn('^', feedback)
        self.assertNotIn('<', feedback.split('Python syntax error')[1])
        self.assertEqual(built, [GOOD])
        self.assertIn(('status', 'Ready'), events)
        self.assertIn('needed 2 attempts', self.replies(events)[-1])

    def test_the_second_attempt_is_not_an_identical_request(self):
        with tempfile.TemporaryDirectory() as root:
            _, build = self.blender(root)
            payloads, _ = self.run_sequence(root, [response(draft('import bpy\nx = (')), response(draft(GOOD))], build=build)
        self.assertEqual(payloads[0]['options']['temperature'], 0)
        self.assertGreater(payloads[1]['options']['temperature'], 0)

    def test_cut_off_file_is_continued_instead_of_discarded(self):
        body = 'import bpy\n' + ''.join(f'bpy.ops.mesh.primitive_cube_add(location=({i}, 0, 0))\n' for i in range(40))
        whole = draft(body)
        cut = whole[:len(whole) // 2]
        saved = core.whole_lines(core.read_draft(cut, truncated=True).content)
        rest = body[len(saved):]
        with tempfile.TemporaryDirectory() as root:
            built, build = self.blender(root)
            payloads, events = self.run_sequence(root, [response(cut, reason='length'), response(draft(rest))], build=build)
        self.assertEqual(built, [body])
        self.assertIn('Continue from the line after this excerpt', payloads[1]['messages'][-1]['content'])
        self.assertGreaterEqual(payloads[1]['options']['num_predict'], payloads[0]['options']['num_predict'])
        self.assertTrue(any(kind == 'status' and 'Asking the model to continue (1 of 4)' in value for kind, value in events))
        self.assertIn(('status', 'Ready'), events)

    def test_fenced_code_and_wrapped_json_are_accepted(self):
        for reply in ('Here is the scene:\n```python\n' + GOOD + '```\nEnjoy!', 'Sure.\n```json\n' + draft(GOOD) + '\n```'):
            with self.subTest(reply=reply[:20]), tempfile.TemporaryDirectory() as root:
                built, build = self.blender(root)
                _, events = self.run_sequence(root, [response(reply)], build=build)
                self.assertEqual(built, [GOOD])
                self.assertIn(('status', 'Ready'), events)

    def test_calls_the_app_makes_itself_are_removed_before_blender_runs(self):
        script = GOOD + 'bpy.ops.render.render(write_still=True)\nbpy.ops.wm.save_as_mainfile(filepath="x.blend")\n'
        with tempfile.TemporaryDirectory() as root:
            built, build = self.blender(root)
            self.run_sequence(root, [response(draft(script))], build=build)
        self.assertNotIn('bpy.ops.render.render(write_still', built[0].replace('pass  # bpy.ops.render.render() removed', ''))
        self.assertIn('pass  # bpy.ops.wm.save_as_mainfile() removed', built[0])
        compile(built[0], 'scene.py', 'exec')

    def test_blender_runtime_error_is_fed_back_as_the_real_error(self):
        problem = ('Blender ran the script and Python raised: RuntimeError: Operator bpy.ops.object.mode_set.poll() failed, '
                   'context is incorrect\nAt scene.py line 2:\n    bpy.ops.object.mode_set(mode="EDIT")')
        with tempfile.TemporaryDirectory() as root:
            built, build = self.blender(root, accept=lambda source: True if 'mode_set' not in source else problem)
            payloads, events = self.run_sequence(root, [
                response(draft('import bpy\nbpy.ops.object.mode_set(mode="EDIT")\n')), response(draft(GOOD))], build=build)
        self.assertIn('context is incorrect', payloads[1]['messages'][-1]['content'])
        self.assertIn('At scene.py line 2', payloads[1]['messages'][-1]['content'])
        self.assertEqual(len(built), 2)
        self.assertIn(('status', 'Ready'), events)

    def test_failed_scripts_fall_back_to_the_models_scene_description(self):
        design = {'title': 'Frosty square', 'background': '#101830', 'objects': [
            {'shape': 'plane', 'at': [0, 0, 0], 'size': [30, 30, 0], 'color': '#eef4fb'},
            {'shape': 'box', 'at': [0, 0, 1], 'size': [2, 2, 2], 'color': '#b5553c'},
            {'shape': 'cone', 'at': [0, 0, 3], 'radius': 1.6, 'height': 1.5, 'color': '#ffffff'},
            {'shape': 'sphere', 'at': [3, 0, 0.5], 'radius': 0.5, 'color': '#ffffff', 'ring': {'count': 6, 'radius': 4}}]}
        with tempfile.TemporaryDirectory() as root:
            built, build = self.blender(root, accept=lambda source: True if 'build_in_blender' in source else 'Model script failed')
            payloads, events = self.run_sequence(root, [response(draft(GOOD)) for _ in range(4)] + [response(json.dumps(design))],
                                                 build=build)
            produced = sorted(path.name for path in Path(root).iterdir())
        self.assertEqual(len(payloads), 5)
        self.assertEqual(payloads[4]['format'], scene_kit.SCENE_FORMAT)
        self.assertIn('Design the requested 3D scene as data', payloads[4]['messages'][-1]['content'])
        self.assertIn('build_in_blender(SCENE)', built[-1])
        self.assertTrue(any(name.endswith('-preview.png') for name in produced), produced)
        reply = self.replies(events)[-1]
        self.assertIn('Built from the scene design the model wrote', reply)
        self.assertIn('Frosty square', reply)
        self.assertEqual([value['state'] for kind, value in events if kind == 'goal_checkpoint'][-1], 'completed')

    def test_when_every_model_route_fails_the_built_in_generator_still_delivers_and_says_so(self):
        with tempfile.TemporaryDirectory() as root:
            _, build = self.blender(root, accept=lambda source: True if 'build_in_blender' in source else 'Model script failed')
            sequence = [response(draft('import bpy\nx = (')) for _ in range(4)] + [response('not json'), response('{"objects": []}')]
            payloads, events = self.run_sequence(root, sequence, build=build)
        self.assertEqual(len(payloads), 6)
        reply = self.replies(events)[-1]
        self.assertIn('did not produce a working scene in 6 attempts', reply)
        self.assertIn('built-in design, not the model’s own', reply)
        self.assertIn('Winter evening in Nottingham', reply)
        self.assertIn('Python syntax error in scene.py, line 2', reply)
        self.assertIn(('status', 'Ready'), events)
        self.assertFalse(any(kind == 'status' and 'task unfinished' in value for kind, value in events))

    def test_without_blender_the_scene_is_a_checked_glb_with_a_viewer(self):
        design = {'title': 'Portal', 'background': '#000010', 'objects': [
            {'shape': 'torus', 'at': [0, 0, 3], 'radius': 2, 'tube': 0.2, 'color': '#00e5ff', 'glow': 5, 'rotate': [90, 0, 0]},
            {'shape': 'cylinder', 'at': [0, 0, 0], 'radius': 6, 'height': 0.4, 'color': '#222233'},
            {'shape': 'crystal', 'at': [0, 0, 1], 'radius': 0.4, 'height': 2, 'color': '#b388ff', 'ring': {'count': 8, 'radius': 4}},
            {'shape': 'gem', 'at': [0, 0, 3], 'radius': 0.5, 'color': '#ffffff', 'glow': 8}]}
        with tempfile.TemporaryDirectory() as root:
            payloads, events = self.run_sequence(root, [response(json.dumps(design))], blender=None,
                                                 text='make a 3d design anything you want make it wow cool etc extreme effort')
            files = {path.suffix: path for path in Path(root).iterdir() if path.is_file()}
            self.assertEqual(scene_kit.check_glb(files['.glb'].read_bytes())['objects'], 11)
            page = files['.html'].read_text(encoding='utf-8')
        self.assertEqual(len(payloads), 1)
        self.assertNotIn('tools', payloads[0])
        self.assertNotIn('http://', page)
        self.assertNotIn('https://', page)
        self.assertIn('Blender was not found on this computer', self.replies(events)[-1])
        verification = [value for kind, value in events if kind == 'verification'][-1]
        self.assertEqual(verification['status'], 'passed')
        self.assertIn('read back', verification['summary'])
        self.assertTrue(any(kind == 'artifact' and value.get('auto_open') for kind, value in events))

    def test_a_stale_blender_path_in_settings_does_not_end_the_request(self):
        with tempfile.TemporaryDirectory() as root:
            built, build = self.blender(root)
            with patch('engine_discovery.find_blender', side_effect=ValueError('The configured Blender executable does not exist.')):
                tools_events = []
                payloads = []

                def stream(url, payload, cancel):
                    payloads.append(payload)
                    return iter([response(draft(GOOD))])
                tools = core.ProjectTools(root, True)
                with patch.object(core, 'stream_chat', side_effect=stream), patch.object(core, 'model_supports_vision', return_value=False), \
                        patch('engine_discovery.discover_blender', return_value='blender-fixture'), \
                        patch.object(tools, 'build_blender_scene', side_effect=build):
                    core._run_agent('fixture', 'fixture', [{'role': 'user', 'content': SCENE_REQUEST}], root, True,
                                    threading.Event(), lambda kind, value: tools_events.append((kind, value)), 16,
                                    {'effort': 'high'}, tools)
        self.assertEqual(built, [GOOD])
        self.assertIn(('status', 'Ready'), tools_events)

    def test_missing_blender_is_installed_when_the_user_allows_it(self):
        with tempfile.TemporaryDirectory() as root:
            built, build = self.blender(root)
            with patch('engine_install.ensure_engine', return_value='blender-installed') as install:
                payloads, events = self.run_sequence(root, [response(draft(GOOD))], blender=None, build=build,
                                                     performance={'auto_install': True})
        install.assert_called_once()
        self.assertEqual(install.call_args.args[0], 'blender')
        self.assertEqual(built, [GOOD])
        self.assertIn(('status', 'Ready'), events)

    def test_installs_never_run_unless_the_setting_is_on(self):
        with tempfile.TemporaryDirectory() as root, patch('engine_install.ensure_engine') as install:
            self.run_sequence(root, [response('{"objects": []}'), response('{"objects": []}')], blender=None)
        install.assert_not_called()


class FileDraftTests(Harness, unittest.TestCase):
    def test_long_page_is_written_from_two_parts(self):
        page = '<!DOCTYPE html>\n<html>\n<body>\n' + ''.join(f'<p>Line {i}</p>\n' for i in range(60)) + '</body>\n</html>\n'
        whole = json.dumps({'path': 'index.html', 'content': page})
        cut = whole[:len(whole) * 2 // 3]
        saved = core.whole_lines(core.read_draft(cut, truncated=True).content)
        with tempfile.TemporaryDirectory() as root:
            _, events = self.run_sequence(root, [
                response("I'll create the page."), response(cut, reason='length'),
                response(json.dumps({'path': 'index.html', 'content': page[len(saved):]})),
                response('Saved index.html and checked its structure.')], text='Create a web page')
            self.assertEqual(Path(root, 'index.html').read_text(encoding='utf-8'), page)
        self.assertIn(('status', 'Ready'), events)

    def test_paths_outside_the_project_are_never_written_by_any_reply_shape(self):
        with tempfile.TemporaryDirectory() as root:
            _, events = self.run_sequence(root, [
                response("I'll create the page."),
                response('{"path": "../escaped.html", "content": "<body>\\npartial\\nmore', reason='length'),
                response(json.dumps({'path': '../escaped.html', 'content': '</body>\n'})),
                response(json.dumps({'path': '.hidden/x.html', 'content': '<body>x</body>'})),
                response('```html\n<body>y</body>\n```'),
                response(json.dumps({'path': 'C:\\Windows\\x.html', 'content': '<body>z</body>'}))],
                text='Create a web page')
            self.assertEqual([path.name for path in Path(root).iterdir() if path.name != '.talktoai-code'], [])
            self.assertFalse(Path(root).parent.joinpath('escaped.html').exists())
        self.assertFalse(any(kind == 'change' for kind, _ in events))
        self.assertNotIn(('status', 'Ready'), events)

    def test_a_continuation_cannot_redirect_the_file_it_is_finishing(self):
        page = '<!DOCTYPE html>\n<html>\n<body>\n<p>one</p>\n<p>two</p>\n</body>\n</html>\n'
        whole = json.dumps({'path': 'index.html', 'content': page})
        cut = whole[:whole.index('<p>two')]
        saved = core.whole_lines(core.read_draft(cut, truncated=True).content)
        with tempfile.TemporaryDirectory() as root:
            self.run_sequence(root, [
                response("I'll create the page."), response(cut, reason='length'),
                response(json.dumps({'path': 'other/elsewhere.html', 'content': page[len(saved):]})),
                response(calls=[call('run_checks')]), response('Saved the page and checked its structure.')],
                text='Create a web page')
            self.assertEqual(Path(root, 'index.html').read_text(encoding='utf-8'), page)
            self.assertFalse(Path(root, 'other').exists())

    def test_failure_tells_the_user_what_happened(self):
        with tempfile.TemporaryDirectory() as root:
            _, events = self.run_sequence(root, [response("I'll create the page.")] + [response('I cannot.') for _ in range(4)],
                                          text='Create a web page', effort='high')
        reply = self.replies(events)[-1]
        self.assertIn('did not return a complete file in 4 attempts', reply)
        self.assertIn('The reply did not contain the file', reply)
        self.assertTrue(any(kind == 'status' and 'task unfinished' in value for kind, value in events))

    def test_cut_off_write_tool_call_becomes_a_continuable_file_reply(self):
        script = "print('hello')\n"
        with tempfile.TemporaryDirectory() as root:
            payloads, events = self.run_sequence(root, [
                response(calls=[call('write_file', path='tool.py', content='print(')], reason='length'),
                response(json.dumps({'path': 'tool.py', 'content': script})),
                response(calls=[call('run_checks')]),
                response('Saved tool.py; its syntax check passed.')], text='Improve this project')
            self.assertEqual(Path(root, 'tool.py').read_text(encoding='utf-8'), script)
        self.assertNotIn('tools', payloads[1])
        self.assertIn('"tool.py"', payloads[1]['messages'][-1]['content'])
        self.assertFalse(any(kind == 'tool' and value['args'].get('content') == 'print(' for kind, value in events))

    def test_standard_effort_keeps_the_earlier_limits(self):
        with tempfile.TemporaryDirectory() as root:
            payloads, events = self.run_sequence(root, [
                response("I'll create the page."),
                response(json.dumps({'path': 'index.html', 'content': '<body>partial</body>'}), reason='length'),
                response(json.dumps({'path': '../escaped.html', 'content': '<body>also invalid</body>'}))],
                text='Create a web page', rounds=4, effort=None)
            self.assertFalse(Path(root, 'index.html').exists())
        self.assertEqual(len(payloads), 3)
        self.assertEqual(payloads[1]['options']['num_predict'], 3072)


class QuestionTests(Harness, unittest.TestCase):
    def test_question_does_not_stop_the_run_when_the_user_chose_to_carry_on(self):
        question = 'What name should I use in the greeting?'
        with tempfile.TemporaryDirectory() as root:
            payloads, events = self.run_sequence(root, [
                response(calls=[call('ask_user', question=question)]),
                response(calls=[call('write_file', path='hello.py', content="print('Hello')\n")]),
                response('Created hello.py using a generic greeting because no name was given.')],
                text='Create a Python script that prints a greeting.', performance={'questions': 'continue'})
            self.assertTrue(Path(root, 'hello.py').is_file())
        self.assertIn(('question_open', {'question': question}), events)
        self.assertNotIn(('status', 'Waiting for your answer'), events)
        answer = next(message for message in payloads[1]['messages'] if message.get('tool_name') == 'ask_user')
        self.assertIn('Do not guess about anything destructive', answer['content'])
        shown = next(value for kind, value in events if kind == 'message' and value.get('tool_calls'))
        self.assertIn(question, shown['content'])

    def test_question_still_waits_by_default_and_after_three_unanswered_ones(self):
        question = 'Which file?'
        with tempfile.TemporaryDirectory() as root:
            _, events = self.run_sequence(root, [response(calls=[call('ask_user', question=question)])],
                                          text='Fix the project')
            self.assertIn(('status', 'Waiting for your answer'), events)
            _, events = self.run_sequence(root, [response(calls=[call('ask_user', question=question + str(i))]) for i in range(4)],
                                          text='Fix the project', performance={'questions': 'continue'})
        self.assertIn(('status', 'Waiting for your answer'), events)
        self.assertEqual(sum(kind == 'question_open' for kind, _ in events), 3)


class BudgetTests(Harness, unittest.TestCase):
    def test_file_reply_gets_room_and_a_short_system_prompt(self):
        with tempfile.TemporaryDirectory() as root:
            _, build = self.blender(root)
            payloads, _ = self.run_sequence(root, [response(draft(GOOD))], build=build, performance={'num_ctx': 32768})
        self.assertEqual(payloads[0]['options']['num_predict'], 8192)
        system = payloads[0]['messages'][0]['content']
        self.assertLess(len(system), 2500)
        self.assertIn('TalkToAi operating policy', system)
        self.assertEqual(payloads[0]['format'], core.NEW_FILE_FORMAT)

    def test_small_context_gets_the_largest_reply_that_fits(self):
        with tempfile.TemporaryDirectory() as root:
            _, build = self.blender(root)
            payloads, _ = self.run_sequence(root, [response(draft(GOOD))], build=build, performance={'num_ctx': 8192})
        self.assertGreater(payloads[0]['options']['num_predict'], 3072)
        self.assertLess(payloads[0]['options']['num_predict'], 8192)

    def test_step_budget_extends_itself_while_work_progresses(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'a.txt').write_text('one', encoding='utf-8')
            Path(root, 'b.txt').write_text('two', encoding='utf-8')
            payloads, events = [], []
            sequence = [response(calls=[call('read_file', path='a.txt')]), response(calls=[call('read_file', path='b.txt')]),
                        response('Both files were read.')]

            def stream(url, payload, cancel):
                payloads.append(payload)
                return iter([sequence.pop(0)])
            with patch.object(core, 'stream_chat', side_effect=stream), patch.object(core, 'model_supports_vision', return_value=False):
                core._run_agent('fixture', 'fixture', [{'role': 'user', 'content': 'What do a.txt and b.txt say?'}], root, False,
                                threading.Event(), lambda kind, value: events.append((kind, value)), 2, {'effort': 'high'},
                                core.ProjectTools(root, False))
        self.assertEqual(len(payloads), 3)
        self.assertIn(('status', 'Ready'), events)
        self.assertTrue(any(kind == 'status' and 'continuing automatically' in value for kind, value in events))

    def test_api_models_are_not_sent_local_options(self):
        provider = type('Profile', (), {'supports_vision': False, 'label': 'API', 'model': 'fixture'})()
        with tempfile.TemporaryDirectory() as root:
            _, build = self.blender(root)
            payloads, _ = self.run_sequence(root, [response('', reason='length'), response(draft(GOOD))], build=build,
                                            provider=provider, performance={'ollama_options': {'num_thread': 6}})
        self.assertNotIn('format', payloads[0])
        self.assertNotIn('num_thread', payloads[0]['options'])
        self.assertEqual(payloads[1]['options']['reasoning_ceiling'], 'low')

    def test_local_tuning_options_reach_the_local_model_only_from_the_allowed_list(self):
        with tempfile.TemporaryDirectory() as root:
            _, build = self.blender(root)
            payloads, _ = self.run_sequence(root, [response(draft(GOOD))], build=build,
                                            performance={'ollama_options': {'num_thread': 6, 'num_ctx': 99, 'stop': ['x']}})
        self.assertEqual(payloads[0]['options']['num_thread'], 6)
        self.assertNotEqual(payloads[0]['options']['num_ctx'], 99)
        self.assertNotIn('stop', payloads[0]['options'])


if __name__ == '__main__':
    unittest.main()
