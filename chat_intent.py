"""Recognize direct Chat actions before choosing a response path."""

import re

from agent_focus import shopping_lookup_request
from project_workers import requests_project_workers
from remote_intent import requests_remote_work
from task_navigation import public_current_query, public_freshness_request, public_search_allowed


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
_CONTINUE = re.compile(r"^(?:try (?:again|harder)|carry on|keep (?:going|trying)|continue(?: the (?:task|work))?|"
                       r"resume(?: the (?:task|work))?)[.! ]*$|\b(?:please|come on(?: sir)?|sir)[ ,!.]+try (?:again|harder)[.! ]*$", re.I)
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
_CREATE = re.compile(r"^(?:build|make|create|generate|design|write|code)\s+", re.I)
_PROJECT_ARTIFACT = re.compile(
    r"\b(?:html|javascript|web ?page|website|web app|browser game|playable game|"
    r"video game|interactive (?:art|demo|game|animation|visuali[sz]ation)|(?:python|shell) script|"
    r"generative art|software project|code project|a program|a script)\b|"
    r"\b[\w.-]+\.(?:html?|py|js|tsx?|css|gd)\b", re.I)
_CREATIVE_CHOICE = re.compile(
    r"^(?:build|make|create|code)\s+(?:me\s+)?(?:something|soemthing)\s+"
    r"(?:impressive|of your (?:own )?cho(?:ice|osing)|to impress me|cool|interactive)\b", re.I)
_PUBLIC_SEARCH = re.compile(
    r'^(?:look up\s+|(?:search|browse)\s+(?:the )?(?:web|internet|online)\s+(?:(?:for|about)\s+)?|'
    r'(?:search|check) online for\s+|search for\s+|fact[- ]check\s+|'
    r'(?:find|cite) (?:some |official |primary )?sources (?:for|on|about)\s+)(.+?)\s*[?!.]?$', re.I)
_PUBLIC_QUESTION = re.compile(r"^(?:what(?:['’]s|\s+(?:is|are|was|were|changed|happened|will))|"
                              r"who|when|which|where|how (?:much|many)|is|are|has|have|did)\b", re.I)
_PERSONAL_CONTEXT = re.compile(
    r"\b(?:my|our|mine|your|me|we|i|you|here|above|attached|uploaded|file|document|spreadsheet|"
    r"workspace|project|repository|codebase|logs?|email|address|phone|passport|medical record|account|"
    r"access key|auth|authorization|bearer|secret|credential|confidential|localhost|internal|intranet)\b|"
    r"(?:[/\\](?:Users|home|etc|tmp)|[\r\n`\\@]|://|\b[A-Za-z0-9_-]{32,}\b)", re.I)
_COMPOUND_SEARCH = re.compile(r"[;]|\b(?:and|then)\s+(?:open|run|save|edit|write|upload|build|create|install|download|send)\b", re.I)
_VIDEO_GAME = re.compile(r'^(?:make|create|generate|produce|design)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:small |simple |short )?video game\b', re.I)
_MAKE_GAME = re.compile(r'^(?:make|create|build|code|design)\s+(?:me\s+)?(?:an?\s+)?'
                        r'(?:(?:small|tiny|simple|playable|2d|3d|fun|little|browser|arcade|video)\s+)*'
                        r'game\b(?!\s+(?:night|plan|story|idea|outline|show|theory)\b)', re.I)


def chat_web_query(text, *, attached=False, document_history=False):
    """Choose a short public lookup from this typed question, without chat history or file data."""
    body = request_body(text)
    if attached or not public_search_allowed(body) or len(body) > 240 or _COMPOUND_SEARCH.search(body):
        return None
    explicit = _PUBLIC_SEARCH.fullmatch(body)
    topic = (explicit[1] if explicit else body).strip().rstrip('?! .')
    if not topic or _PERSONAL_CONTEXT.search(topic) or wants_shopping_lookup(body):
        return None
    if explicit:
        return public_current_query(topic) or topic
    if document_history or not _PUBLIC_QUESTION.match(body):
        return None
    if public_freshness_request(body) or re.search(r'\b(?:weather forecast|forecast in|weather in|breaking news)\b', body, re.I):
        return public_current_query(body) or topic
    return None
_EXPLAIN_CREATION = re.compile(
    r"\b(?:ideas?|examples? of|prompts?|instructions?|tutorials?|suggestions?|a plan|"
    r"(?:a |an |the )?(?:poem|email|essay|letter|story|article|report|list|outline)|"
    r"without (?:creating|saving|writing|editing)|(?:don['’]t|do not|never)\s+(?:write|create|save|change|edit)|"
    r"(?:only|just) (?:explain|show|describe)|(?:in (?:the )?chat|as (?:text|markdown) only))\b", re.I)


def request_body(text):
    return _PREFIX.sub('', text.strip(), count=1).strip()


def requests_project_creation(text):
    """Recognize requests for an actual local deliverable, including an open-ended creative task."""
    body = request_body(text)
    if (not _CREATE.match(body) or _EXPLAIN_CREATION.search(body)
            or (_MEDIA.match(body) and not _VIDEO_GAME.match(body)) or requests_remote_work(body)
            or re.match(r"^(?:create|build|make)\s+(?:me\s+)?(?:an?\s+)?(?:account|agent|specialist)\b", body, re.I)):
        return False
    return bool(_CREATIVE_CHOICE.match(body) or _MAKE_GAME.match(body) or _PROJECT_ARTIFACT.search(body))


def requests_tool_continuation(text):
    return bool(_CONTINUE.search(request_body(text)))


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
    if _TEXT_FORMAT.search(body) or _VIDEO_GAME.match(body):
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
    if _PUBLIC_SEARCH.fullmatch(body) and not public_search_allowed(body) and not _COMPOUND_SEARCH.search(body):
        return False
    if requests_remote_work(body) or requests_project_workers(body, focus=focus):
        return True
    if attached and _ATTACHED_QUESTION.match(body) and not _ATTACHED_ACTION.search(body):
        return False
    return bool(requests_project_creation(body) or wants_browser_lookup(body) or _SCREEN_ACTION.match(body)
                or (_ACTION.match(body) and _TARGET.search(body))
                or (followup and (_FOLLOWUP.match(body) or requests_tool_continuation(body))))
