"""Choose local model settings from this computer's hardware.

Pure functions: nothing here measures, downloads or changes anything. The
figures are estimates made from the model's file size, and every plan says so
in plain words, so the user can see why a setting was chosen.

The rules follow what works for running a large model on a small graphics
card: give the model as much context as memory really allows, keep part of it
on the processor when it does not fit, use the sampling values the model's
makers publish, and never cut a slow local reply short with a cloud-sized
time or length limit.
"""
from dataclasses import dataclass, field
import re


GIB = 1024 ** 3
CONTEXT_CHOICES = (8192, 16384, 32768, 65536)
# Options the app may pass to a local Ollama model. Anything else is ignored.
LOCAL_OPTION_KEYS = ('num_thread', 'num_gpu', 'num_batch', 'top_k', 'top_p', 'min_p', 'repeat_penalty', 'presence_penalty')
_OPTION_LIMITS = {'num_thread': (1, 256), 'num_gpu': (0, 999), 'num_batch': (16, 2048), 'top_k': (1, 1000),
                  'top_p': (0.0, 1.0), 'min_p': (0.0, 1.0), 'repeat_penalty': (0.5, 2.0), 'presence_penalty': (-2.0, 2.0)}
# Hosted first-party APIs whose current models all accept far more than the local default.
_LARGE_CONTEXT_HOSTS = ('https://api.openai.com/v1', 'https://api.anthropic.com/v1', 'https://api.x.ai/v1',
                        'https://generativelanguage.googleapis.com/v1beta/openai')


def local_options(performance):
    """The checked local-model options from a performance mapping."""
    supplied = (performance or {}).get('ollama_options')
    checked = {}
    for name in LOCAL_OPTION_KEYS:
        value = supplied.get(name) if isinstance(supplied, dict) else None
        if type(value) not in (int, float):
            continue
        low, high = _OPTION_LIMITS[name]
        if low <= value <= high and (type(value) is int or name not in ('num_thread', 'num_gpu', 'num_batch', 'top_k')):
            checked[name] = value
    return checked


def sampling_preset(model):
    """Published sampling values for a model family, or {} to leave the model's own defaults.

    Ollama's own library models already carry these. Models pulled from a GGUF
    repository often do not, and then the server's generic defaults apply.
    """
    name = (model or '').lower().rsplit('/', 1)[-1]
    if re.search(r'qwen[-_ ]?3\.8', name):
        return {'top_k': 20, 'top_p': 0.95, 'min_p': 0.0, 'repeat_penalty': 1.0, 'presence_penalty': 0.0}
    if re.search(r'qwen[-_ ]?3', name):
        return {'top_k': 20, 'min_p': 0.0, 'repeat_penalty': 1.0}
    return {}


