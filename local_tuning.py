"""Measured, reversible local Ollama retention tuning for a single installed model."""

from dataclasses import dataclass
from enum import Enum
import math
import re

from model_library import DownloadCancelled
from ollama_retention import DEFAULT_KEEP_ALIVE, normalize_keep_alive
from project_workers import verify_local_model
from routing import LOCAL_PORT, choose_route, matching_model, require_local_runtime
from runtime_diagnostics import measure_local_reply, running_probe


class Phase(str, Enum):
    ENTERING = 'entering'
    BOUNDARY = 'boundary'
    EMERGING = 'emerging'


@dataclass(frozen=True)
class RetentionTrial:
    model: str
    prior: str
    phase: Phase
    detail: str
    candidate: str | None = None
    digest: str | None = None


def _seconds(value):
    return value if type(value) in (int, float) and 0 <= value and math.isfinite(value) else None


def _correct(observation):
    # The known-answer test reports exact formatting separately. For retention
    # tuning, the exact digit and the same arithmetic equation both qualify.
    return (observation.get('outcome') in ('verified', 'different')
            and isinstance(observation.get('preview'), str)
            and re.fullmatch(r'(?:2\s*\+\s*3\s*=\s*)?5[.!]?', observation['preview'].strip()) is not None)


def measure_retention(config, route, cancel, emit):
    """Run two matched checks without unloading a model or changing stored settings."""
    prior = normalize_keep_alive(config.get('ollama_keep_alive', DEFAULT_KEEP_ALIVE))
    if prior in ('4h', '24h', 'until_released'):
        return RetentionTrial('', prior, Phase.BOUNDARY, 'Your current model retention is already four hours or longer.')
    if cancel.is_set():
        raise DownloadCancelled('Local model tuning stopped.')
    require_local_runtime(cancel)
    route = route if route in ('local', 'local_large') else 'auto'
    chosen = choose_route(config, route, include_server=False)
    model = chosen['model']
    verify_local_model(chosen['url'], model)
    state = running_probe(LOCAL_PORT)
    if state['state'] != 'reachable':
        return RetentionTrial(model, prior, Phase.BOUNDARY, 'Ollama could not report which models are in memory.')
    if any(not matching_model([item['name']], model) for item in state['models']):
        return RetentionTrial(model, prior, Phase.BOUNDARY, 'Another model is loaded. Select that model or finish using it before tuning.')
    if state['models']:
        return RetentionTrial(model, prior, Phase.BOUNDARY, 'This model is already loaded. Tune before the first chat after it next leaves memory to measure the loading cost.')

    emit(Phase.ENTERING, f'Tuning {model}. Running the first of two local replies.')
    kwargs = {'keep_alive': prior, 'num_ctx': config.get('num_ctx', 8192)}
    first = measure_local_reply(model, cancel, lambda *_: None, **kwargs)
    if cancel.is_set():
        raise DownloadCancelled('Local model tuning stopped.')
    if not first.get('digest') or not _correct(first):
        return RetentionTrial(model, prior, Phase.BOUNDARY, 'The first check could not verify the expected answer and exact installed weights.')
    emit(Phase.ENTERING, f'Tuning {model}. Running the second reply with the model in memory.')
    second = measure_local_reply(model, cancel, lambda *_: None, **kwargs)
    if cancel.is_set():
        raise DownloadCancelled('Local model tuning stopped.')
    first_load, second_load = (_seconds(item.get('load_seconds')) for item in (first, second))
    digest = first['digest']
    matches = (digest == second.get('digest') and first.get('num_ctx') == second.get('num_ctx')
               and first.get('was_loaded') is False and second.get('was_loaded') is True)
    if not matches or not _correct(second) or first_load is None or second_load is None:
        return RetentionTrial(model, prior, Phase.BOUNDARY, 'Both checks need the same verified model and context, a correct reply and readable load timing.')
    detail = f'{model} took {first_load:.2f}s to load for the first reply and {second_load:.2f}s for the warm reply.'
    candidate = '4h' if first_load - second_load >= 1 and first_load >= 2 * second_load else None
    return RetentionTrial(model, prior, Phase.BOUNDARY, detail, candidate, digest)


def apply_retention(trial, preferences, cancel):
    """Apply a checked candidate once through the existing preference transaction."""
    if trial.phase != Phase.BOUNDARY or trial.candidate != '4h' or not trial.digest:
        return trial, None
    with preferences.lock:
        if cancel.is_set():
            raise DownloadCancelled('Local model tuning stopped.')
        current = preferences.config.get('ollama_keep_alive', DEFAULT_KEEP_ALIVE)
        if current != trial.prior:
            return RetentionTrial(trial.model, trial.prior, Phase.BOUNDARY,
                                  trial.detail + ' The retention setting changed during tuning.'), None
        result = preferences.change({'ollama_keep_alive': trial.candidate}, origin='local-tuning')
    detail = trial.detail + ' TalkToAi now requests four hours in memory after each reply.'
    return RetentionTrial(trial.model, trial.prior, Phase.EMERGING, detail, trial.candidate, trial.digest), result


def undo_retention(preferences):
    with preferences.lock:
        history = preferences.history(50)
        undone = {entry.get('rollback_of') for entry in history}
        latest = next((entry for entry in reversed(history)
                       if entry.get('origin') == 'local-tuning' and entry['id'] not in undone), None)
        if latest is None:
            raise ValueError('There is no recent applied local tuning change to undo.')
        return preferences.rollback(latest['id'])
