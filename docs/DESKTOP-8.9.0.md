# TalkToAi Code 8.9.0 on Windows

## Start a conversation

Open **Chat** and type a message. Try “Help me write a birthday invitation,” then “Make it shorter.” TalkToAi uses recent messages from the same conversation for follow-up questions. You can attach a document and ask about it. **Stop** interrupts a reply. If a reply fails, **Retry reply** uses the original message and its attachment.

The default OpenZero stream removes a stray template marker that some Ollama replies otherwise showed before the first word. Everyday conversation works without opening a project. To do something on your computer, make an explicit request such as “Open my Brave browser,” or select **Use tools** for the next message. **Code** opens the project workspace.

## Check a model you downloaded

1. In Chat, open **Models → Installed Ollama**.
2. Select your model, for example `qwen3.6:35b`, then choose **Test reply**. The app starts an existing Ollama service if needed, sends a short arithmetic question with a known answer, then shows the reply and elapsed time. You can see whether it answered exactly as requested, answered differently, or stopped before completing a reply. A completed check records the time and result for that exact model version on this PC.
3. Choose **Use selected for Local** to chat with it. Turning on **Auto** also considers the configured large model when it is already installed.

The first reply can take several minutes while Qwen loads. “3B active” describes the part used to generate each token. The full 35B model still takes roughly 23 GB of disk space and needs sufficient RAM or VRAM. For a smaller local option, open **Models → Recommended → Install OpenZero 1.7B**. Its weights take about 1.1 GB. The first automatic OpenZero setup on Windows can also install Ollama through Windows Package Manager. An existing working Ollama installation is reused.

In **Auto**, if Ollama explicitly reports that the selected local model needs more memory before it produces output, Chat can try your configured smaller local model once. It shows which model takes over. An explicit **Local large** selection keeps your chosen model.

## Reuse files from Hermes or another app

If both apps use the same local Ollama service, look in **Installed Ollama** first. You can select an existing model directly. Entries with the same manifest digest are labeled, and removing one name may recover little space because other names can share its weights.

If you already have a compatible `.gguf` file on disk, open **Models → Saved downloads → Use a GGUF from disk…**. Choose the file, or every shard of the same split model. Ollama imports the local files and reuses matching blobs already in its store. This avoids another internet download. The original file stays in its original folder. For your first Qwen install, **Install Qwen3.6 35B A3B** in Recommended gets the version that Chat can run. **Save Qwen UD-Q4_K_M GGUF** creates a standalone export for a separate use.

## Recover disk space

**Saved downloads** lists completed Studio exports largest first. Select a compatible GGUF and choose **Use selected for chat** to import it. Choose **Remove saved file…** to remove a saved export. **Installed Ollama → Remove from Ollama…** removes the selected model name from the shared local service. Both actions identify the item before removal.

Studio can resume a recent interrupted Hugging Face download, including one started by 8.8.0. It automatically clears its own recognized partial downloads after 30 days of inactivity. Choose **Clear unfinished…** to discard listed partial downloads sooner. Complete models, conversations and files in another app’s folders require a separate user action.

## Connect an API

Choose your provider in the private API vault. Ordinary Chat sends a conversation without requesting function tools. For compatible providers, a definite rejection of certain optional parameters can be retried automatically before the model starts answering. For tool requests, Studio preserves the tools the task needs and respects the profiles and cost limits you enabled. An authentication, capacity or model error can still require action in your provider account.

[Browser, Blender, Unity, Godot and offline game assets](DESKTOP-8.8.0.md) · [Provider setup](DESKTOP-8.7.0.md)

The local reply check draws on ResearchForumOnline’s fixed-answer method for diagnosing local model serving and the practice of exercising live backends in Hermes. A single example measures the behavior of that local setup on that prompt. [ResearchForumOnline serving-path study](https://github.com/ResearchForumOnline/research/blob/28397b2570bf7d87ea46d3dfb47b36c426eaed74/papers/local-llm-serving-path-benchmark-2026-07-26.md) · [Hermes live diagnostics](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/doctor_live.py)
