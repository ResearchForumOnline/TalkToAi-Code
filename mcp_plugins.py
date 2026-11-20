"""User-configured MCP stdio plugins, scoped to the foreground Act turn."""
import collections
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time


PROTOCOL = '2025-11-25'
SUPPORTED_PROTOCOLS = {PROTOCOL, '2025-06-18', '2025-03-26', '2024-11-05'}
MAX_FRAME = 1024 * 1024
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,39}\Z')
ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,99}\Z')
BASE_ENV = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME',
            'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'LANG', 'LC_ALL', 'TMPDIR',
            'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432', 'COMMONPROGRAMFILES',
            'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'USER', 'USERNAME'}


def validate_plugins(raw):
    if not isinstance(raw, dict) or len(raw) > 24:
        raise ValueError('Use a JSON object with up to 24 named MCP plugins.')
    result = {}
    for name, value in raw.items():
        if not NAME.fullmatch(name) or not isinstance(value, dict):
            raise ValueError('Plugin names must start with a letter and use up to 40 letters, digits, hyphens or underscores.')
        if set(value) - {'command', 'args', 'enabled', 'pass_env'}:
            raise ValueError(f'{name} has an unknown plugin setting.')
        command = value.get('command')
        args = value.get('args', [])
        enabled = value.get('enabled', False)
        pass_env = value.get('pass_env', [])
        if not isinstance(command, str) or not command.strip() or len(command) > 1024 or '\x00' in command:
            raise ValueError(f'{name} needs a single executable name or absolute path.')
        if not isinstance(args, list) or len(args) > 64 or any(not isinstance(arg, str) or '\x00' in arg or len(arg) > 4096 for arg in args):
            raise ValueError(f'{name} needs a JSON array of literal arguments.')
        if not isinstance(enabled, bool) or not isinstance(pass_env, list) or len(pass_env) > 20 or any(
                not isinstance(env, str) or not ENV_NAME.fullmatch(env) for env in pass_env):
            raise ValueError(f'{name} needs a boolean enabled flag and a list of environment-variable names.')
        result[name] = {'command': command.strip(), 'args': args, 'enabled': enabled, 'pass_env': pass_env}
    return result


