"""One Effort choice per run: Low is fastest, High is the default, Max spends the most.

A profile only scales budgets the controller already enforces: model steps,
output tokens, whole-file draft attempts, automatic continuation and command
time. It never changes permissions, tool access or the selected model.
"""
from dataclasses import dataclass


LEVELS = ('low', 'high', 'max')
DEFAULT_EFFORT = 'high'


@dataclass(frozen=True)
class EffortProfile:
    name: str
    label: str
    steps: int                 # model/tool cycles in one pass when the caller does not choose
    extra_passes: int          # further passes granted automatically while a pass makes progress
    output_tokens: int         # ordinary response budget
    output_ceiling: int        # largest budget after a confirmed length cutoff
    draft_tokens: int          # first budget for a whole-file response
    draft_ceiling: int
    draft_attempts: int        # complete attempts at one requested file
    draft_continuations: int   # "carry on from this line" requests after a length cutoff
    spec_attempts: int         # scene-description attempts before the built-in generator
    text_continuations: int    # automatic continuations of a cut-off reply
    command_seconds: int       # default limit for one project command
    reasoning: str             # reasoning hint for API models that accept one; '' keeps their default
    local_thinking: bool | None  # None follows the user's "show model activity" choice
    summary: str
    chat_tokens: int = 1536    # one Chat reply
    chat_continuations: int = 0  # times a Chat reply cut off by the length limit is carried on automatically


# Used when a caller passes no effort. It reproduces the limits of releases before
# 9.1.0, so integrations and saved automation keep their earlier behaviour.
STANDARD = EffortProfile(
    'standard', 'Standard', 16, 0, 1536, 4096, 3072, 4096, 2, 0, 0, 2, 180, '', None,
    'Earlier fixed limits.')

PROFILES = {
    'low': EffortProfile(
        'low', 'Low · fastest', 16, 1, 1536, 4096, 4096, 8192, 2, 2, 1, 2, 180, 'low', False,
        'Short answers and small budgets. Quickest on slow hardware; may need a second request.', 1536, 1),
    'high': EffortProfile(
        'high', 'High · default', 32, 2, 2048, 8192, 8192, 16384, 4, 4, 2, 4, 600, '', None,
        'Balanced budgets. Continues by itself while it is making progress.', 3072, 3),
    'max': EffortProfile(
        'max', 'Max · best result', 64, 4, 4096, 16384, 16384, 32768, 6, 8, 2, 8, 1800, 'high', True,
        'Largest budgets and most retries. Slowest, and uses the most tokens on paid APIs.', 6144, 6),
}


def normalize_effort(value):
    """Return a known level name, or None when the caller did not choose one."""
    if isinstance(value, EffortProfile):
        return value.name if value.name in PROFILES else None
    if isinstance(value, str) and value.strip().lower() in PROFILES:
        return value.strip().lower()
    return None


def effort_profile(value=None):
    name = normalize_effort(value)
    return PROFILES[name] if name else STANDARD


def effort_from(performance):
    """Read the level from a run's performance settings."""
    return effort_profile((performance or {}).get('effort'))


def step_budget(profile, requested=None):
    """A caller-selected step count wins. Otherwise use the level's own."""
    try:
        requested = int(requested) if requested is not None else None
    except (TypeError, ValueError):
        requested = None
    return max(1, min(64, requested if requested else profile.steps))


def command_seconds(profile, command=''):
    """Installs and builds routinely outlast a short limit. Give them room, bounded."""
    limit = profile.command_seconds
    text = command.lower() if isinstance(command, str) else ''
    slow = ('pip install', 'pip3 install', '-m pip', 'npm install', 'npm ci', 'pnpm install', 'yarn install',
            'winget ', 'brew install', 'apt-get ', 'apt install', 'cargo build', 'cargo install', 'cmake --build',
            'make ', 'ninja', 'dotnet build', 'dotnet restore', 'gradle', 'mvn ', 'conda install', 'choco install',
            'git clone', 'playwright install')
    if any(marker in text for marker in slow) or text.strip() in ('make', 'ninja'):
        limit = max(limit, 900)
    return max(30, min(1800, limit))


def reasoning_for(profile, configured=''):
    """An explicit reasoning setting on an API profile always wins."""
    return configured or profile.reasoning


def describe(profile):
    return (f'{profile.label}: {profile.summary} Up to {profile.steps} steps a pass'
            + (f', {profile.extra_passes} automatic extra pass' + ('es' if profile.extra_passes != 1 else '')
               if profile.extra_passes else '')
            + f', {profile.draft_attempts} attempts at a requested file.')
