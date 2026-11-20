"""Translate provider wire output into the agent's text, tool and usage events."""
from __future__ import annotations

import hashlib
import http.client
import json
import socket
import threading
from urllib.parse import urlsplit

from api_router import ProviderHTTPError, ProviderPartialResponseError
import providers


MAX_EVENT = 8_000_000


def response_scope(profile, key):
    """Tie encrypted reasoning to the model and API credential that issued it."""
    return hashlib.sha256(json.dumps([profile.base_url, profile.model, key]).encode()).hexdigest()


def _response_input(messages, system_role):
    items = []
    for message in messages:
        role = message['role']
        content = message['content']
        if role == 'tool':
            items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': content or ''})
            continue
        replay = message.get('_openai_response', {})
        calls = message.get('tool_calls', [])
        if replay and replay['content'] == content and replay['call_ids'] == [call['id'] for call in calls]:
            items.extend(replay['output'])
            continue
        if isinstance(content, list):
            content = [
                {'type': 'input_image', 'image_url': part['image_url']['url'], 'detail': part['image_url'].get('detail', 'auto')}
                if part['type'] == 'image_url' else {'type': 'input_text', 'text': part['text']}
                for part in content
            ]
        if content or not calls:
            items.append({'role': system_role if role == 'system' else role, 'content': content or ''})
        for call in calls:
            items.append({'type': 'function_call', 'call_id': call['id'], **call['function']})
    return items


def _response_request(profile, messages, payload):
    request = {
        'model': profile.model,
        'input': _response_input(messages, 'developer' if profile.is_openai else 'system'),
        'store': False,
        'stream': True,
        'max_output_tokens': profile.max_output_tokens,
        'parallel_tool_calls': False,
    }
    tools = payload.get('tools') or []
    if tools:
        # Optional parameters in the app's existing schemas must stay optional.
        request['tools'] = [{**tool['function'], 'type': 'function', 'strict': False} for tool in tools]
    if profile.model.startswith(('gpt-5', 'gpt-6', 'o1', 'o3', 'o4')):
        request['include'] = ['reasoning.encrypted_content']
    reasoning = {}
    if profile.reasoning_effort:
        reasoning['effort'] = profile.reasoning_effort
    if payload.get('think') and 'include' in request:
        reasoning['summary'] = 'auto'
    if reasoning:
        request['reasoning'] = reasoning
    return request


def _wire_events(response):
    """Read SSE frames (including multiline data) and compatible JSON responses."""
    content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
    if content_type == 'application/json':
        body = response.read(MAX_EVENT + 1)
        if len(body) > MAX_EVENT:
            raise RuntimeError('API response exceeded the size limit.')
        yield json.loads(body)
        return
    frame = []
    size = 0
    while True:
        line = response.readline(MAX_EVENT + 1)
        if len(line) > MAX_EVENT:
            raise RuntimeError('API stream event exceeded the size limit.')
        text = line.rstrip(b'\r\n')
        if not text and frame:
            data = b'\n'.join(frame)
            yield None if data.strip() == b'[DONE]' else json.loads(data)
            frame.clear()
            size = 0
        if not line:
            return
        if text.startswith(b'data:'):
            size += len(text)
            if size > MAX_EVENT:
                raise RuntimeError('API stream event exceeded the size limit.')
            frame.append(text[5:].removeprefix(b' '))
        elif text.lstrip().startswith(b'{'):
            yield json.loads(text)