class McpSession:
    def __init__(self, project, spec, cancel):
        self.cancel = cancel
        self.frames = queue.Queue(maxsize=32)
        self.request_id = 0
        self.process = None
        self.owner = None
        self.tools = None
        self.closed = threading.Event()
        command = spec['command']
        candidate = Path(command).expanduser()
        if candidate.is_absolute():
            executable = str(candidate.resolve(strict=True))
        elif re.fullmatch(r'[A-Za-z0-9_.+-]+', command):
            executable = shutil.which(command)
        else:
            raise ValueError('Use a PATH executable name or an absolute path for the plugin.')
        if not executable or Path(executable).suffix.lower() in ('.cmd', '.bat'):
            raise ValueError('Choose an installed native executable such as node, python or uvx.')
        if Path(project).resolve() in Path(executable).resolve().parents and not candidate.is_absolute():
            raise PermissionError('Choose an explicit absolute path for an executable located inside this project.')
        env = {key: value for key, value in os.environ.items()
               if key.upper() in BASE_ENV or key in spec['pass_env']}
        self.process = subprocess.Popen(
            [executable, *spec['args']], cwd=project, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=os.name != 'nt',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            if os.name == 'nt':
                import win32job
                self.owner = win32job.CreateJobObject(None, '')
                limits = win32job.QueryInformationJobObject(self.owner, win32job.JobObjectExtendedLimitInformation)
                limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(self.owner, win32job.JobObjectExtendedLimitInformation, limits)
                win32job.AssignProcessToJobObject(self.owner, int(self.process._handle))
            self.reader = threading.Thread(target=self._receive, daemon=True)
            self.reader.start()
            result = self.request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                                                  'clientInfo': {'name': 'TalkToAi Code', 'version': '1'}})
            if result.get('protocolVersion') not in SUPPORTED_PROTOCOLS or 'tools' not in result.get('capabilities', {}):
                raise ValueError('The server did not negotiate a supported MCP tools protocol.')
            self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        except BaseException:
            self.close()
            raise

    def _offer(self, frame):
        while not self.closed.is_set():
            try:
                self.frames.put(frame, timeout=.1)
                return
            except queue.Full:
                pass

    def _receive(self):
        try:
            with self.process.stdout as stream:
                while not self.closed.is_set():
                    raw = stream.readline(MAX_FRAME + 1)
                    if not raw:
                        break
                    if len(raw) > MAX_FRAME:
                        self._offer(ValueError('MCP message exceeds 1 MiB.'))
                        return
                    frame = json.loads(raw.decode('utf-8'))
                    if not isinstance(frame, dict) or frame.get('jsonrpc') != '2.0':
                        raise ValueError('Plugin stdout must contain one JSON-RPC message per line.')
                    self._offer(frame)
        except (OSError, ValueError, RecursionError) as exc:
            self._offer(ValueError('Invalid MCP stdout. Check that the command launches an MCP stdio server.'))
        else:
            self._offer(EOFError('MCP plugin exited. Check its executable, arguments and environment.'))

    def _send(self, frame):
        data = (json.dumps(frame, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        if len(data) > 65536:
            raise ValueError('MCP request exceeds 64 KiB.')
        if self.cancel.is_set():
            raise InterruptedError('Task stopped.')
        done = queue.Queue(maxsize=1)

        def write():
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except (ValueError, OSError) as exc:
                done.put(exc)
            else:
                done.put(None)

        threading.Thread(target=write, daemon=True).start()
        result = self._wait(done, time.monotonic() + 5)
        if result:
            raise RuntimeError('The MCP server did not accept input.') from result

    def _wait(self, channel, deadline):
        while not self.cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('MCP plugin did not respond in time.')
            try:
                return channel.get(timeout=min(.15, remaining))
            except queue.Empty:
                pass
        raise InterruptedError('Task stopped.')

    def request(self, method, params):
        self.request_id += 1
        request_id = self.request_id
        self._send({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + 30
        while True:
            frame = self._wait(self.frames, deadline)
            if isinstance(frame, Exception):
                raise frame
            if 'method' in frame:
                if frame.get('method') == 'notifications/tools/list_changed':
                    self.tools = None
                if 'id' in frame:
                    # The host grants no sampling, roots, or elicitation to a plugin.
                    self._send({'jsonrpc': '2.0', 'id': frame['id'], 'error': {
                        'code': -32601, 'message': 'Client method unavailable'}})
                continue
            if frame.get('id') != request_id:
                continue
            if 'error' in frame:
                error = frame['error'] if isinstance(frame['error'], dict) else {}
                raise RuntimeError(f"MCP {method} failed (code {error.get('code', 'unknown')}).")
            if not isinstance(frame.get('result'), dict):
                raise ValueError('MCP plugin returned an invalid result.')
            return frame['result']

    def list_tools(self):
        tools = {}
        params = {}
        seen = set()
        for _ in range(20):
            page = self.request('tools/list', params)
            entries = page.get('tools')
            if not isinstance(entries, list):
                raise ValueError('The MCP plugin returned an invalid tool list.')
            for item in entries:
                if not isinstance(item, dict):
                    continue
                name, schema = item.get('name'), item.get('inputSchema')
                if isinstance(name, str) and 0 < len(name) <= 120 and isinstance(schema, dict):
                    tools[name] = item
            cursor = page.get('nextCursor')
            if cursor is None:
                self.tools = tools
                return tools
            if not isinstance(cursor, str) or cursor in seen:
                break
            seen.add(cursor)
            params = {'cursor': cursor}
        raise ValueError('The MCP plugin exceeded the tool pagination limit.')

    def call_tool(self, name, arguments):
        if self.tools is None:
            self.list_tools()
        if name not in self.tools:
            raise ValueError('Use a tool name from this plugin’s tool list.')
        result = self.request('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError'):
            details = bounded_output(result).get('content', '')
            raise RuntimeError('MCP tool reported an error. '+details[:3000])
        return bounded_output(result)

    def close(self):
        self.closed.set()
        process = self.process
        if not process:
            return
        if self.owner:
            self.owner.Close()
            self.owner = None
        elif os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        process.stdin.close()
        if hasattr(self, 'reader'):
            self.reader.join(timeout=.3)
        else:
            process.stdout.close()


def bounded_output(result):
    parts = result.get('content', [])
    parts = parts if isinstance(parts, list) else []
    strings = [str(item.get('text', ''))[:14000] for item in parts[:20]
               if isinstance(item, dict) and item.get('type') == 'text']
    joined = '\n'.join(strings)
    output = {'content': joined[:14000], 'truncated': len(joined) >= 14000 or len(parts) > 20,
              'visual_or_binary_items_omitted': sum(isinstance(item, dict) and item.get('type') != 'text' for item in parts)}
    if 'structuredContent' in result:
        structured = json.dumps(result['structuredContent'], ensure_ascii=False)
        if len(structured) <= 7000:
            output['structured_content'] = result['structuredContent']
        else:
            output['truncated'] = True
    return output


class McpPlugins:
    def __init__(self, project, config, cancel):
        self.project = project
        self.config = {name: spec for name, spec in validate_plugins(config).items() if spec['enabled']}
        self.cancel = cancel
        self.sessions = {}
        self.observed = collections.defaultdict(set)

    def execute(self, action, plugin, tool, arguments):
        if action == 'plugins':
            return {'enabled_plugins': list(self.config)}
        if action not in ('tools', 'call'):
            raise ValueError('Choose plugins, tools or call.')
        if plugin not in self.config:
            raise PermissionError('Choose a named plugin enabled by the user in Integrations.')
        if action == 'call' and tool not in self.observed[plugin]:
            raise PermissionError('List this plugin’s tools and inspect the exact input schema before calling it.')
        try:
            if plugin not in self.sessions:
                self.sessions[plugin] = McpSession(self.project, self.config[plugin], self.cancel)
            session = self.sessions[plugin]
            if action == 'tools':
                entries = session.list_tools()
                # Select a schema by exact tool name if a large catalog needs paging.
                if tool:
                    item = entries.get(tool)
                    if item is None:
                        raise ValueError('Choose a tool from the plugin’s catalog.')
                    schema = json.dumps(item['inputSchema'], ensure_ascii=False)
                    if len(schema) > 16000:
                        raise ValueError('This tool schema exceeds the agent input limit.')
                    self.observed[plugin].add(tool)
                    return {'name': tool, 'description': str(item.get('description', ''))[:2000], 'input_schema': item['inputSchema']}
                return {'tools': [{'name': name, 'description': str(item.get('description', ''))[:160]}
                                  for name, item in list(entries.items())[:65]], 'total': len(entries)}
            payload = json.loads(arguments or '{}')
            if not isinstance(payload, dict):
                raise ValueError('Tool arguments must be a JSON object matching the observed input schema.')
            return session.call_tool(tool, payload)
        except (TimeoutError, InterruptedError, OSError, EOFError):
            session = self.sessions.pop(plugin, None)
            if session:
                session.close()
            self.observed.pop(plugin, None)
            raise

    def close(self):
        for session in self.sessions.values():
            session.close()
        self.sessions.clear()
        self.observed.clear()
