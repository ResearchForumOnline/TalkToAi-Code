import tempfile
import unittest
from project_memory import read_memory,save_memory,LIMIT

class MemoryTests(unittest.TestCase):
    def test_persistence_and_project_isolation(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            self.assertEqual(read_memory(a),'')
            save_memory(a,'Use Godot; test with import check',previous='')
            self.assertIn('Godot',read_memory(a));self.assertEqual(read_memory(b),'')
    def test_stale_editor_and_size_limit(self):
        with tempfile.TemporaryDirectory() as a:
            save_memory(a,'new decision')
            with self.assertRaises(ValueError):save_memory(a,'old decision',previous='')
            with self.assertRaises(ValueError):save_memory(a,'x'*(LIMIT+1))
            self.assertEqual(read_memory(a),'new decision')
