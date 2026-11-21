"""Recognize direct Chat actions before choosing a response path."""

import re

from agent_focus import shopping_lookup_request
from project_workers import requests_project_workers
from remote_intent import requests_remote_work


_PREFIX = re.compile(
    r"^(?:(?:hi|hello|hey|sir|talktoai|agent zero|please)[ ,!:.]+|"
    r"(?:can|could|would|will) you\s+|i (?:want|need|would like) you to\s+|"
    r"i(?:'|’)d like you to\s+|(?:first|then|now|next)[ ,]+)+",
    re.I,
)
_ADVICE = re.compile(
    r"^(?:how (?:do|can|should|would|could) (?:i|we)\b|"
    r"(?:show|give|find|tell|teach) (?:me )?(?:how\b|(?:some )?(?:ideas|examples|tips|advice)\b))",
    re.I,
)
_ACTION = re.compile(
    r"^(?:open|launch|start|control|take over|click|scroll|type|save|run|read|inspect|search|look up|"
    r"browse|find|check|use|edit|update|fix|install|download|list|show|summari[sz]e|"
    r"analyse|analyze|review|compare|audit|diagnose|change|move|copy|rename|"
    r"build|test|investigate|connect|export|put|fill|select|press|switch)\b",
    re.I,
)
_TARGET = re.compile(
    r"\b(?:my (?:computer|screen|email|mail|files)|active tab|app(?:lication)? window|window|desktop|"
    r"browser|Chrome|Brave|Edge|Firefox|Notepad|Excel|folder|project|repo(?:sitory)?|"
    r"codebase|documentation|directory|downloads|inbox|Gmail|Blender|Godot|Unity|terminal|"
    r"PowerShell|GitHub|README|PC Pilot|MCP|plugins?|online|the (?:web|internet)|web for|"
    r"file (?:named|called)|(?:my|this|the) (?:local )?(?:files?|app)|unit tests?|test suite)\b|"
    r"https?://|[A-Za-z]:[\\/]|\b[^\s/\\]+\.(?:py|js|tsx?|html|md|json|pdf|docx|csv|blend|gd|cs)\b",
    re.I,
)
_ATTACHED_QUESTION = re.compile(
    r"^(?:summari[sz]e|read|explain|analy[sz]e|review|check)\b", re.I,
)
_ATTACHED_ACTION = re.compile(
    r"\b(?:open .*? (?:in|with)|(?:edit|rename|save|export|move|upload)\b.*?\b(?:as|to|in)|"
    r"(?:and|then) (?:look up|search|check online))\b", re.I,
)
_SCREEN_ACTION = re.compile(
    r"^(?:click\s+(?:on\s+)?\S|scroll\s+(?:down|up|left|right)\b|"
    r"press\s+(?:enter|tab|escape|ctrl\+\S+|alt\+\S+)\b)",
    re.I,
)
_WEB_LOOKUP = re.compile(
    r"^(?:look up\s+\S|(?:search|browse)\s+(?:the\s+)?(?:web|internet)\b|"
    r"(?:find|check|search)\b.{0,200}\b(?:online|github)\b)",
    re.I,
)
_FOLLOWUP = re.compile(
    r"^(?:go back|open (?:it|that|the next one)|(?:enter|type|select|save|close|"
    r"refresh|switch to|rename|run|edit|fix|apply|open)\s+\S)",
    re.I,
)
_DRAW = re.compile(r"^(?:draw|paint|illustrate)\s+(.+)$", re.I)
_MEDIA = re.compile(
    r"^(?:make|create|generate|render|produce|design)\s+"
    r"(?:me\s+)?(?:(?:a|an|the)\s+)?(?:(?:short|small|simple|realistic|digital|animated|vertical|"
    r"horizontal|cartoon|photorealistic|ai|\d{1,2}[- ](?:second|minute)s?)\s+){0,4}"
    r"(image|picture|photo|illustration|logo|poster|icon|video(?: clip)?|clip|animation)\b"
    r"(?:\s*(?:of|about|showing|with|for)\b)?[\s:,-]*(.*)$",
    re.I,
)
_TEXT_FORMAT = re.compile(r"\b(?:in|as)\s+(?:an?\s+)?(?:svg|ascii|mermaid)\b", re.I)
_WRITING_DRAW = re.compile(r"^(?:up\s+|(?:me\s+)?(?:(?:a|my|your)\s+)?(?:conclusion|plan|ascii)\b)", re.I)


def request_body(text):
    return _PREFIX.sub('', text.strip(), count=1).strip()


def wants_browser_lookup(text):
    body = request_body(text)
    return bool(not _ADVICE.match(body) and (_WEB_LOOKUP.match(body) or shopping_lookup_request(body)))


def wants_shopping_lookup(text):
    body = request_body(text)
    return not _ADVICE.match(body) and shopping_lookup_request(body)


def wants_screen_action(text):
    return bool(_SCREEN_ACTION.match(request_body(text)))


def media_request(text):
    body = request_body(text)
    if _TEXT_FORMAT.search(body):
        return None
    if '\n' in body:
        body = ' '.join(body.split())
    match = _MEDIA.match(body)
    if match:
        noun, description = match.groups()
        kind = 'video' if noun.lower() in ('video', 'video clip', 'clip', 'animation') else 'image'
        if description:
            # Preserve useful style qualifiers such as "poster" or "vertical video".
            return kind, body
        return None
    drawing = _DRAW.match(body)
    if drawing and not _WRITING_DRAW.search(drawing[1]):
        return 'image', drawing[1].strip()
    return None


def chat_requests_tools(text, *, attached=False, followup=False, focus=None):
    """Choose tools for clear actions while preserving advice and attachment chat."""
    body = request_body(text)
    if _ADVICE.match(body):
        return False
    if requests_remote_work(body) or requests_project_workers(body, focus=focus):
        return True
    if attached and _ATTACHED_QUESTION.match(body) and not _ATTACHED_ACTION.search(body):
        return False
    return bool(wants_browser_lookup(body) or _SCREEN_ACTION.match(body)
                or (_ACTION.match(body) and _TARGET.search(body))
                or (followup and _FOLLOWUP.match(body)))
