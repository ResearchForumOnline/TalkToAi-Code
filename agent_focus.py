"""Select Agent Zero's task guidance without changing its tools or permissions."""
from __future__ import annotations

import re


FOCUS_CHOICES = (
    ('auto', 'Auto'),
    ('game', 'Games'),
    ('coding', 'Coding'),
    ('research', 'Research'),
    ('writing', 'Writing'),
    ('shopping', 'Shopping'),
    ('everyday', 'Everyday'),
)
_FOCUS_KEYS = frozenset(key for key, _ in FOCUS_CHOICES)
_FLAGS = re.IGNORECASE
_ACTION = re.compile(
    r'\b(?:add|build|change|check|compile|create|debug|develop|edit|fix|implement|improve|inspect|'
    r'install|integrate|launch|make|modify|open|optimize|patch|refactor|remove|repair|review|run|'
    r'set up|test|update|use|write)\b', _FLAGS,
)
_ISSUE = re.compile(r'\b(?:bug|crash(?:es|ed|ing)?|error|fail(?:s|ed|ing)?|traceback)\b', _FLAGS)
_RESEARCH = re.compile(
    r'\b(?:fact[- ]check|look up|research\s+(?:how|why|what|whether|the|a|an|this|these)|'
    r'(?:conduct|do)\s+(?:some\s+)?research|'
    r'(?:find|check|cite|compare|verify)\s+(?:[\w-]+\s+){0,3}'
    r'(?:primary sources?|official (?:sources?|documentation)|sources?\s+(?:for|on|about)|'
    r'published (?:studies|papers)|'
    r'research papers?|references?\s+(?:for|on|about)|claims?\s+(?:about|that))|'
    r'review\s+(?:(?:the|these|those|available|cited|official|primary)\s+){0,3}sources\b(?!\s+code)|'
    r'literature (?:review|search)|systematic review)\b', _FLAGS,
)
_WRITING = re.compile(
    r'\b(?:compose|draft|edit|polish|proofread|rewrite|revise|summarize|translate|update|write)\s+'
    r'(?:(?:a|an|the|this|that|my|our|short|long|brief|concise|formal|friendly|professional|'
    r'project|existing|new)\s+){0,4}[`"“]?(?:article|blog post|cover letter|draft|email|essay|letter|'
    r'press release|proposal|readme(?:\.md)?|report|resume|résumé|speech|story|video script|'
    r'web copy|website copy)(?![\w-]|\.[\w-])|'
    r'\b(?:proofread this|fix (?:the )?(?:grammar|spelling)|revise (?:this|the) (?:text|paragraph))\b', _FLAGS,
)
_ENGINE = re.compile(
    r'(?<![\w.-])(?:godot|gdscript)(?![\w-]|\.[\w-])|'
    r'\b(?:unity\s+(?:editor|engine|hub|project|scene|prefab|game)|'
    r'(?:in|with|for|using)\s+unity)(?![\w-]|\.[\w-])|\bunity\s+c#(?!\w)|'
    r'(?<![\w.-])(?:project\.godot|[\w-]+(?:\.[\w-]+)*\.(?:gd|tscn|tres|unity|prefab))'
    r'(?![\w-]|\.[\w-])', _FLAGS,
)
_GAME_TASK = re.compile(
    r'\b(?:add|build|create|debug|develop|fix|implement|launch|make|playtest|test|update)\s+'
    r'(?:[\w-]+\s+){0,3}(?:games?(?!\s+(?:night|plan|show|theory)\b)|'
    r'gameplay|gamepad support|player controller|collision shapes?|level editor|'
    r'level generation|tile maps?|tilemaps?|game assets?)\b', _FLAGS,
)
_CODE = re.compile(
    r'\b(?:api endpoint|code|codebase|compile|dependency|javascript|kotlin|python|sql|'
    r'test suite|traceback|typescript|unit tests?)\b|\b(?:c#|c\+\+)(?!\w)|'
    r'(?<![\w.-])[\w-]+(?:\.[\w-]+)*\.(?:c|cc|cpp|cs|go|h|hpp|java|js|jsx|kt|lua|mjs|php|py|rb|rs|sh|sql|swift|ts|tsx)'
    r'(?![\w-]|\.[\w-])', _FLAGS,
)
_CODING_TASK = re.compile(
    r'\b(?:refactor|debug (?:this|it)|run (?:the )?tests|(?:write|code) (?:a |an )?(?:function|class|'
    r'program|shell script)|fix (?:the |this )?(?:bug|stack trace))\b', _FLAGS,
)
_PROJECT = re.compile(
    r'\b(?:this|my|our|the|current|selected)\s+(?:app|build|game|level|project|scene|workspace)\b|'
    r'\b(?:build|run|test|check|map|open)\s+project\b', _FLAGS,
)
_SHOPPING_ACTION = re.compile(
    r'\b(?:compare|find|check|look up|recommend|search(?: for)?|show(?: me)?|what(?: is| are|\'s)|'
    r'which|where can i buy)\b', _FLAGS,
)
_SHOPPING_QUERY = re.compile(
    r'\b(?:compare|find|check|look up|search for|show(?: me)?|what(?: is| are|\'s))\s+'
    r'(?:[\w-]+\s+){0,4}(?:prices?|deals?|discounts?|coupons?|shipping costs?|returns polic(?:y|ies)|'
    r'retailers?|in[- ]stock availability)\b|\bwhere can i buy\b', _FLAGS,
)
_SHOPPING_PRODUCT = re.compile(
    r'\b(?:ssds?|laptops?|notebook pcs?|tablets?|monitors?|headphones?|earbuds?|graphics cards?|gpus?|smartphones?|'
    r'keyboard|printer|camera|television|smart tv|vacuum|shoes|trainers|jacket|coat|backpack|'
    r'microphone|router|desk|office chair|washing machine|dishwasher|coffee maker)\b', _FLAGS,
)
_SHOPPING_TERMS = re.compile(
    r'\b(?:prices?|deals?|discounts?|coupons?|in stock|shipping|delivery|returns?|retailer|'
    r'under budget|budget of|best value|should i buy|for sale|cheapest|affordable)\b|'
    r'[£$€]\s*\d|\b\d[\d,.]*\s*(?:gbp|usd|eur)\b', _FLAGS,
)
_SHOPPING_EXCLUDED = re.compile(
    r'\b(?:checkout|check out (?:my|the) (?:cart|basket)|place (?:my|an|the) order|'
    r'(?:add|put)\b.{0,55}\bto (?:(?:my|the|a|your) )?(?:cart|basket)|'
    r'(?:cancel|confirm|submit|complete) (?:my|the|this) (?:order|purchase)|'
    r'order status|tracking number|delivery status|'
    r'(?:my|your) (?:shipping|delivery|billing) address|payment (?:details|information)|'
    r'(?:credit |debit )?card number|share prices?|stock prices?|bitcoin prices?|'
    r'token prices?|price (?:ceiling|elasticity|floor|index)|unit tests?|'
    r'pricing (?:api|function|module|algorithm))\b|'
    r'\b(?:in|from)\s+(?:this|my|our|the attached)\s+(?:spreadsheet|csv|file)\b|'
    r'(?:^|[.!?;]\s*|\b(?:please|then|and|or|can you|could you|would you)\s+)'
    r'(?:buy|purchase|order|pay for|reserve|book)\b', _FLAGS,
)

