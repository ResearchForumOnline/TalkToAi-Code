# TalkToAi Code 9.0.5

## Inkling GPU acceleration on Windows x64

Update TalkToAi Code, reopen it, and send a message with **Inkling 2 Mini** selected. The app reuses your prepared Q4 or Q8 file. A PC with a Vulkan graphics driver may download the verified llama.cpp GPU runner once (about 39 MB).

TalkToAi checks the available compute device and fits model layers to available GPU memory. It runs a short local inference check before using GPU acceleration for your conversation. Some layers may run on the CPU, especially with Q8 or limited graphics memory. If GPU setup or the check fails, the app starts its CPU runner.

The model selector shows **GPU** with the number of layers actually offloaded, or **CPU** after startup. Hover over the model for details, or open **Models and setup → Recommended** to read the same status. An unloaded Inkling model checks acceleration the next time it starts.

[Inkling installation and saved downloads](DESKTOP-9.0.4.md) · [Pinned llama.cpp runner](https://github.com/ggml-org/llama.cpp/releases/tag/b12613)
