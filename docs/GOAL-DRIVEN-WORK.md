# Goal-driven work

A useful agent needs a durable record of the intended result, acceptance criteria, current evidence and next action. TalkToAi Code stores this task brief with its conversation. It is conversation metadata rather than model training, and does not grant new access or schedule background work.

## Use it

1. Open the intended project in Code and select Act for implementation.
2. Use Task goal in Steps to state the objective and small, observable acceptance criteria, or ask the agent to maintain a task goal.
3. Enable Keep going for bounded continuation. Stop interrupts the current work.
4. Inspect criterion evidence and actual test output. Resume with Continue unfinished work after reopening the conversation.

An example game objective is: Add a usable pause menu to the existing game.
Acceptance criteria could be: Escape opens and closes the menu; simulation pauses while the menu is open; restarting restores the initial state; available project checks pass. A headless import check alone does not establish the first three criteria.

An example research objective is: Reproduce the numerical result from a named method on a fixed input. Criteria should specify the input, command, tolerance and independently checked output. Use the experiment journal for measurements and evidence-file hashes.

## Meaning of progress

Criterion statuses and evidence notes are agent-reported. They help maintain scope and identify remaining work, but do not prove that an outcome is correct. A completed-sounding response is not enough to satisfy pending criteria. The app can request a bounded review and otherwise report unfinished work. Tool failures, running owned jobs and failed checks retain their existing completion gates.

Saved goals may be stale. Current user instructions take priority, and a goal is not authorization for unrelated actions. Task reports include the saved goal so the objective and reported result can be reviewed together. No task automatically resumes solely because the app was restarted.

## Design references

- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md
- https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/persistence.mdx

This is an original implementation in TalkToAi Code, with no new framework dependency. It improves the orchestration around the selected model; it does not alter the model weights or establish artificial general intelligence.
