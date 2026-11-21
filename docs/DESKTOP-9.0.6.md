# TalkToAi Code 9.0.6

## Inkling GPU settings on Windows x64

Open **Settings → Inkling acceleration**, choose a mode, and save. Select your existing Inkling model and send a message. Changing the mode restarts Inkling on the next request and reuses your model file.

| Mode | What happens |
| --- | --- |
| **Automatic** | Tries GPU acceleration. If the GPU checks fail, it starts the CPU runner and shows the reason. |
| **Require GPU** | Starts only after the GPU runner loads model layers and completes a local generation check. If those checks fail, the app reports the reason. |
| **CPU only** | Starts the CPU runner directly. |

Inkling Q4 and Q8 are larger than 8 GB, so an 8 GB card uses partial offload. Some model weights stay in system RAM. Inkling can try a smaller GPU allocation when the regular fit fails. When Vulkan cannot run the model and a compatible NVIDIA driver is installed, it can try CUDA. That first CUDA setup downloads about 652–749 MB of verified runner files. The GPU setting applies to Inkling.

The model selector displays the number of GPU layers after startup. **Models and setup → Recommended** shows the active backend or the last failure. If another Ollama model is occupying graphics memory, you can release that model under **Installed Ollama** before retrying Inkling.

[Inkling installation and saved downloads](DESKTOP-9.0.4.md) · [Pinned llama.cpp runner](https://github.com/ggml-org/llama.cpp/releases/tag/b12613)
