"""Turn-owned native process sessions. Original TalkToAi implementation.

No shell is inserted. An explicitly requested shell remains possible in Act
mode, with the signed-in user's permissions. Jobs never survive the turn.
"""
import codecs
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid


class ProcessJobs:
    RETAIN = 64000
    OUTPUT_LIMIT = 4 * 1024 * 1024

    def __init__(self, root, cancel=None, emit=None):
        self.root = Path(root).resolve()
        self.cancel_event = cancel or threading.Event()
        self.emit = emit or (lambda kind, value: None)
        self.jobs = {}
        self.lock = threading.RLock()
        self.closed = False

    def start(self, executable, arguments='[]', cwd='', timeout_seconds='300'):
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(executable, str) or not executable.strip() or '\x00' in executable:
            raise ValueError('Provide an executable name/path, not a shell command.')
        if not isinstance(args, list) or len(args) > 128 or any(not isinstance(a, str) or '\x00' in a for a in args):
            raise ValueError('arguments must be a JSON array of at most 128 literal strings.')
        if len(json.dumps([executable, *args], ensure_ascii=False)) > 16000:
            raise ValueError('Process arguments exceed the 16000-character limit.')
        if Path(executable).suffix.lower() in ('.bat', '.cmd'):
            raise ValueError('Batch files require an explicit shell executable and arguments.')
        seconds = int(timeout_seconds)
        if not 1 <= seconds <= 1800:
            raise ValueError('timeout_seconds must be between 1 and 1800.')
        directory = (self.root / cwd).resolve()
        if not directory.is_dir() or (directory != self.root and self.root not in directory.parents):
            raise ValueError('Working directory must be inside this project.')
        with self.lock:
            if self.closed or self.cancel_event.is_set():
                raise InterruptedError('Task stopped; no process started.')
            if len(self.jobs) >= 20 or sum(j['process'].poll() is None for j in self.jobs.values()) >= 3:
                raise ValueError('Job limit reached: at most 3 running and 20 total per turn.')
            process = subprocess.Popen([executable, *args], cwd=directory, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt')
            owner = None
            if os.name == 'nt':
                import win32job
                try:
                    owner = win32job.CreateJobObject(None, '')
                    limits = win32job.QueryInformationJobObject(owner, win32job.JobObjectExtendedLimitInformation)
                    limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    win32job.SetInformationJobObject(owner, win32job.JobObjectExtendedLimitInformation, limits)
                    win32job.AssignProcessToJobObject(owner, int(process._handle))
                except Exception:
                    if owner: owner.Close()
                    if process.poll() is None: process.kill()
                    process.wait(timeout=10);process.stdout.close()
                    raise RuntimeError('Unable to establish ownership of the Windows process tree; job not retained.')
            key = uuid.uuid4().hex[:12]
            job = {'id': key, 'process': process, 'command': [executable, *args], 'cwd': str(directory),
                   'started': time.monotonic(), 'timeout': seconds, 'text': '', 'offset': 0,
                   'total_bytes': 0, 'reason': '', 'owner': owner, 'done': threading.Event(), 'stop_lock': threading.Lock()}
            self.jobs[key] = job
        self._notify(job)
        threading.Thread(target=self._read, args=(job,), daemon=True).start()
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        return self.status(key)

    def _append(self, job, text):
        with self.lock:
            job['text'] += text
            overflow = max(0, len(job['text']) - self.RETAIN)
            if overflow:
                job['text'] = job['text'][overflow:]
                job['offset'] += overflow

    def _read(self, job):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                chunk = job['process'].stdout.read1(4096)
                if not chunk:
                    break
                self._append(job, decoder.decode(chunk))
                job['total_bytes'] += len(chunk)
                if job['total_bytes'] > self.OUTPUT_LIMIT:
                    self._stop(job, 'output_limit')
                    break
            self._append(job, decoder.decode(b'', final=True))
        finally:
            job['process'].stdout.close()
            job['reader_done'] = True

    def _watch(self, job):
        try:
            while True:
                if self.cancel_event.is_set():
                    self._stop(job, 'cancelled')
                elif time.monotonic() - job['started'] >= job['timeout']:
                    self._stop(job, 'timed_out')
                if job['process'].poll() is not None and job.get('reader_done'):
                    break
                self._notify(job)
                time.sleep(.2)
        finally:
            # Descendants must not remain after their parent/session finishes.
            with job['stop_lock']:
                if job['owner']:
                    job['owner'].Close();job['owner'] = None
            job['ended'] = time.monotonic()
            job['done'].set()
            self._notify(job)

    def _stop(self, job, reason):
        with job['stop_lock']:
            process = job['process']
            if job['done'].is_set() or (process.poll() is not None and job.get('reader_done')):
                return
            job['reason'] = reason
            if os.name == 'nt':
                import win32job
                try:
                    if job['owner']: win32job.TerminateJobObject(job['owner'], 1)
                except Exception:
                    job['reason'] = 'cancel_failed'
            else:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL)

    def _get(self, key):
        with self.lock:
            if key not in self.jobs:
                raise ValueError('Unknown job id. Only jobs from the current turn can be inspected or cancelled.')
            return self.jobs[key]

    def status(self, key, cursor=0, wait_seconds=0):
        job = self._get(key)
        cursor, wait_seconds = int(cursor), float(wait_seconds)
        if cursor < 0 or not 0 <= wait_seconds <= 5:
            raise ValueError('cursor must be nonnegative; wait_seconds must be between 0 and 5.')
        if wait_seconds:
            job['done'].wait(wait_seconds)
        with self.lock:
            returncode = job['process'].poll()
            finished = job['done'].is_set()
            state = (job['reason'] or ('completed' if returncode == 0 else 'failed')) if finished else 'running'
            begin = max(cursor, job['offset'])
            end = job['offset'] + len(job['text'])
            if cursor > end:
                raise ValueError('Output cursor exceeds current output. Start at 0.')
            output = job['text'][begin-job['offset']:begin-job['offset']+12000]
            result = {'id': key, 'state': state, 'exit_code': returncode if finished else None,
                    'command': job['command'], 'cwd': job['cwd'], 'seconds': round(job.get('ended',time.monotonic())-job['started'], 1),
                    'output': output, 'next_cursor': begin+len(output), 'truncated': cursor < job['offset'],
                    'has_more': begin+len(output) < end, 'stop_reason': job['reason'],
                    'note': 'Process exit is not proof of test coverage or game playability. Jobs end with this turn.'}
            while len(json.dumps(result, ensure_ascii=False)) > 21000 and result['output']:
                result['output'] = result['output'][:len(result['output'])//2]
                result['next_cursor'] = begin+len(result['output'])
                result['has_more'] = result['next_cursor'] < end
            return result

    def _notify(self, job):
        try:
            # UI gets a bounded tail. Polling for the model uses an independent cursor.
            with self.lock: cursor = max(job['offset'], job['offset']+len(job['text'])-6000)
            self.emit('job', self.status(job['id'], cursor))
        except Exception:
            pass  # UI delivery must not strand an owned process.

    def cancel(self, key):
        job = self._get(key)
        self._stop(job, 'cancelled')
        job['done'].wait(2)
        return self.status(key)

    def running(self):
        with self.lock: return [key for key, job in self.jobs.items() if not job['done'].is_set()]

    def close(self):
        with self.lock:
            self.closed = True
            jobs = list(self.jobs.values())
        for job in jobs:
            self._stop(job, 'turn_ended')
        for job in jobs:
            job['done'].wait(3)
            if not job['done'].is_set():
                raise RuntimeError('A command has not confirmed exit; inspect the Jobs panel before continuing.')
