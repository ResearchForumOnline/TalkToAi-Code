# TalkToAi Code 9.1.1

## Follow a task

In Code, **Keep going → Until done** lets Agent Zero work across successive passes. New profiles use this by default. Existing profiles keep their saved duration. You can choose one, two or four hours or press **Stop** at any time. When the app needs a model connection or a decision, answer in the same conversation to resume.

Type **show my to-do list** in Chat or Code, including while a job runs. To make your own, try **Make a to-do list: sketch the scene, add lighting, export the model**. Say **mark item 2 done** or **uncheck 2** to edit it. Each list belongs to its conversation and project. The Steps panel identifies items marked by you, reported by the agent, or verified by the app. A scene check uses the filename and SHA-256 of the registered output.

## Make a scene without a model

Open a project, select **Act**, and ask **Make a 3D winter scene offline** or **Make a 3D lunar impact scene offline**. The built-in generator creates supported subjects without downloading model weights or connecting to an API.

With Blender available, TalkToAi saves an editable scene and reopens it to check its geometry. Without Blender it can save a GLB and a self-contained browser viewer. Open the file in **Evidence**, or ask **How do I open the scene in my browser?** for a local viewer link. **Open the scene in my browser** launches it. The Blender viewer shows mesh geometry and base material colours from the saved frame. Use Blender for animation, textures and the fully rendered appearance.

The built-in generator produces stylized artwork. If you asked for photorealism, the app keeps the starting scene and records the remaining work. You can choose **keep the stylized scene** to accept the saved file.

## Resume interrupted work

After a scene task pauses, **finish the scene** refers back to the original request. The app checks the recorded file hash before reusing the result. If a local Ollama stream ends unexpectedly during agent work, TalkToAi can retry the interrupted step without executing an incomplete tool call. A repeated read after a Python error can show the lines around the actual failure.

The checklist distinguishes a reported result from an outcome the app can check. This follows the project's published work on verifiable agent outcomes. [Outcome-first local agents](https://github.com/ResearchForumOnline/research/blob/main/papers/outcome-first-local-agents-2026.md) · [Agent release evidence boundaries](https://github.com/ResearchForumOnline/research/blob/main/papers/agent-release-evidence-boundaries-2026.md)
