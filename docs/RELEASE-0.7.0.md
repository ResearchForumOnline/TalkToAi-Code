# TalkToAi Code 0.7.0

## Persistent task goals

- Save an objective, 1-8 acceptance criteria, evidence notes and a next action with each conversation.
- Edit the brief in Steps > Task goal. Changed objectives reset criterion status; unchanged criteria retain their evidence.
- Let the agent establish and maintain goals through a lazily loaded tool set, including during Keep going work.
- Resume with the stored goal after reopening. Current user steering takes priority; changing project clears the previous goal.
- Review pending or blocked criteria before completion, then pause explicitly if unresolved. Goal metadata does not count as actual tool progress or independent verification.
- Include the goal in exported task reports.

## Verification

262 tests passed on Windows with native acceptance enabled. Tests cover normalization, stable criterion IDs, bounded storage/context, persistence, project changes, current steering, completion gates and UI editing.

The model's criterion statuses remain self-reported. These changes improve task orchestration and continuity; they do not change model weights, establish AGI or guarantee a scientific discovery.

See [goal-driven work](https://github.com/ResearchForumOnline/TalkToAi-Code/blob/v0.7.0/docs/GOAL-DRIVEN-WORK.md) for examples and primary design references.
