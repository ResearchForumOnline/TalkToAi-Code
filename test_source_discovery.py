from pathlib import Path
import tempfile
import unittest
import os
import threading
from unittest.mock import patch
import agent_core as core


class SourceDiscoveryTests(unittest.TestCase):
    def test_filename_filter_finds_source_beyond_unfiltered_result_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for index in range(1505):(root/f'capture-{index:04}.png').touch()
            (root/'main.gd').write_text('extends Node3D')
            (root/'main.tscn').write_text('[gd_scene]')
            tools=core.ProjectTools(root)
            self.assertNotIn('main.gd',tools.files())
            self.assertEqual(tools.execute('list_files',{'pattern':'*.gd'}),'main.gd')
            self.assertEqual(tools.execute('list_files',{'pattern':'*.tscn'}),'main.tscn')

    def test_pattern_is_optional_and_generated_directories_stay_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'main.gd').touch();(root/'.godot').mkdir();(root/'.godot'/'hidden.gd').touch()
            tools=core.ProjectTools(root)
            self.assertEqual(tools.execute('list_files',{}),'main.gd')
            self.assertEqual(tools.execute('list_files',{'pattern':'*.gd'}),'main.gd')
            self.assertNotIn('pattern',core.TOOLS[0]['function']['parameters']['required'])

    def test_pattern_path_and_missing_matches_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'scripts').mkdir();(root/'scripts'/'player.gd').touch()
            tools=core.ProjectTools(root)
            self.assertIn('player.gd',tools.execute('list_files',{'pattern':'scripts/*.gd'}))
            self.assertIn('No matching',tools.execute('list_files',{'pattern':'*.tscn'}))
            with self.assertRaises(ValueError):tools.execute('list_files',{'pattern':123})

    @unittest.skipUnless(os.name=='nt','Windows PowerShell recovery')
    def test_failed_shell_command_reports_actual_shell_and_native_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            result=core.ProjectTools(folder,act=True).execute('run_command',{'command':'Write-Output one && Write-Output two'})
        self.assertIn('Exit 1',result)
        self.assertIn('[Shell recovery]',result)
        self.assertIn('Windows PowerShell',result)
        self.assertIn('list_files with pattern',result)

    @unittest.skipUnless(os.name=='nt','Windows command failure classification')
    def test_test_compiler_and_generic_failures_do_not_get_shell_syntax_hints(self):
        with tempfile.TemporaryDirectory() as folder:
            tools=core.ProjectTools(folder,act=True)
            for output in ('AssertionError: expected ValueError was not raised','FAILED (failures=1)','error CS1002: expected semicolon','generic failure'):
                result=tools.execute('run_command',{'command':"Write-Output '"+output+"'; exit 1"})
                self.assertTrue(result.startswith('Exit 1'))
                self.assertIn(output,result)
                self.assertNotIn('[Shell recovery]',result)

    def test_phase_metrics_are_observed_not_invented(self):
        for extra,expected in [({'load_duration':26350000000,'prompt_eval_duration':1500000000,'eval_duration':950000000,'eval_count':16,'prompt_eval_count':120},{'load_seconds':26.35,'prompt_seconds':1.5,'generation_seconds':.95,'prompt_tokens':120}),({}, {})]:
            events=[]
            with tempfile.TemporaryDirectory() as folder,patch.object(core,'stream_chat',return_value=iter([{'message':{'content':'Done'},'done':True,**extra}])),patch.object(core,'model_supports_vision',return_value=False),patch.object(core,'AUTO_CONTEXT',False):
                core.run_agent('fixture','fixture',[{'role':'user','content':'Explain briefly'}],folder,False,threading.Event(),lambda k,v:events.append((k,v)))
            measured=[value for key,value in events if key=='metrics'][0]
            for field in ('load_seconds','prompt_seconds','generation_seconds','prompt_tokens'):
                if field in expected:self.assertEqual(measured[field],expected[field])
                else:self.assertNotIn(field,measured)

    def test_unfiltered_truncation_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for index in range(1501):(root/f'{index:04}.txt').touch()
            self.assertIn('File scan limit reached',core.ProjectTools(root).execute('list_files',{}))


if __name__=='__main__':unittest.main()