class StreamResult:
    def __init__(self):
        self.started = False
        self.usage = None
        self.reason = None
        self.calls = {}
        self.text = []
        self.replay = None

    def chat_events(self, data, show_reasoning):
        usage = data.get('usage') or (data.get('x_groq') or {}).get('usage')
        if isinstance(usage, dict):
            self.usage = usage
        choice = (data.get('choices') or [{}])[0]
        if choice.get('finish_reason'):
            self.reason = choice['finish_reason']
        delta = choice.get('delta') or choice.get('message') or {}
        content = delta.get('content') or delta.get('refusal')
        if isinstance(content, str) and content:
            self.started = True
            yield {'message': {'content': content}, 'done': False}
        reasoning = delta.get('reasoning_content') or delta.get('reasoning')
        if isinstance(reasoning, str) and reasoning:
            self.started = True
            if show_reasoning:
                yield {'message': {'thinking': reasoning}, 'done': False}
        for position, item in enumerate(delta.get('tool_calls') or []):
            self.started = True
            index = item.get('index', position)
            if type(index) is not int or not 0 <= index < 16:
                raise RuntimeError('Provider returned an invalid tool call index.')
            slot = self.calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
            if item.get('id'):
                slot['id'] = item['id']
            function = item.get('function') or {}
            slot['function']['name'] += function.get('name') or ''
            slot['function']['arguments'] += function.get('arguments') or ''

    def response_events(self, data, show_reasoning, scope):
        event = data.get('type', '')
        if event in ('response.output_text.delta', 'response.refusal.delta'):
            text = data.get('delta')
            if isinstance(text, str) and text:
                self.started = True
                self.text.append(text)
                yield {'message': {'content': text}, 'done': False}
        elif event == 'response.reasoning_summary_text.delta':
            self.started = True
            if show_reasoning and isinstance(data.get('delta'), str):
                yield {'message': {'thinking': data['delta']}, 'done': False}
        elif event.startswith('response.function_call_arguments.') or (
                event in ('response.output_item.added', 'response.output_item.done') and data.get('item', {}).get('type') in ('function_call', 'reasoning', 'message')):
            self.started = True
        elif event in ('response.completed', 'response.incomplete'):
            yield from self.finish_response(data['response'], scope)
        elif data.get('object') == 'response':
            # Also support an endpoint returning one complete JSON body.
            yield from self.finish_response(data, scope)

    def finish_response(self, response, scope):
        self.started = True
        if response.get('status') == 'incomplete':
            reason = (response.get('incomplete_details') or {}).get('reason')
            if reason == 'max_output_tokens':
                self.reason = 'length'
            else:
                raise RuntimeError('OpenAI returned an incomplete response.')
        elif response.get('status') == 'completed':
            self.reason = 'stop'
        else:
            raise RuntimeError('OpenAI did not complete the response.')

        output = response.get('output') or []
        replay = []
        final_text = []
        ids = set()
        for index, item in enumerate(output):
            kind = item.get('type')
            if kind == 'message':
                for part in item.get('content') or []:
                    final_text.append(part.get('text') or part.get('refusal') or '')
                replay.append(item)
            elif kind == 'reasoning' and item.get('encrypted_content'):
                replay.append(item)
            elif kind == 'function_call' and self.reason != 'length':
                call_id = item.get('call_id')
                if item.get('status') not in (None, 'completed') or not isinstance(call_id, str) or not call_id or call_id in ids:
                    raise RuntimeError('OpenAI returned an invalid or incomplete function call.')
                ids.add(call_id)
                self.calls[index] = {'id': call_id, 'type': 'function', 'function': {'name': item['name'], 'arguments': item['arguments']}}
                replay.append(item)

        content = ''.join(final_text)
        if content and not self.text:
            yield {'message': {'content': content}, 'done': False}
        if self.reason != 'length' and replay:
            self.replay = {'scope': scope, 'output': replay, 'content': content,
                           'call_ids': [call['id'] for call in self.calls.values()]}
        if self.calls:
            self.reason = 'tool_calls'
        usage = response.get('usage')
        if isinstance(usage, dict):
            self.usage = dict(usage, prompt_tokens=usage.get('input_tokens'),
                              completion_tokens=usage.get('output_tokens'))


def stream_provider(profile, payload, cancel, convert_messages):
    providers.validate_cost_policy(profile)
    if cancel.is_set():
        raise InterruptedError('Task stopped.')
    key = providers.api_key(profile)
    scope = response_scope(profile, key) if profile.uses_responses else None
    messages = convert_messages(payload.get('messages', []), openai_scope=scope)
    if profile.uses_responses:
        request = _response_request(profile, messages, payload)
    else:
        request = dict(payload, model=profile.model, messages=messages)
        request.pop('think', None)
        request.pop('keep_alive', None)
        providers.configure_request(profile, request, request.pop('options', {}))
    try:
        yield from _stream_request(profile, request, payload.get('think', False), cancel, key, scope)
    except ProviderHTTPError as exc:
        if not profile.uses_responses or exc.status != 400 or exc.code != 'invalid_encrypted_content':
            raise
        revised = [item for item in request['input'] if item.get('type') != 'reasoning']
        if len(revised) == len(request['input']):
            raise
        yield {'_openai_replay_reset': True}
        yield from _stream_request(profile, dict(request, input=revised), payload.get('think', False), cancel, key, scope)


