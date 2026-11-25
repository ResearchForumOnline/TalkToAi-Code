"""Read a model's whole-file reply without discarding recoverable work.

No model calls, file writes or permission changes happen here. The caller
still validates the path, writes through the checked file tool and runs the
ordinary checks. This module only turns a reply into a (path, content)
candidate, says whether the content is known to be complete, and joins a
cut-off file to its continuation.
"""
from dataclasses import dataclass
import json
import re


_FENCE = re.compile(r'^[ \t]*(`{3,}|~{3,})[ \t]*([\w.+#-]*)[^\n]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)', re.S | re.M)
_OPEN_FENCE = re.compile(r'\A\s*(?:`{3,}|~{3,})[ \t]*[\w.+#-]*[^\n]*\n')
_NAMED = re.compile(r'^[ \t>*_`#-]*(?:file(?:name)?|path|save (?:as|to))?[ \t:*_`-]*'
                    r'([\w][\w./ -]{0,200}\.[A-Za-z0-9]{1,8})[ \t:*_`]*$', re.I)
_CODE_START = re.compile(r'^\s*(?:import |from \w|def |class |#|"""|\'\'\'|@|<!doctype|<html|<svg|<\?xml|\{|\[|'
                         r'function |const |let |var |//|/\*|#include|package |using |bpy\.)', re.I)
_ESCAPES = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}


@dataclass(frozen=True)
class Draft:
    path: str | None      # None when the reply named no file
    content: str
    complete: bool        # False when the text is known to stop early
    source: str           # how the content was found; for status text and tests


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(pairs) != len(fields):
        raise ValueError('The file response contains a duplicate field.')
    return fields


def _from_object(data, source):
    if not isinstance(data, dict):
        return None
    path, content = data.get('path'), data.get('content')
    if not isinstance(content, str) or (path is not None and not isinstance(path, str)):
        return None
    return Draft(path or None, content, True, source)


def _decode_string(text, start):
    """Decode a JSON string that begins at text[start] == '"'.

    Returns (value, end, closed). A string cut off by the output limit yields
    the characters that did arrive and closed=False; a half-written escape at
    the very end is dropped rather than guessed.
    """
    out = []
    index = start + 1
    size = len(text)
    while index < size:
        char = text[index]
        if char == '"':
            return ''.join(out), index + 1, True
        if char != '\\':
            out.append(char)
            index += 1
            continue
        if index + 1 >= size:
            break
        code = text[index + 1]
        if code == 'u':
            digits = text[index + 2:index + 6]
            if len(digits) < 4 or not re.fullmatch(r'[0-9a-fA-F]{4}', digits):
                break
            value = int(digits, 16)
            index += 6
            if 0xD800 <= value < 0xDC00 and text[index:index + 2] == '\\u':
                low = text[index + 2:index + 6]
                if len(low) == 4 and re.fullmatch(r'[0-9a-fA-F]{4}', low) and 0xDC00 <= int(low, 16) < 0xE000:
                    value = 0x10000 + ((value - 0xD800) << 10) + (int(low, 16) - 0xDC00)
                    index += 6
            if 0xD800 <= value < 0xE000:
                continue  # an unpaired surrogate cannot be written to a UTF-8 file
            out.append(chr(value))
            continue
        out.append(_ESCAPES.get(code, code))
        index += 2
    return ''.join(out), size, False


def _salvage_object(text):
    """Recover path and content from a JSON object that stops before it closes."""
    start = text.find('{')
    if start < 0:
        return None
    fields = {}
    closed = {}
    for key in ('path', 'content'):
        match = re.search(r'"' + key + r'"\s*:\s*"', text[start:])
        if not match:
            continue
        value, _, done = _decode_string(text, start + match.end() - 1)
        fields[key] = value
        closed[key] = done
    if 'content' not in fields:
        return None
    path = fields.get('path') if closed.get('path') else None
    return Draft(path or None, fields['content'], False, 'partial JSON')


def _largest_fence(text):
    blocks = [(len(match.group(3)), match) for match in _FENCE.finditer(text)]
    if not blocks:
        return None
    _, match = max(blocks, key=lambda item: item[0])
    closed = match.end(3) != match.end()  # the closing fence arrived, not the end of the reply
    before = [line for line in text[:match.start()].splitlines() if line.strip()]
    named = _NAMED.match(before[-1]) if before else None
    return match.group(2).lower(), match.group(3), closed, (named.group(1).strip() if named else None)


def strip_fences(text):
    """Remove one wrapping Markdown fence from a reply that is otherwise only code."""
    opened = _OPEN_FENCE.match(text)
    if not opened:
        return text
    lines = text[opened.end():].split('\n')
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and re.fullmatch(r'[ \t]*(?:`{3,}|~{3,})[ \t]*', lines[-1]):
        lines.pop()
    return '\n'.join(lines) + '\n'


