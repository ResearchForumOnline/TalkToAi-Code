"""Reading a file out of a model reply, and checking a Blender script before Blender runs it."""
import json
import unittest

import file_draft as drafts
import scene_lint as lint


class ReadDraftTests(unittest.TestCase):
    def test_every_reply_shape_models_actually_send(self):
        page = json.dumps({'path': 'a.py', 'content': 'x = 1\n'})
        cases = {
            page: ('a.py', 'x = 1\n', True, 'JSON'),
            'Here it is:\n```json\n' + page + '\n```\nDone.': ('a.py', 'x = 1\n', True, 'JSON in prose'),
            'main.py\n```python\nprint(1)\n```': ('main.py', 'print(1)\n', True, 'code block'),
            '**File: tools/run.py**\n```python\nprint(2)\n```': ('tools/run.py', 'print(2)\n', True, 'code block'),
        }
        for reply, expected in cases.items():
            with self.subTest(reply=reply[:30]):
                found = drafts.read_draft(reply)
                self.assertEqual((found.path, found.content, found.complete, found.source), expected)

    def test_a_reply_cut_off_by_the_output_limit_keeps_what_arrived(self):
        found = drafts.read_draft('{"path": "a.py", "content": "x = 1\\ny = (', truncated=True)
        self.assertEqual((found.path, found.content, found.complete), ('a.py', 'x = 1\ny = (', False))
        fenced = drafts.read_draft('```python\nprint(1)\nprint(2', default_path='s.py', truncated=True)
        self.assertEqual((fenced.path, fenced.complete), ('s.py', False))
        closed = drafts.read_draft(json.dumps({'path': 'a.py', 'content': 'ok\n'}), truncated=True)
        self.assertTrue(closed.complete)

    def test_broken_or_ambiguous_replies_are_not_files(self):
        for reply in ('', '   ', 'I cannot do that.', '{"path": "a.py", "content": 5}',
                      '{"path": "a.py", "content": "x", "path": "b.py"}', '{"path": "a.py", "content": "x'):
            with self.subTest(reply=reply):
                self.assertIsNone(drafts.read_draft(reply))
        # A code block with no name has no path of its own; the caller supplies one or rejects it.
        self.assertIsNone(drafts.read_draft('```python\nprint(1)\n```').path)
        self.assertEqual(drafts.read_draft('```python\nprint(1)\n```', default_path='scene.py').path, 'scene.py')
        self.assertEqual(drafts.read_draft('import bpy\nbpy.ops.x()', default_path='scene.py').content, 'import bpy\nbpy.ops.x()\n')
        self.assertIsNone(drafts.read_draft('Sure! I will make it now.', default_path='scene.py'))

    def test_json_objects_in_replies(self):
        self.assertEqual(drafts.read_json_object('x {"a": 1} y'), {'a': 1})
        self.assertEqual(drafts.read_json_object('```json\n{"a": [1, 2]}\n```'), {'a': [1, 2]})
        with self.assertRaisesRegex(ValueError, 'not a JSON object'):
            drafts.read_json_object('nothing here')

    def test_continuations_join_without_repeats_or_restarts(self):
        self.assertEqual(drafts.whole_lines('a\nb\nc'), 'a\nb\n')
        self.assertEqual(drafts.whole_lines('abc'), '')
        self.assertEqual(drafts.join_continuation('a\nb\nline three\n', 'line three\nd\n'), ('a\nb\nline three\nd\n', False))
        self.assertEqual(drafts.join_continuation('a\nb\n}\n', '}\nd\n'), ('a\nb\n}\n}\nd\n', False))
        saved = ''.join(f'line {n}\n' for n in range(10))
        self.assertEqual(drafts.join_continuation(saved, saved + 'line 10\n'), (saved + 'line 10\n', True))
        request = drafts.continuation_request('a.py', 'l1\nl2\nl3\npartial')
        self.assertIn('3 complete lines are saved', request)
        self.assertIn('"a.py"', request)
        self.assertTrue(request.endswith('l1\nl2\nl3'))


class SceneLintTests(unittest.TestCase):
    def test_a_syntax_error_names_the_line_with_no_angle_brackets(self):
        source = 'import bpy\nbpy.ops.mesh.primitive_cube_add(\nbpy.ops.x()\n'
        with self.assertRaises(SyntaxError) as caught:
            compile(source, 'scene.py', 'exec')
        message = lint.describe_syntax_error(caught.exception, source, name='scene.py')
        self.assertTrue(message.startswith('Python syntax error in scene.py, line 2, column 32'))
        self.assertIn('    bpy.ops.mesh.primitive_cube_add(\n', message)
        self.assertNotIn('<', message)
        self.assertTrue(lint.stops_early(caught.exception, source))

    def test_repairs_are_kept_only_when_the_result_compiles(self):
        repaired, fixes = lint.repair_script('```python\nimport bpy\nprint(“hi”)\n```')
        self.assertEqual(repaired, 'import bpy\nprint("hi")\n')
        self.assertEqual(len(fixes), 2)
        prose, fixes = lint.repair_script('Here is the script:\n\nimport bpy\nx = 1\n\nHope this helps!')
        compile(prose, 'scene.py', 'exec')
        self.assertIn('removed text around the code', fixes)
        self.assertEqual(lint.repair_script('import bpy\nx = ('), ('import bpy\nx = (', []))

    def test_calls_that_would_end_or_replace_the_build_are_removed(self):
        source, notes, problems = lint.lint_blender_script(
            'import bpy\nbpy.ops.render.render(write_still=True)\nimport sys\nsys.exit(0)\n')
        compile(source, 'scene.py', 'exec')
        self.assertNotIn('render.render(write', source)
        self.assertNotIn('sys.exit(0)', source)
        self.assertEqual((len(notes), problems), (2, []))
        self.assertIn('added the missing "import bpy"', lint.lint_blender_script('bpy.ops.mesh.primitive_cube_add()\n')[1])
        self.assertEqual(lint.lint_blender_script('x = 1\n')[2], ['The script never uses bpy, so it cannot build a Blender scene.'])
        self.assertIn('Remove the use of subprocess.', lint.lint_blender_script('import bpy, subprocess\nsubprocess.run(["x"])\n')[2][-1])

    def test_blender_output_becomes_one_actionable_message(self):
        output = ('Blender 4.2\nTraceback (most recent call last):\n  File "/tmp/x/scene.py", line 3, in <module>\n'
                  '    bpy.ops.object.mode_set(mode="EDIT")\n'
                  'RuntimeError: Operator bpy.ops.object.mode_set.poll() failed, context is incorrect\n')
        message = lint.describe_blender_failure(output, 'import bpy\n\nbpy.ops.object.mode_set(mode="EDIT")\n',
                                                script_file='/tmp/x/scene.py')
        self.assertIn('RuntimeError: Operator bpy.ops.object.mode_set.poll() failed', message)
        self.assertIn('At scene.py line 3:\n    bpy.ops.object.mode_set(mode="EDIT")', message)
        self.assertIn('Hint:', message)
        self.assertNotIn('/tmp/x', message)
        self.assertEqual(lint.describe_blender_failure('', ''), 'Blender stopped without a Python traceback.')


if __name__ == '__main__':
    unittest.main()
