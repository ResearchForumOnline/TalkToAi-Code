"""Recognize an explicit request to operate on a remote machine.

This only selects the existing SSH tool route. It does not choose a host,
authenticate, run a command, or turn on a disabled app permission.
"""

import re


_REMOTE_MACHINE = re.compile(
    r'\b(?:vps|remote\s+(?:server|host|machine|box)|'
    r'(?:my|our|the|this)\s+(?:linux\s+)?(?:server|host|vps))\b', re.I)
_REMOTE_ACTION = re.compile(
    r'\b(?:check|inspect|diagnose|troubleshoot|fix|edit|update|upgrade|patch|'
    r'deploy|restart|configure|install|run|read|list|audit|maintain|'
    r'connect|log\s?in|ssh|open|build|create|make|work\s+on|look\s+at)\b', re.I)
_INFORMATION_ONLY = re.compile(
    r'^\s*(?:please\s+)?(?:how|what|why|when|where|who|explain\b|'
    r'describe\b|tell\s+me\s+(?:about|how)\b|show\s+me\s+how\b|'
    r'can\s+you\s+(?:explain|tell\s+me|show\s+me)\s+how\b)', re.I)
_MODEL_SUFFIX = re.compile(r'^\s*(?:model|llm|inference|runtime|route|setting|label|option|backend)\b', re.I)
_LOCAL_PREFIX = re.compile(r'\blocal\s+$', re.I)
_INFERENCE_USE = re.compile(r'\buse\s+(?:my|our|the)?\s*$', re.I)
_REMOTE_DESTINATION = re.compile(r'\b(?:on|inside|at|to)\s+(?:my|our|the)?\s*(?:vps|server|remote\s+host)\b', re.I)


def requests_remote_work(text):
    """True when the user asks to inspect or change a VPS/server itself.

    Inference routing such as ``use my server model`` is deliberately excluded.
    The GUI still applies its existing Act mode and SSH permission rules.
    """
    if not isinstance(text, str) or not text.strip() or _INFORMATION_ONLY.match(text):
        return False
    for clause in re.split(r'[;.!?\r\n]+|\bthen\b', text, flags=re.I):
        for machine in _REMOTE_MACHINE.finditer(clause):
            before = clause[:machine.start()]
            after = clause[machine.end():]
            if _MODEL_SUFFIX.match(after) or _LOCAL_PREFIX.search(before):
                continue
            if _INFERENCE_USE.search(before) and re.match(r'^\s*(?:to|for)\b', after, re.I):
                # "Use my server to make/fix a game" usually chooses the LLM
                # host. Do not reinterpret it as access to server files.
                if not _REMOTE_DESTINATION.search(after):
                    continue
            # Keep the action close to the named machine. A separate task in
            # the same message must not turn a model-route mention into SSH.
            nearby = clause[max(0, machine.start() - 90):min(len(clause), machine.end() + 70)]
            if _REMOTE_ACTION.search(nearby):
                return True
    return False
