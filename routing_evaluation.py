"""Read-only, bounded audit of labelled route decisions on one fixed corpus.

This evaluates a declared event such as "review required under rubric v1".
It cannot decide whether an action is authorized, safe, or morally good.
"""

import hashlib
import json
import math

from research_journal import _evidence_path, _journal, _text


MAX_INPUT_BYTES = 1024 * 1024
MAX_CASES = 1000
MAX_FAMILIES = 16
ROUTES = {'allow', 'review', 'defer'}
INPUT_KEYS = {'schema', 'dataset', 'split', 'event_definition', 'rubric_version',
              'baseline_version', 'candidate_version', 'cases'}
CASE_KEYS = {'id', 'family', 'actual_review_required', 'baseline', 'candidate'}


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON field: {key[:80]}')
        result[key] = value
    return result


def _read_input(project, relative_path):
    root, _ = _journal(project)
    relative, path = _evidence_path(root, relative_path)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Routing evaluation must stay inside the project without links.')
    before = path.stat()
    if not path.is_file() or before.st_size > MAX_INPUT_BYTES:
        raise ValueError('Routing evaluation must be a project file of at most 1 MiB.')
    with path.open('rb') as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    after = path.stat()
    _evidence_path(root, relative.as_posix())
    signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if len(data) > MAX_INPUT_BYTES or signature(before) != signature(after) or len(data) != after.st_size:
        raise ValueError('Routing evaluation changed while being read or exceeds 1 MiB.')
    def reject_nonfinite(value):
        raise ValueError(f'Nonfinite JSON number: {value}.')
    try:
        document = json.loads(data.decode('utf-8'), object_pairs_hook=_no_duplicate_keys,
                              parse_constant=reject_nonfinite)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Routing evaluation must be valid UTF-8 JSON.') from exc
    return relative.as_posix(), hashlib.sha256(data).hexdigest(), len(data), document


def _prediction(value):
    if not isinstance(value, dict) or set(value) not in ({'route'}, {'route', 'predicted_review_probability'}):
        raise ValueError('Each prediction needs a route and optional predicted_review_probability.')
    route = value['route']
    if not isinstance(route, str) or route not in ROUTES:
        raise ValueError('Route must be allow, review, or defer.')
    probability = value.get('predicted_review_probability')
    if 'predicted_review_probability' in value:
        if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError('Predicted review probability must be a finite number from 0 to 1.')
    return route, probability


def _validated(document):
    if not isinstance(document, dict) or set(document) != INPUT_KEYS or document.get('schema') != 'talktoai.routing-evaluation.v1':
        raise ValueError('Unsupported routing evaluation schema or fields.')
    for field in ('dataset', 'event_definition', 'rubric_version', 'baseline_version', 'candidate_version'):
        _text(document[field], field, 240 if field == 'event_definition' else 120, True)
    if document['split'] not in ('held_out', 'development', 'training', 'unknown'):
        raise ValueError('split must be held_out, development, training, or unknown.')
    cases = document['cases']
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError('Provide 1 to 1,000 labelled cases.')
    seen = set()
    families = set()
    any_probability = False
    all_probability = True
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_KEYS:
            raise ValueError('Each case needs id, family, actual_review_required, baseline, and candidate.')
        identifier = _text(case['id'], 'Case ID', 80, True)
        family = _text(case['family'], 'Case family', 80, True)
        if identifier in seen:
            raise ValueError('Case IDs must be unique in this evaluation.')
        seen.add(identifier)
        families.add(family)
        if len(families) > MAX_FAMILIES:
            raise ValueError('Use at most 16 case families.')
        if type(case['actual_review_required']) is not bool:
            raise ValueError('actual_review_required must be true or false.')
        for side in ('baseline', 'candidate'):
            _, probability = _prediction(case[side])
            has_probability = probability is not None
            any_probability |= has_probability
            all_probability &= has_probability
    if any_probability and not all_probability:
        raise ValueError('Supply probabilities for every baseline and candidate prediction, or for none.')
    return cases, any_probability


