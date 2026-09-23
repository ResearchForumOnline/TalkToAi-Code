import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_core import ProjectTools
from process_jobs import ProcessJobs
from workspace_outputs import read_batch, register_output


class WorkspaceReadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.tools=ProjectTools(self.root,False)
    def tearDown(self):self.temp.cleanup()

    def test_batch_has_hashes_and_per_file_errors(self):
        (self.root/'a.txt').write_text('hello',encoding='utf-8')
        result=json.loads(read_batch(self.tools,[{'path':'a.txt'},{'path':'missing'},{'path':'../outside'},{'path':'.env'}]))['files']
        self.assertEqual(result[0]['content'],'hello');self.assertTrue(result[0]['complete'])
        self.assertEqual(result[0]['sha256'],hashlib.sha256(b'hello').hexdigest())
        self.assertTrue(all('error' in item for item in result[1:]))

    def test_pages_can_be_reassembled_and_reject_stale_hash(self):
        text='😀 sample\n'*2500;(self.root/'a.txt').write_bytes(text.encode('utf-8'))
        request={'path':'a.txt'};parts=[]
        for _ in range(20):
            item=json.loads(read_batch(self.tools,[request]))['files'][0];parts.append(item['content'])
            if item['complete']:break
            request.update(offset=item['next_offset'],expected_sha256=item['sha256'])
        else:self.fail('Pagination did not complete')
        self.assertEqual(''.join(parts),text)
        (self.root/'a.txt').write_text('changed',encoding='utf-8')
        self.assertIn('changed',json.loads(read_batch(self.tools,[request]))['files'][0]['error'])

    def test_control_characters_stay_inside_serialized_budget(self):
        (self.root/'a.txt').write_text('\x01'*20000,encoding='utf-8')
        raw=read_batch(self.tools,[{'path':'a.txt'}]*8)
        self.assertLess(len(raw),20000)
        self.assertTrue(all(f['next_offset']>0 for f in json.loads(raw)['files']))

    def test_invalid_batch_and_binary_are_rejected(self):
        for invalid in ([],[{}],[{'path':'x'}]*9):
            with self.assertRaises(ValueError):read_batch(self.tools,invalid)
        (self.root/'blob').write_bytes(b'\x00binary')
        self.assertIn('Binary',json.loads(read_batch(self.tools,[{'path':'blob'}]))['files'][0]['error'])

    def test_output_registration_does_not_modify_or_execute_file(self):
        source=self.root/'build.exe';source.write_bytes(b'fixture, not an executable')
        item=json.loads(register_output(self.tools,'build.exe','Game build'))
        self.assertEqual(item['type'],'file');self.assertEqual(item['title'],'Game build')
        self.assertEqual(item['bytes'],source.stat().st_size)
        self.assertEqual(item['sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
        with self.assertRaises(ValueError):register_output(self.tools,'../outside')
        with self.assertRaises(PermissionError):self.tools.execute('register_output',{'path':'build.exe'})

    def test_plan_mode_blocks_every_process_operation(self):
        for name in ('start_process','poll_process','cancel_process'):
            with self.assertRaises(PermissionError):self.tools.execute(name,{})


class ProcessJobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.events=[]
        self.cancel=threading.Event();self.jobs=ProcessJobs(self.root,self.cancel,lambda k,v:self.events.append((k,v)))
    def tearDown(self):self.jobs.close();self.temp.cleanup()

    def start(self,script,timeout='10'):
        return self.jobs.start(sys.executable,['-u','-c',script],timeout_seconds=timeout)['id']

    def finish(self,key,seconds=10):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            result=self.jobs.status(key,wait_seconds=.2)
            if result['state']!='running':return result
        self.fail('Process failed to finish within test deadline')

    def test_actual_execution_preserves_literal_arguments_and_exit_code(self):
        result=self.jobs.start(sys.executable,['-c','import sys; print(sys.argv[1]); sys.exit(7)','hello ; & $(literal)'])
        final=self.finish(result['id'])
        self.assertEqual(final['state'],'failed');self.assertEqual(final['exit_code'],7)
        self.assertIn('hello ; & $(literal)',final['output'])

    def test_incremental_output_does_not_launch_or_consume_twice(self):
        key=self.start("print('first'); print('second')")
        final=self.finish(key)
        again=self.jobs.status(key)
        self.assertEqual(again['output'],final['output'])
        self.assertEqual(self.jobs.status(key,final['next_cursor'])['output'],'')
        self.assertEqual(len(self.jobs.jobs),1)

    def test_cancel_only_owned_job(self):
        key=self.start('import time; print("started"); time.sleep(30)')
        with self.assertRaises(ValueError):self.jobs.cancel('not-owned')
        self.jobs.cancel(key);final=self.finish(key)
        self.assertEqual(final['state'],'cancelled');self.assertIsNotNone(final['exit_code'])

    def test_timeout_and_turn_cleanup(self):
        key=self.start('import time; time.sleep(30)','1')
        self.assertEqual(self.finish(key)['state'],'timed_out')
        other=self.start('import time; time.sleep(30)')
        self.jobs.close();self.assertEqual(self.jobs.status(other)['state'],'turn_ended')
        with self.assertRaises(InterruptedError):self.start('print(1)')

    def test_stop_event_cancels_process(self):
        key=self.start('import time; time.sleep(30)');self.cancel.set()
        self.assertEqual(self.finish(key)['state'],'cancelled')

    def test_output_is_bounded_and_cursor_reports_truncation(self):
        key=self.start("print('x'*90000)");result=self.finish(key)
        self.assertTrue(result['truncated']);self.assertLessEqual(len(result['output']),12000)
        self.assertLessEqual(len(self.jobs.jobs[key]['text']),self.jobs.RETAIN)

    def test_output_flood_is_stopped(self):
        self.jobs.OUTPUT_LIMIT=16000
        key=self.start("import time; print('x'*40000); time.sleep(30)")
        self.assertEqual(self.finish(key)['state'],'output_limit')

    def test_validation_does_not_start_a_process(self):
        with patch('process_jobs.subprocess.Popen') as launch:
            for args in ({'arguments':'{}'},{'arguments':['bad\x00arg']},{'cwd':'..'}, {'timeout_seconds':'0'}, {'timeout_seconds':'1801'}):
                with self.assertRaises(ValueError):self.jobs.start(sys.executable,**args)
            with self.assertRaises(ValueError):self.jobs.start('build.cmd')
            launch.assert_not_called()

    @unittest.skipUnless(os.name=='nt','Windows process-tree ownership')
    def test_cancellation_stops_descendant_even_after_parent_exits(self):
        # Child holds inherited output pipe and would create a marker if left alive.
        child="import time; from pathlib import Path; time.sleep(2); Path('orphan.txt').write_text('orphan'); time.sleep(20)"
        script='import subprocess, sys, time; time.sleep(.1); subprocess.Popen([sys.executable,"-u","-c",'+repr(child)+']); print("spawned")'
        key=self.start(script)
        deadline=time.monotonic()+5
        while 'spawned' not in self.jobs.status(key)['output'] and time.monotonic()<deadline:time.sleep(.05)
        self.jobs.cancel(key);self.finish(key)
        time.sleep(2.2)
        self.assertFalse((self.root/'orphan.txt').exists())


if __name__=='__main__':unittest.main()
