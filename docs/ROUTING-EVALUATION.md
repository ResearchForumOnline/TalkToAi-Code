# Audit a routing model on fixed labelled cases

`routing_evaluation.audit_routing_evaluation(project, evaluation_path)` reads a
project-local JSON file and returns a bounded report. It helps compare two
versions of a route selector over the **same cases and labels**. The audit does
not run the models, change app permissions, or decide whether an action is
authorized. A `review` route is an operational label defined by the dataset's
rubric; it is not a universal moral judgment.

The format supports a concrete question from the public
[Probability of Goodness routing paper](https://github.com/ResearchForumOnline/research/blob/main/papers/probability-of-goodness-ethical-routing.md):
after defining an observable event, do candidate decisions and optional
probability forecasts behave better on a versioned, labelled set? That paper
identifies the existing keyword score as uncalibrated telemetry. This audit
does not promote that score into an authorization rule.

Save an evaluation as `evaluation/routes.json` in the selected project:

```json
{
  "schema": "talktoai.routing-evaluation.v1",
  "dataset": "synthetic route fixture v1",
  "split": "development",
  "event_definition": "Review required under synthetic rubric v1",
  "rubric_version": "synthetic-v1",
  "baseline_version": "rules-v1",
  "candidate_version": "rules-v2",
  "cases": [
    {
      "id": "example-1",
      "family": "ordinary-requests",
      "actual_review_required": false,
      "baseline": {"route": "review", "predicted_review_probability": 0.7},
      "candidate": {"route": "allow", "predicted_review_probability": 0.1}
    },
    {
      "id": "example-2",
      "family": "changed-settings",
      "actual_review_required": true,
      "baseline": {"route": "allow", "predicted_review_probability": 0.2},
      "candidate": {"route": "review", "predicted_review_probability": 0.8}
    }
  ]
}
```

The example is synthetic and too small to establish model quality. Use
`split: "held_out"` only when the cases were actually held back from model
selection. Other accepted declarations are `development`, `training`, and
`unknown`. Give cases stable unique IDs and a scenario `family` so near
duplicates can be inspected together. The app trusts the declared labels and
split; it cannot independently authenticate them.

From the source checkout, run:

```powershell
python -c "from routing_evaluation import audit_routing_evaluation; print(audit_routing_evaluation('.', 'evaluation/routes.json'))"
```

`project` can be another existing folder instead of `.`. The function never
executes text from the evaluation file. It reads at most 1 MiB, accepts 1–1,000
cases and 16 families, rejects duplicate IDs and JSON keys, rejects
non-finite or out-of-range forecasts, and refuses linked, private, or
out-of-project paths. It returns the SHA-256 of the bytes read and does not
write a journal entry. Treat the digest as byte identity, not publisher
authenticity or validation of the labels.

The report includes the counts of correct reviews, false allows, false
reviews, correct allows, and deferrals by true label. It gives route coverage,
decision accuracy among non-deferrals, and error fractions with explicit
denominators. Paired counts show newly introduced and prevented false allows
on the **same** review-required cases. Each family gets an error and deferral
breakdown.
An undefined fraction is `null`, not zero.

If every prediction supplies `predicted_review_probability`, the report adds
the Brier score, the mean squared difference between that forecast and the
declared binary outcome across **all** cases, including deferrals. Lower is
better on these cases. It also returns ten
fixed probability bins with forecast means and observed event fractions. Bins
are descriptive; small bins and selected datasets cannot establish
calibration or generalization. Partial probability coverage is rejected so a
model cannot omit difficult cases from the score. With no forecasts, route
counts still work.

Version, rubric, source hash, data split, and observed errors must be reviewed
together. A lower Brier score can coexist with a newly introduced false allow;
the report flags that case. It deliberately makes **no automatic winner or
deployment decision**. Hard constraints and user permissions take precedence
over any score. Meaningful claims need representative cases, independently
checked labels, separate held-out families, and review of false-allow costs.
For methodological context, see the
[NIST AI Risk Management Framework](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)
and [probability calibration documentation](https://scikit-learn.org/1.8/modules/calibration.html).

This implements a small part of the public
[Zero Boundary Algebra 1.1 principle](https://github.com/ResearchForumOnline/research/blob/main/papers/zero-boundary-algebra-formal-specification-1.1.md)
that a claimed state and checked evidence should remain distinct. It does not
implement ZBA's formal operators or cryptographic commitments.