def _ratio(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else None


def _side(cases, side, probability_available):
    counts = {'true_review': 0, 'false_allow': 0, 'false_review': 0, 'true_allow': 0,
              'defer_review_required': 0, 'defer_ordinary': 0}
    bins = [{'count': 0, 'sum_probability': 0.0, 'review_required': 0} for _ in range(10)]
    brier_sum = 0.0
    for case in cases:
        actual = case['actual_review_required']
        route, probability = _prediction(case[side])
        if route == 'defer':
            counts['defer_review_required' if actual else 'defer_ordinary'] += 1
        elif actual:
            counts['true_review' if route == 'review' else 'false_allow'] += 1
        else:
            counts['false_review' if route == 'review' else 'true_allow'] += 1
        if probability_available:
            brier_sum += (probability - int(actual)) ** 2
            bucket = bins[min(9, int(probability * 10))]
            bucket['count'] += 1
            bucket['sum_probability'] += probability
            bucket['review_required'] += int(actual)
    positives = sum(case['actual_review_required'] for case in cases)
    negatives = len(cases) - positives
    decided = sum(counts[key] for key in ('true_review', 'false_allow', 'false_review', 'true_allow'))
    result = {'counts': counts,
              'rates': {'false_allow_among_review_required': _ratio(counts['false_allow'], positives),
                        'false_review_among_ordinary': _ratio(counts['false_review'], negatives),
                        'coverage': _ratio(decided, len(cases)),
                        'decision_accuracy': _ratio(counts['true_review'] + counts['true_allow'], decided),
                        'deferral': _ratio(len(cases) - decided, len(cases))}}
    if probability_available:
        result['forecast'] = {'brier_score': round(brier_sum / len(cases), 8),
                              'bins': [{'range': f'{index / 10:.1f}-{(index + 1) / 10:.1f}',
                                        'count': item['count'],
                                        'mean_predicted': _ratio(item['sum_probability'], item['count']),
                                        'observed_review_fraction': _ratio(item['review_required'], item['count'])}
                                       for index, item in enumerate(bins) if item['count']]}
    else:
        result['forecast'] = {'status': 'no_probabilities_reported'}
    return result


def audit_routing_evaluation(project, evaluation_path):
    """Audit fixed local labels and two predictions without running or writing code.

    All labels and probabilities are supplied by the caller. The result is
    descriptive and never grants permission or changes runtime routing.
    """
    relative, digest, input_bytes, document = _read_input(project, evaluation_path)
    cases, probability_available = _validated(document)
    positive = sum(case['actual_review_required'] for case in cases)
    baseline = _side(cases, 'baseline', probability_available)
    candidate = _side(cases, 'candidate', probability_available)
    paired = {'prevented_false_allows': 0, 'introduced_false_allows': 0,
              'new_deferrals': 0, 'resolved_deferrals': 0}
    families = {}
    for case in cases:
        base_route = case['baseline']['route']
        next_route = case['candidate']['route']
        if case['actual_review_required']:
            paired['prevented_false_allows'] += int(base_route == 'allow' and next_route != 'allow')
            paired['introduced_false_allows'] += int(base_route != 'allow' and next_route == 'allow')
        paired['new_deferrals'] += int(base_route != 'defer' and next_route == 'defer')
        paired['resolved_deferrals'] += int(base_route == 'defer' and next_route != 'defer')
        family = families.setdefault(case['family'], {'cases': 0, 'review_required': 0,
                                                      'baseline_false_allows': 0, 'candidate_false_allows': 0,
                                                      'baseline_false_reviews': 0, 'candidate_false_reviews': 0,
                                                      'baseline_deferrals': 0, 'candidate_deferrals': 0})
        family['cases'] += 1
        family['review_required'] += int(case['actual_review_required'])
        family['baseline_false_allows'] += int(case['actual_review_required'] and base_route == 'allow')
        family['candidate_false_allows'] += int(case['actual_review_required'] and next_route == 'allow')
        family['baseline_false_reviews'] += int(not case['actual_review_required'] and base_route == 'review')
        family['candidate_false_reviews'] += int(not case['actual_review_required'] and next_route == 'review')
        family['baseline_deferrals'] += int(base_route == 'defer')
        family['candidate_deferrals'] += int(next_route == 'defer')
    warnings = ['Labels, routes, probabilities and split are supplied by the evaluation file; independent validity is unverified.']
    if document['split'] != 'held_out':
        warnings.append('This split is not declared held out; optimization on these cases may overfit.')
    if len(cases) < 30:
        warnings.append('Fewer than 30 cases; rates and forecast bins are especially unstable.')
    if not 0 < positive < len(cases):
        warnings.append('Only one outcome class is present; one error rate is undefined.')
    if paired['introduced_false_allows']:
        warnings.append('Candidate introduces false allows on declared review-required cases.')
    response = {'schema': 'talktoai.routing-audit-result.v1',
                'source': {'path': relative, 'sha256': digest, 'bytes_checked': input_bytes},
                'dataset': document['dataset'], 'split': document['split'],
                'event_definition': document['event_definition'], 'rubric_version': document['rubric_version'],
                'baseline_version': document['baseline_version'], 'candidate_version': document['candidate_version'],
                'cases': len(cases), 'review_required': positive,
                'baseline': baseline, 'candidate': candidate, 'paired_changes': paired,
                'by_family': families, 'warnings': warnings,
                'verification': 'Read-only arithmetic over a byte-hashed local file. No labels, source independence, generalization, calibrated probability, action authorization or ethical correctness were verified.'}
    return json.dumps(response, ensure_ascii=False, allow_nan=False)