def _stream_request(profile, request, show_reasoning, cancel, key, scope):
    parsed = urlsplit(profile.base_url)
    connection = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    conn = connection(parsed.hostname, parsed.port, timeout=15)
    base = parsed.path.rstrip('/')
    if not base and parsed.hostname != 'api.deepseek.com':
        base = '/v1'
    suffix = '/responses' if profile.uses_responses else '/chat/completions'
    path = base if base.endswith(suffix) else base + suffix
    headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream, application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    result = StreamResult()
    done = threading.Event()
    response = None
    try:
        if cancel.is_set():
            raise InterruptedError('Task stopped.')
        conn.connect()
        sock = conn.sock
        sock.settimeout(900 if parsed.hostname == 'api.x.ai' else 300 if profile.is_openai else 180)

        def watch():
            while not done.wait(.1):
                if cancel.is_set():
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return

        threading.Thread(target=watch, daemon=True).start()
        conn.request('POST', path, body=json.dumps(request).encode(), headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            code = None
            if profile.uses_responses or profile.is_openai:
                try:
                    body = json.loads(response.read(64_000))
                    error = body.get('error') if isinstance(body, dict) else None
                    code = error.get('code') if isinstance(error, dict) else None
                except (ValueError, OSError, http.client.HTTPException):
                    pass
            raise ProviderHTTPError(response.status, response.getheader('Retry-After'), code, response.getheader('X-Request-ID'))

        for data in _wire_events(response):
            if cancel.is_set():
                raise InterruptedError('Task stopped.')
            if data is None:
                if not profile.uses_responses and not result.calls:
                    result.reason = result.reason or 'stop'
                break
            if not isinstance(data, dict):
                raise RuntimeError('API stream returned an invalid event.')
            if data.get('error') or data.get('type') in ('error', 'response.failed'):
                error = data.get('error') or (data.get('response') or {}).get('error') or data
                code = error.get('code') if isinstance(error, dict) else None
                if isinstance(code, str) and code in ('server_error', 'rate_limit_exceeded', 'insufficient_quota'):
                    status = 503 if code == 'server_error' else 429
                    raise ProviderHTTPError(status, response.getheader('Retry-After'), code, response.getheader('X-Request-ID'))
                raise RuntimeError('Provider returned an error while generating the response.')
            if profile.uses_responses:
                yield from result.response_events(data, show_reasoning, scope)
                if result.reason:
                    break
            else:
                yield from result.chat_events(data, show_reasoning)

        if cancel.is_set():
            raise InterruptedError('Task stopped.')
        if not result.reason:
            raise RuntimeError('API stream disconnected before completion.')
        final = {}
        if result.calls and result.reason in ('tool_calls', 'function_call', 'stop'):
            final['tool_calls'] = [result.calls[k] for k in sorted(result.calls)]
        event = {'message': final, 'done': True, 'done_reason': result.reason, 'eval_count': 0, 'eval_duration': 1,
                 'api_usage': result.usage, 'api_provider': profile.label, 'api_model': profile.model}
        if result.replay:
            event['_openai_response'] = result.replay
        yield event
    except Exception as exc:
        if cancel.is_set():
            raise InterruptedError('Task stopped.') from None
        if result.started:
            raise ProviderPartialResponseError() from None
        if isinstance(exc, (ValueError, UnicodeError)):
            raise RuntimeError('Provider returned malformed response data.') from None
        if isinstance(exc, http.client.HTTPException):
            raise OSError('API connection interrupted before output.') from None
        raise
    finally:
        done.set()
        if response is not None:
            response.close()
        conn.close()