def read_draft(reply, *, default_path=None, truncated=False):
    """Find the requested file in a reply. Returns None when nothing usable arrived.

    truncated says the reply stopped at the output limit. A JSON object or a
    fenced block that nevertheless closed is complete; anything else is not.
    """
    if not isinstance(reply, str) or not reply.strip():
        return None
    text = reply.strip()
    # 1. The requested format: one JSON object.
    try:
        found = _from_object(json.loads(text, object_pairs_hook=_unique_fields), 'JSON')
    except ValueError:
        found = None
    if found:
        return found
    # 2. The same object inside a fence or after a sentence of preamble.
    fence = _largest_fence(text)
    candidates = [fence[1]] if fence and fence[0] in ('json', '') else []
    first, last = text.find('{'), text.rfind('}')
    if 0 <= first < last:
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            found = _from_object(json.loads(candidate.strip(), object_pairs_hook=_unique_fields), 'JSON in prose')
        except ValueError:
            continue
        if found:
            return found
    # 3. A JSON object that the output limit cut short. A reply that ended normally
    #    with broken JSON is a different mistake and is reported as such.
    if truncated and re.match(r'\A(?:(?:`{3,}|~{3,})(?:json)?\s*)?\{\s*"(?:path|content)"', text):
        found = _salvage_object(text)
        if found:
            return found
    # 4. A fenced code block, optionally introduced by its filename.
    if fence and fence[1].strip():
        language, body, closed, named = fence
        if language != 'json' or default_path:
            return Draft(named or default_path, body, closed, 'code block')
    # 5. Plain file text for a destination the caller already knows.
    if default_path and _CODE_START.match(text):
        return Draft(default_path, text + ('' if text.endswith('\n') else '\n'), not truncated, 'plain text')
    return None


def read_json_object(reply):
    """Return the JSON object in a reply that may wrap it in a fence or a sentence."""
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError('The reply was empty. Return one JSON object.')
    text = reply.strip()
    candidates = [text]
    fence = _largest_fence(text)
    if fence:
        candidates.append(fence[1].strip())
    first, last = text.find('{'), text.rfind('}')
    if 0 <= first < last:
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    if first >= 0 and last < first:
        raise ValueError('The JSON object was cut off before it closed. Return a shorter complete object.')
    raise ValueError('The reply was not a JSON object. Return only the JSON object, with no commentary.')


def whole_lines(text):
    """Drop a final line that the output limit may have cut in the middle."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if text.endswith('\n'):
        return text
    head, separator, _ = text.rpartition('\n')
    return head + separator


def continuation_request(path, partial, *, structured=True):
    """Ask for the rest of a file, showing only its last lines."""
    lines = whole_lines(partial).split('\n')
    while lines and not lines[-1].strip():
        lines.pop()
    tail = '\n'.join(lines[-14:])
    shape = ('Return one JSON object with path set to ' + json.dumps(path or 'the same path')
             + ' and content set to ONLY the remaining lines.' if structured else
             'Return ONLY the remaining lines as plain text.')
    return ('Your file was cut off by the output limit. ' + str(len(lines)) + ' complete lines are saved. '
            'Continue from the line after this excerpt and write to the end of the file. '
            'Do not repeat lines that are already saved and do not start again from the top. '
            + shape + ' No commentary or Markdown fences.\n\nThe saved file currently ends with:\n' + tail)


def join_continuation(partial, addition):
    """Append a continuation, dropping lines the model repeated from the saved part.

    Returns (text, restarted). restarted is True when the model sent the file
    again from its first line; that later copy then replaces the saved part.
    """
    saved = whole_lines(partial).split('\n')
    if saved and saved[-1] == '':
        saved.pop()
    new = addition.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    while new and not new[0].strip():
        new.pop(0)
    if not new:
        return '\n'.join(saved) + '\n', False
    head = [line.rstrip() for line in saved if line.strip()][:3]
    start = [line.rstrip() for line in new if line.strip()][:3]
    if len(head) == 3 and head == start and len(saved) > 6:
        return '\n'.join(new) + ('' if addition.endswith('\n') else '\n'), True
    limit = min(40, len(saved), len(new))
    overlap = 0
    for size in range(limit, 0, -1):
        if [line.rstrip() for line in saved[-size:]] == [line.rstrip() for line in new[:size]]:
            # One repeated blank or closing bracket is ordinary code, not an echo.
            if size == 1 and len(saved[-1].strip()) < 4:
                continue
            overlap = size
            break
    text = '\n'.join(saved + new[overlap:])
    return text + ('' if text.endswith('\n') else '\n'), False
