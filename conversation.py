"""A single tool-free conversational turn, shared by local and API routes."""
import time

from ollama_retention import keep_alive_for, local_port
from reasoning_stream import ReasoningActivity


CHAT_PROMPT = (
    "You are Agent Zero, the conversational assistant in TalkToAi. "
    "Answer the user directly in their language. Use earlier messages for follow-up questions. "
    "You can help with everyday questions, writing, learning, and ideas. "
    "Treat attachments as reference material. You have no tools on this turn. "
    "Be clear about uncertainty and actions you can actually take."
)


def _conversation_payload(model, history, performance, *, url=None):
    turns = []
    for item in history:
        role = item.get('role')
        content = item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str) or not content.strip():
            continue
        if item.get('source') in ('app', 'chat_error') or item.get('_automation_nudge') or item.get('tool_calls'):
            continue
        if role == 'assistant' and content.startswith(('Task error: ', 'App pause report\n')):
            continue  # Old Studio transcripts saved these as ordinary model replies.
        if role == 'user':
            turns.append([{'role': 'user', 'content': content}])
        elif turns:
            turns[-1].append({'role': 'assistant', 'content': content})
    if not turns or len(turns[-1]) != 1:
        raise ValueError('Enter a message to start this reply.')

    capacity = max(2048, int(performance.get('num_ctx', 8192)))
    output_tokens = min(1536, capacity // 2)
    # Keep complete recent exchanges, with space for the reply and template.
    # The full transcript remains in Studio. Never silently cut an attachment.
    budget = (capacity - output_tokens - 256) * 3 - len(CHAT_PROMPT)
    size = 0
    selected = []
    for turn in reversed(turns):
        cost = sum(len(m['content'].encode('utf-8', errors='replace')) + 32 for m in turn)
        if size + cost > budget:
            if not selected:
                raise ValueError('This message is too long for the selected context. Shorten it, split the attachment, or increase Model context in Settings.')
            break
        selected.append(turn)
        size += cost
    messages = [{'role': 'system', 'content': CHAT_PROMPT}]
    for turn in reversed(selected):
        for message in turn:
            if messages[-1]['role'] == message['role']:
                messages[-1]['content'] += '\n\n' + message['content']
            else:
                messages.append(dict(message))
    # Cold short chats can fit a smaller KV cache. Reuse a suitable warm context
    # because changing num_ctx can make Ollama reload the same model.
    context = min(capacity, 4096) if size + len(CHAT_PROMPT) <= (4096 - output_tokens - 256) * 3 else capacity
    resident = performance.get('resident_context')
    if (url is None or local_port(url) is not None) and type(resident) is int and context <= resident <= capacity:
        context = resident
    return {'model': model, 'messages': messages, 'stream': True,
            'keep_alive': keep_alive_for(url, performance.get('ollama_keep_alive')),
            'think': bool(performance.get('show_thinking')), 'options': {
                'num_ctx': context, 'num_predict': output_tokens, 'temperature': .7}}


def model_metrics(stats, started, first, step):
    """Report local timing and provider-reported usage in Studio's shared format."""
    tokens = stats.get('eval_count')
    tokens = tokens if type(tokens) is int and tokens >= 0 else 0
    nanos = stats.get('eval_duration')
    duration = nanos / 1e9 if type(nanos) in (int, float) and 0 < nanos < float('inf') else 0
    metrics = {'step': step, 'seconds': round(time.monotonic() - started, 2),
               'first_token_seconds': round(first, 2) if first is not None else None,
               'tokens': tokens, 'tokens_per_second': round(tokens / duration, 2) if duration else 0,
               'api_usage': stats.get('api_usage'), 'api_provider': stats.get('api_provider'),
               'api_model': stats.get('api_model')}
    for source, target in (('load_duration', 'load_seconds'), ('prompt_eval_duration', 'prompt_seconds'),
                           ('eval_duration', 'generation_seconds')):
        value = stats.get(source)
        if type(value) in (int, float) and 0 <= value < float('inf'):
            metrics[target] = round(value / 1e9, 3)
    count = stats.get('prompt_eval_count')
    if type(count) is int and count >= 0:
        metrics['prompt_tokens'] = count
    return metrics


def describe_switch(switch):
    if switch['reason'] == 'tool_support':
        return switch['label'] + ' cannot use tools with this model. Checking enabled API alternatives.'
    return (switch['label'] + ' reached a limit or is unavailable. Checking enabled API alternatives. '
            + 'Cooldown ' + str(switch['cooldown_seconds']) + 's.')


def conversational_reply(url, model, history, cancel, emit, performance, stream):
    payload = _conversation_payload(model, history, performance or {}, url=url)
    started = time.monotonic()
    first = None
    chunks = []
    terminal = None
    reasoning = ReasoningActivity(include_excerpt=payload['think'])

    def waiting(silent):
        emit('model_wait', {'conversation': True, 'step': 1, 'elapsed_seconds': round(time.monotonic() - started, 1),
                            'silent_seconds': silent,
                            'phase': 'waiting_for_first_response' if first is None else 'waiting_for_next_output'})

    waiting(0)
    try:
        for event in stream(url, payload, cancel):
            if cancel.is_set():
                raise InterruptedError()
            if event.get('_heartbeat'):
                waiting(event['silent_seconds'])
                continue
            if event.get('_provider_route'):
                emit('api_route', event['_provider_route'])
                continue
            if event.get('_provider_switch'):
                emit('status', describe_switch(event['_provider_switch']))
                continue
            if event.get('error'):
                raise RuntimeError('The model stopped before finishing its reply. Check the model in Models and setup.')
            message = event.get('message', {})
            if message.get('tool_calls'):
                raise RuntimeError('The model tried a tool during an ordinary reply. Choose a chat model or start a Code task for this action.')
            if payload['think']:
                activity = reasoning.feed(message.get('thinking'))
                if activity:
                    emit('reasoning', activity)
            delta = message.get('content')
            if isinstance(delta, str) and delta:
                if first is None:
                    first = time.monotonic() - started
                chunks.append(delta)
                emit('delta', delta)
            if event.get('done'):
                terminal = event
                break
    except InterruptedError:
        emit('status', 'Stopped')
        return
    finally:
        finished = reasoning.finish()
        if finished:
            emit('reasoning', finished)
    if cancel.is_set():
        emit('status', 'Stopped')
        return
    if terminal is None:
        raise RuntimeError('The model disconnected before finishing this reply. You can retry your message.')
    emit('metrics', model_metrics(terminal, started, first, 1))
    answer = ''.join(chunks)
    if not answer.strip():
        if terminal.get('done_reason') == 'length':
            raise RuntimeError('The model reached its output limit before writing a reply. Use a shorter question or increase the output limit in the API vault.')
        raise RuntimeError('The model completed the request without writing a reply. Try another model in Models and setup or your API vault.')
    reply = {'role': 'assistant', 'content': answer}
    if terminal.get('done_reason') == 'length':
        reply['finish_reason'] = 'length'
    emit('message', reply)
    if reply.get('finish_reason'):
        emit('status', 'The response reached the model’s output limit. Send “continue” to pick up here.')