_GUIDANCE = {
    'game': (
        'Work as a game developer. Identify the project engine and the affected scenes, scripts, '
        'and assets from project evidence. Use available engine or asset tools where they fit. '
        'Check engine imports or compilation and verify the requested behavior with a playtest or '
        'targeted gameplay test when available. Report what the checks showed.'
    ),
    'coding': (
        'Work as a software developer. Inspect the relevant entry points and existing behavior, '
        'then make a focused change that fits the project. Run the relevant checks through the '
        'normal entry point and report the observed result and any remaining verification gap.'
    ),
    'research': (
        'Work as a researcher. Identify the question and use available sources or supplied '
        'evidence. Prefer primary sources, reconcile disagreements, and attribute material claims. '
        'Distinguish observations, inferences, and unanswered questions.'
    ),
    'writing': (
        'Work as an editor and writer. Identify the intended audience, purpose, and requested '
        'format. Deliver a usable draft or make the requested edits while preserving supplied '
        'facts and voice. Check that the finished text covers each requested point.'
    ),
    'shopping': (
        'Research options from public seller pages. Match the exact product, SKU, condition, and '
        'region. Compare the current total price, delivery, and returns. Cite the exact offer URLs '
        'and when you checked them, and note any unverified costs or stock. Keep addresses and '
        'payment details private. Stop after presenting options for the user to decide.'
    ),
}


def normalize_focus(value: object) -> str:
    """Accept stable saved keys and default to Auto for unknown settings."""
    key = value.strip().lower() if isinstance(value, str) else ''
    return key if key in _FOCUS_KEYS else 'auto'


def _clean_prompt(text: str) -> str:
    return ' '.join(text[:4000].replace('’', "'").split())


def shopping_lookup_request(text: str) -> bool:
    """Recognize public shopping comparisons and leave transactions to the user."""
    return _shopping_lookup(_clean_prompt(text))


def _shopping_lookup(prompt: str) -> bool:
    if _SHOPPING_EXCLUDED.search(prompt) or not _SHOPPING_ACTION.search(prompt):
        return False
    return bool(_SHOPPING_QUERY.search(prompt) or (
        _SHOPPING_PRODUCT.search(prompt) and _SHOPPING_TERMS.search(prompt)
    ))


def _prompt_focus(prompt: str) -> str:
    if _shopping_lookup(prompt):
        return 'shopping'
    if _RESEARCH.search(prompt):
        return 'research'
    if _WRITING.search(prompt):
        return 'writing'
    technical_request = _ACTION.search(prompt) or _ISSUE.search(prompt)
    if _GAME_TASK.search(prompt) or (technical_request and _ENGINE.search(prompt)):
        return 'game'
    if _CODING_TASK.search(prompt) or (technical_request and _CODE.search(prompt)):
        return 'coding'
    return 'everyday'


def _project_task(prompt: str) -> bool:
    return bool(_ACTION.search(prompt) and _PROJECT.search(prompt))


def needs_project_kind(latest_prompt: str) -> bool:
    """Check whether reading the selected project's engine marker could affect Auto."""
    prompt = _clean_prompt(latest_prompt)
    return _project_task(prompt) and _prompt_focus(prompt) == 'everyday'


def resolve_focus(preference: object, latest_prompt: str, *, project_kind: str = '') -> str:
    """Resolve an explicit choice or interpret the user's current task in Auto."""
    selected = normalize_focus(preference)
    if selected != 'auto':
        return selected
    prompt = _clean_prompt(latest_prompt)
    inferred = _prompt_focus(prompt)
    if inferred == 'everyday' and project_kind.strip().lower() in {'godot', 'unity'} and _project_task(prompt):
        return 'game'
    return inferred


def focus_guidance(effective_key: object) -> str:
    """Return the chosen specialty's supplemental instructions."""
    return _GUIDANCE.get(normalize_focus(effective_key), '')