def context_bytes(model_bytes, tokens, kv_cache='f16'):
    """Estimated memory for a context of this many tokens.

    Dense models with grouped-query attention keep roughly 110 to 260 KB per
    token at full precision, rising slowly with size: about 96 KB plus one
    100,000th of the model file. Models with sliding-window or linear-attention
    layers need less, so this errs towards a smaller context. It is an estimate
    for choosing between 8K, 16K, 32K and 64K, not a measurement.
    """
    per_token = min(400_000, 96_000 + model_bytes // 100_000)
    return int(tokens * per_token * {'f16': 1.0, 'q8_0': 0.5, 'q4_0': 0.25}.get(kv_cache, 1.0))


@dataclass(frozen=True)
class Plan:
    num_ctx: int
    placement: str                      # 'gpu', 'split', 'cpu', 'too_large' or 'unknown'
    options: dict = field(default_factory=dict)
    notes: tuple = ()
    model_bytes: int = 0

    def summary(self):
        return ' '.join(self.notes)


def plan(hardware, *, model='', model_bytes=0, configured_ctx=8192, context_limit=0, effort='high'):
    """Recommend a context size and options for one local model on this computer."""
    configured_ctx = configured_ctx if configured_ctx in CONTEXT_CHOICES else 8192
    options = sampling_preset(model)
    notes = []
    if options:
        notes.append('Using the sampling values published for this model family.')
    ram = int((hardware or {}).get('ram_bytes') or 0)
    vram = int((hardware or {}).get('vram_bytes') or 0)
    if not hardware or not ram or model_bytes <= 0:
        notes.append('Memory or model size is unknown, so the context size stays as set.')
        return Plan(configured_ctx, 'unknown', options, tuple(notes), max(0, model_bytes))

    unified = bool(hardware.get('unified_memory'))
    working = int(0.6 * GIB)                       # runner, compute buffers, the desktop's own share
    system_free = max(0, int(ram * 0.7))           # leave room for the system and other apps
    if unified:
        gpu_budget, placement = vram, 'gpu'
        total_budget = vram
    else:
        gpu_budget = int(vram * 0.92)
        fits_gpu = vram >= GIB and model_bytes + context_bytes(model_bytes, 8192) + working <= gpu_budget
        placement = 'gpu' if fits_gpu else 'split' if vram >= 2 * GIB else 'cpu'
        total_budget = gpu_budget if fits_gpu else (gpu_budget if placement == 'split' else 0) + system_free
    if model_bytes + context_bytes(model_bytes, 8192) + working > total_budget:
        notes.append(f'This model needs about {(model_bytes + context_bytes(model_bytes, 8192) + working) / GIB:.0f} GB with an 8K '
                     f'context; about {total_budget / GIB:.0f} GB is usable here. It may not load, or will be very slow. '
                     'A smaller model is the reliable choice.')
        return Plan(8192, 'too_large', options, tuple(notes), model_bytes)

    # Low effort favours speed: stay at the configured size. Otherwise use what memory allows,
    # but a model that is not wholly on the graphics card reads a long prompt slowly.
    ceiling = {'low': configured_ctx, 'max': 65536}.get(effort, 32768)
    if placement != 'gpu':
        ceiling = min(ceiling, 32768)
    if context_limit:
        ceiling = min(ceiling, max(8192, context_limit))
    best = 8192
    for choice in CONTEXT_CHOICES:
        if choice <= ceiling and model_bytes + context_bytes(model_bytes, choice) + working <= total_budget:
            best = choice
    chosen = max(configured_ctx, best) if model_bytes + context_bytes(model_bytes, configured_ctx) + working <= total_budget else best
    where = {'gpu': 'It fits on the graphics card', 'split': 'It is shared between the graphics card and main memory',
             'cpu': 'It runs on the processor'}[placement]
    notes.append(f'{where}. Context {chosen // 1024}K is the largest size estimated to fit in memory'
                 + (' at this Effort level.' if chosen < 65536 and effort != 'max' else '.'))
    if placement != 'gpu':
        notes.append('Replies will be slower than on a larger graphics card; the app waits for them instead of timing out.')
    return Plan(chosen, placement, options, tuple(notes), model_bytes)


def api_context(base_url, configured_ctx, *, effort='high', self_hosted=False):
    """Context the app may plan for with an API model.

    The saved context size is a local-memory setting. Hosted first-party models
    accept far more, so a conversation should not be trimmed to 8K for them.
    Other endpoints, free tiers and self-hosted servers keep the saved size.
    """
    configured_ctx = configured_ctx if configured_ctx in CONTEXT_CHOICES else 8192
    if self_hosted or effort == 'low' or (base_url or '').rstrip('/') not in _LARGE_CONTEXT_HOSTS:
        return configured_ctx
    return max(configured_ctx, 32768 if effort == 'max' else 16384)


def server_environment(hardware, existing=None):
    """Environment for an Ollama server the app starts itself. Never overrides the user's own values.

    Flash attention and an 8-bit context cache roughly halve the memory a
    context needs, which is what lets a larger context fit on a small card.
    """
    existing = existing if existing is not None else {}
    wanted = {'OLLAMA_FLASH_ATTENTION': '1', 'OLLAMA_KV_CACHE_TYPE': 'q8_0'}
    if not hardware or not (hardware.get('vram_bytes') or hardware.get('unified_memory')):
        return {}
    return {name: value for name, value in wanted.items() if not existing.get(name)}


def memory_check(model_bytes, hardware, *, label='This model'):
    """A plain warning when a model is unlikely to load on this computer, or ''."""
    ram = int((hardware or {}).get('ram_bytes') or 0)
    if model_bytes <= 0 or not ram:
        return ''
    vram = int((hardware or {}).get('vram_bytes') or 0)
    usable = vram if hardware.get('unified_memory') else int(vram * 0.92) + int(ram * 0.75)
    needed = model_bytes + int(1.5 * GIB)
    if needed <= usable:
        return ''
    return (f'{label} is {model_bytes / GIB:.1f} GB and needs about {needed / GIB:.0f} GB of free memory. This computer has '
            f'{ram / GIB:.0f} GB of memory' + (f' and {vram / GIB:.0f} GB of graphics memory' if vram >= GIB else '')
            + ', so it is likely to load very slowly or not at all. A smaller model will work better here.')


def suggest_models(hardware):
    """Models from the app's list estimated to load on this computer, in list order.

    With unknown hardware every model is listed; the user decides.
    """
    if not (hardware or {}).get('ram_bytes'):
        return list(LOCAL_MODELS)
    return [item for item in LOCAL_MODELS
            if plan(hardware, model=item['id'], model_bytes=item['bytes'], effort='low').placement != 'too_large']


# Sizes are the published download sizes. "needs" is a plain guide, not a guarantee.
LOCAL_MODELS = (
    {'id': 'qwen3.5:4b', 'title': 'Qwen 3.5 4B', 'bytes': int(3.4 * GIB),
     'description': 'Small, quick and good with tools. Fits a 6 GB graphics card with room for a long context.'},
    {'id': 'hf.co/shafire/Zero-Gemma4-E4B-OpenZero-GGUF', 'title': 'OpenZero Gemma 4 E4B', 'bytes': int(5.46 * GIB),
     'description': 'The OpenZero tune of Gemma 4 E4B. Fits a 6 GB graphics card with a short context, or runs shared with memory.'},
    {'id': 'qwen3.5:9b', 'title': 'Qwen 3.5 9B', 'bytes': int(6.6 * GIB),
     'description': 'Stronger reasoning and code. Shared between a 6 GB card and main memory, or fits an 8 GB card.'},
    {'id': 'qwen3.8:27b', 'title': 'Qwen 3.8 27B', 'bytes': 18 * GIB,
     'description': 'The most capable of these. Needs about 24 GB of memory in total; slow but usable on a small card with enough RAM.'},
)
