"""The activity bar's text: what the agent is doing now, in plain words."""
import unittest

from activity_bar import ActivityFeed, describe_tool


class ActivityFeedTests(unittest.TestCase):
    def test_tool_calls_read_as_short_actions(self):
        self.assertEqual(describe_tool('run_command', {'command': 'cmake --build build'}), 'Running: cmake --build build')
        self.assertEqual(describe_tool('write_file', '{"path": "src/main.cpp", "content": "..."}'), 'Writing src/main.cpp')
        self.assertEqual(describe_tool('install_tool', {'name': 'blender'}), 'Installing blender')
        self.assertEqual(describe_tool('run_script', {'language': 'python', 'code': '# setup\nimport bpy\n'}),
                         'Running a python script: import bpy')
        self.assertEqual(describe_tool('build_native', {}), 'Building the C/C++ project')
        self.assertEqual(describe_tool('mystery_tool', None), 'Using mystery tool')
        self.assertLessEqual(len(describe_tool('run_command', {'command': 'x' * 500})), 96)

    def test_the_line_shows_the_running_action_and_its_time(self):
        now = [100.0]
        feed = ActivityFeed(clock=lambda: now[0])
        feed.begin()
        feed.tool('run_command', {'command': 'pip install numpy'})
        now[0] += 75
        feed.status('Working - step 3')   # a long command stays visible
        self.assertEqual(feed.line(), 'Running: pip install numpy · 1m 15s')
        feed.result('Exit 1\nerror')
        self.assertIn('Running: pip install numpy · failed', feed.history())
        feed.status('Working - step 4')
        self.assertEqual(feed.line(), 'Working - step 4')
        feed.finish()
        self.assertEqual(feed.line(), 'Done · 1 action')
        self.assertFalse(feed.busy)


if __name__ == '__main__':
    unittest.main()
