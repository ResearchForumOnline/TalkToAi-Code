# TalkToAi Code 9.0.1

## Ask for the action in Chat

Type “Open Brave and inspect the Downloads page,” “Look up the latest Godot release,” or “Review the implementation, tests and README in this project.” Chat chooses the appropriate available tools and can continue with commands such as “Now click Save.” Requests for advice and ordinary conversation use the selected chat model. Code keeps its workspace for longer projects, diffs and checks.

Agent Zero can start up to three local project reviewers for work that splits into clear questions. Try “Fix this loader and investigate gaps in its regression tests.” Reviewers run one at a time on the selected local Ollama model and report the project files they observed.

## Create an image or video

Ask “Create an image of a lighthouse at blue hour” or “Make a short video of a rainy street.” TalkToAi connects to ComfyUI running on this computer at `127.0.0.1:8188`. The resulting image appears with a preview in Chat. A video appears with **Open video**. The conversation also keeps the output in Evidence.

- **Images.** A running ComfyUI with a supported installed Stable Diffusion checkpoint can generate images immediately. You can also use a locally prepared image workflow.
- **Video.** Set up a text-to-video workflow and its weights in ComfyUI first. Export the workflow in **API format**. Put the literal text `{{prompt}}` in its positive prompt input and save the JSON as `video.workflow.json` under the TalkToAi app data folder’s `media` folder. A locally prepared image workflow can use the same process with `image.workflow.json`. Use an included ComfyUI save node for the requested output. The workflow determines the model, duration, and output dimensions.
- **Find the folder.** Open **More → Open app data folder**, then open or create `media`. Start ComfyUI and send your request directly in Chat. **Stop** stops TalkToAi from waiting for the result from that job.

ComfyUI and its image or video weights are local prerequisites. TalkToAi routes normal model conversations through Ollama or the selected API. The local media route uses no TalkToAi subscription or API charges. [ComfyUI setup](https://docs.comfy.org/get_started) · [ComfyUI API examples](https://github.com/Comfy-Org/ComfyUI/tree/master/script_examples)

## Model memory and the project tools

The one-hour Ollama retention, measured tuning and undo, installed-model refresh, browser targeting and desktop tools from 9.0 are included. [9.0 model guide](DESKTOP-9.0.0.md) · [ZBA, Qwen Code and Hermes references](DESKTOP-9.0.0.md#research)
