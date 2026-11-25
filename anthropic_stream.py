"""Speak the Claude Messages API: streamed text, tool use and thinking.

The agent hands every provider the same chat-style messages. This module turns
them into Messages API turns, reads the event stream back into the agent's
events, and keeps each assistant turn's own content blocks so a tool loop can
return them unchanged, as the API requires.

Only the user's own key is used. Error bodies are classified, never displayed:
they can quote the request.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import threading
from urllib.parse import urlsplit

from api_router import ProviderHTTPError, ProviderPartialResponseError
import providers
from provider_stream import _wire_events, cut_off_tool, response_scope


_STOP = {'end_turn': 'stop', 'stop_sequence': 'stop', 'tool_use': 'tool_calls', 'max_tokens': 'length',
         'model_context_window_exceeded': 'length', 'refusal': 'stop', 'pause_turn': 'stop'}
_STREAM_ERRORS = {'overloaded_error': 529, 'rate_limit_error': 429, 'api_error': 500, 'timeout_error': 504}
_OPTIONAL = ('cache_control', 'thinking', 'output_config')
_IMAGE = re.compile(r'data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/=\s]+)\Z')


def _image_block(url):
    match = _IMAGE.match(url) if isinstance(url, str) else None
    if match:
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': match[1], 'data': ''.join(match[2].split())}}
    if isinstance(url, str) and url.startswith('https://'):
        return {'type': 'image', 'source': {'type': 'url', 'url': url}}
    return {'type': 'text', 'text': '[An attached image could not be sent in a supported format.]'}


def _user_blocks(content):
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content.strip() else []
    blocks = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get('type') == 'image_url':
            blocks.append(_image_block((part.get('image_url') or {}).get('url')))
        elif isinstance(part.get('text'), str) and part['text'].strip():
            blocks.append({'type': 'text', 'text': part['text']})
    return blocks


def _arguments(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if isinstance(value, str) and value.strip() else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _own_turn(replay, scope, content, identifiers):
    """Is this the saved, unmodified reply for this assistant turn from the same model and key?"""
    if not isinstance(replay, dict) or replay.get('scope') != scope or not isinstance(replay.get('output'), list) or not replay['output']:
        return False
    if replay.get('call_ids') != identifiers:
        return False
    # Call identifiers are unique to one reply. Without calls, compare the text.
    return bool(identifiers) or (replay.get('content') or '').strip() == (content if isinstance(content, str) else '').strip()


def messages_request(messages, scope):
    """Return (system text, Messages API turns).

    A turn this app received from the same model and key is sent back exactly as
    it arrived, thinking included: the API requires that inside a tool loop. A
    tool call from any other source (another model, a local model, an older
    session) has no such blocks, so it is described in text with its result.
    That is always a valid request, whichever model produced the history.
    """
    system, turns, structured = [], [], set()

    def add(role, blocks, *, first=False):
        blocks = [block for block in blocks if block]
        if not blocks:
            return
        if not turns or turns[-1]['role'] != role:
            turns.append({'role': role, 'content': []})
        content = turns[-1]['content']
        if first:
            # Tool results lead the turn that follows the tool calls.
            position = next((index for index, block in enumerate(content) if block.get('type') != 'tool_result'), len(content))
            content[position:position] = blocks
        else:
            content.extend(blocks)

    for message in messages:
        role, content = message['role'], message.get('content')
        if role == 'system':
            if isinstance(content, str) and content.strip():
                system.append(content)
        elif role == 'tool':
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            identifier = message.get('tool_call_id')
            if identifier in structured:
                add('user', [{'type': 'tool_result', 'tool_use_id': identifier, 'content': text or '(no output)'}], first=True)
            else:
                add('user', [{'type': 'text', 'text': 'Result of that action:\n' + (text or '(no output)')}])
        elif role == 'assistant':
            calls = message.get('tool_calls') or []
            identifiers = [call.get('id') for call in calls]
            replay = message.get('_openai_response')
            if _own_turn(replay, scope, content, identifiers):
                add('assistant', replay['output'])
                structured.update(identifiers)
                continue
            blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) and content.strip() else []
            for call in calls:
                function = call.get('function') or {}
                blocks.append({'type': 'text', 'text': 'Action taken: ' + str(function.get('name')) + ' '
                               + json.dumps(_arguments(function.get('arguments')), ensure_ascii=False)[:4000]})
            add('assistant', blocks)
        else:
            add('user', _user_blocks(content))
    if turns and turns[0]['role'] != 'user':
        turns.insert(0, {'role': 'user', 'content': [{'type': 'text', 'text': '(The earlier part of this conversation is not included.)'}]})
    return '\n\n'.join(system), turns


def build_request(profile, payload, messages, scope):
    system, turns = messages_request(messages, scope)
    request = {'model': profile.model, 'max_tokens': profile.max_output_tokens, 'stream': True, 'messages': turns,
               # The same instructions and tools are sent on every step of a task; reuse them.
               'cache_control': {'type': 'ephemeral'},
               'thinking': {'type': 'adaptive', 'display': 'summarized'} if payload.get('think') else {'type': 'adaptive'}}
    if system:
        request['system'] = system
    tools = payload.get('tools') or []
    if tools:
        request['tools'] = [{'name': tool['function']['name'], 'description': tool['function'].get('description', ''),
                             'input_schema': tool['function'].get('parameters') or {'type': 'object', 'properties': {}}}
                            for tool in tools]
    effort = providers.request_effort(profile, payload.get('options'))
    if effort:
        request['output_config'] = {'effort': effort}
    # Sampling parameters are not part of this API version and are never sent.
    return request


def _classify(body, status):
    """(code, optional request field the error names). The message itself is never kept."""
    error = body.get('error') if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, None
    kind = error.get('type') if isinstance(error.get('type'), str) else None
    message = str(error.get('message') or '').lower()
    if status == 404 or kind == 'not_found_error':
        return 'model_not_found', None
    if status not in (400, 413, 422):
        return kind, None
    if re.search(r'prompt is too long|context (?:window|length)|too many (?:input )?tokens|exceeds? the maximum', message):
        return 'context_length_exceeded', None
    if not re.search(r'tool_use|tool_result|redacted_thinking|signature|thinking block', message):
        for field in _OPTIONAL:
            if field in message or (field == 'output_config' and 'effort' in message):
                return kind, field
    return kind, None


def stream_anthropic(profile, payload, cancel, convert_messages, key):
    scope = response_scope(profile, key)
    messages = convert_messages(payload.get('messages', []), openai_scope=scope)
    request = build_request(profile, payload, messages, scope)
    while True:
        try:
            yield from _stream(profile, request, bool(payload.get('think')), cancel, key, scope)
            return
        except _Rejected as rejected:
            # The request was refused before any output because this model does not
            # accept one optional field. Remove only that field. The model, tools,
            # output limit and cost policy are never changed.
            if rejected.field not in request:
                raise rejected.error from None
            request = {name: value for name, value in request.items() if name != rejected.field}


class _Rejected(Exception):
    def __init__(self, error, field):
        super().__init__('request rejected')
        self.error, self.field = error, field


def _stream(profile, request, show_reasoning, cancel, key, scope):
    parsed = urlsplit(profile.base_url)
    connection = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    conn = connection(parsed.hostname, parsed.port, timeout=15)
    headers = {'content-type': 'application/json', 'accept': 'text/event-stream', 'x-api-key': key,
               'anthropic-version': providers.ANTHROPIC_VERSION}
    blocks, order = {}, []
    usage, stop, started, text = {}, None, False, []
    done = threading.Event()
    response = None
    try:
        if cancel.is_set():
            raise InterruptedError('Task stopped.')
        conn.connect()
        sock = conn.sock
        sock.settimeout(900)  # silence between events; the API sends keep-alive pings while it thinks

        def watch():
            while not done.wait(.1):
                if cancel.is_set():
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return

        threading.Thread(target=watch, daemon=True).start()
        conn.request('POST', parsed.path.rstrip('/') + '/messages', body=json.dumps(request).encode(), headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            try:
                body = json.loads(response.read(64_000))
            except (ValueError, OSError, http.client.HTTPException):
                body = {}
            code, field = _classify(body, response.status)
            error = ProviderHTTPError(response.status, response.getheader('retry-after'), code, response.getheader('request-id'))
            if response.status == 400 and field:
                raise _Rejected(error, field)
            raise error

        for data in _wire_events(response):
            if cancel.is_set():
                raise InterruptedError('Task stopped.')
            if not isinstance(data, dict):
                if data is None:
                    break
                raise RuntimeError('API stream returned an invalid event.')
            kind = data.get('type')
            if kind == 'error':
                error = data.get('error') if isinstance(data.get('error'), dict) else {}
                status = _STREAM_ERRORS.get(error.get('type'))
                if status:
                    raise ProviderHTTPError(status, response.getheader('retry-after'), error.get('type'), response.getheader('request-id'))
                raise RuntimeError('Provider returned an error while generating the response.')
            if kind == 'message_start':
                usage.update((data.get('message') or {}).get('usage') or {})
            elif kind == 'content_block_start':
                index = data.get('index')
                if type(index) is not int or not 0 <= index < 64 or index in blocks:
                    raise RuntimeError('Provider returned an invalid content block index.')
                block = dict(data.get('content_block') or {})
                block['_json'] = ''
                block['_closed'] = False
                blocks[index] = block
                order.append(index)
                started = started or block.get('type') in ('tool_use', 'thinking', 'redacted_thinking')
                if block.get('type') == 'text' and block.get('text'):
                    started = True
                    text.append(block['text'])
                    yield {'message': {'content': block['text']}, 'done': False}
            elif kind == 'content_block_delta':
                block = blocks.get(data.get('index'))
                delta = data.get('delta') or {}
                if block is None:
                    raise RuntimeError('Provider sent content for an unknown block.')
                variety = delta.get('type')
                if variety == 'text_delta' and isinstance(delta.get('text'), str):
                    block['text'] = block.get('text', '') + delta['text']
                    if delta['text']:
                        started = True
                        text.append(delta['text'])
                        yield {'message': {'content': delta['text']}, 'done': False}
                elif variety == 'input_json_delta' and isinstance(delta.get('partial_json'), str):
                    started = True
                    block['_json'] += delta['partial_json']
                elif variety == 'thinking_delta' and isinstance(delta.get('thinking'), str):
                    started = True
                    block['thinking'] = block.get('thinking', '') + delta['thinking']
                    if show_reasoning and delta['thinking']:
                        yield {'message': {'thinking': delta['thinking']}, 'done': False}
                elif variety == 'signature_delta' and isinstance(delta.get('signature'), str):
                    block['signature'] = block.get('signature', '') + delta['signature']
            elif kind == 'content_block_stop':
                block = blocks.get(data.get('index'))
                if block is not None:
                    block['_closed'] = True
            elif kind == 'message_delta':
                stop = (data.get('delta') or {}).get('stop_reason') or stop
                usage.update(data.get('usage') or {})
            elif kind == 'message_stop':
                break

        if cancel.is_set():
            raise InterruptedError('Task stopped.')
        reason = _STOP.get(stop)
        if not reason:
            raise RuntimeError('API stream disconnected before completion.')
        calls, replay, cut_off = [], [], None
        for index in order:
            block = blocks[index]
            variety = block.get('type')
            if variety == 'tool_use':
                if reason == 'length':
                    cut_off = cut_off or cut_off_tool(block.get('name'), block['_json'])
                    continue
                try:
                    arguments = json.loads(block['_json']) if block['_json'].strip() else block.get('input') or {}
                except ValueError:
                    raise RuntimeError('Provider returned an invalid or incomplete tool call.') from None
                if (not block['_closed'] or not isinstance(arguments, dict) or not isinstance(block.get('id'), str)
                        or not block['id'] or not isinstance(block.get('name'), str)
                        or any(block['id'] == call['id'] for call in calls)):
                    raise RuntimeError('Provider returned an invalid or incomplete tool call.')
                calls.append({'id': block['id'], 'type': 'function',
                              'function': {'name': block['name'], 'arguments': json.dumps(arguments, ensure_ascii=False)}})
                replay.append({'type': 'tool_use', 'id': block['id'], 'name': block['name'], 'input': arguments})
            elif variety == 'text':
                if block.get('text'):
                    replay.append({'type': 'text', 'text': block['text']})
            elif variety in ('thinking', 'redacted_thinking'):
                replay.append({name: value for name, value in block.items() if not name.startswith('_')})
        content = ''.join(text)
        if stop == 'refusal' and not content and not calls:
            content = 'The model declined to answer this request.'
            yield {'message': {'content': content}, 'done': False}
        if calls:
            reason = 'tool_calls'
        cached = sum(usage.get(name) or 0 for name in ('cache_read_input_tokens', 'cache_creation_input_tokens')
                     if type(usage.get(name)) is int)
        prompt = (usage.get('input_tokens') if type(usage.get('input_tokens')) is int else 0) + cached
        completion = usage.get('output_tokens') if type(usage.get('output_tokens')) is int else 0
        event = {'message': {'tool_calls': calls} if calls else {}, 'done': True, 'done_reason': reason,
                 'eval_count': 0, 'eval_duration': 1, 'api_provider': profile.label, 'api_model': profile.model,
                 'api_usage': {'prompt_tokens': prompt, 'completion_tokens': completion, 'total_tokens': prompt + completion,
                               'cached_tokens': usage.get('cache_read_input_tokens') or 0} if usage else None}
        if cut_off:
            event['_cut_off_tool'] = cut_off
        if reason != 'length' and replay:
            event['_openai_response'] = {'scope': scope, 'output': replay, 'content': content,
                                         'call_ids': [call['id'] for call in calls]}
        yield event
    except _Rejected:
        raise
    except Exception as exc:
        if cancel.is_set():
            raise InterruptedError('Task stopped.') from None
        if started:
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
