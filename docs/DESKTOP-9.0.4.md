# TalkToAi Code 9.0.4

## Use Inkling 2 Mini on Windows x64

Open **Models and setup → Recommended** and choose **Inkling 2 Mini Q4** (16.6 GB) or **Q8** (22.9 GB). Studio sets up a pinned llama.cpp runner, then selects Inkling for Chat and Code when the runner is ready. The runner uses your CPU. Start with Q4 if you need a smaller download or have less available memory.

To reuse an existing file, open **Saved downloads** and select the Inkling Q4 or Q8 file, then choose **Use selected with Inkling**. Use **Browse for GGUF** for a copy in another folder. Studio can rebuild the original Unsloth layout with its local weights and a small compatibility transfer, then verify the result against the publisher's checksum. Keep enough extra disk space for the finished compatible file while the conversion runs. You can delete the original separately once the compatible copy works.

The small default stays OpenZero. **Local large** starts with Qwen3.6 35B A3B. Inkling is a separately selectable route and stops to release memory when you switch models. The guided setup is for Windows x64. See the model publisher's instructions for other platforms. [Unsloth model files and compatibility notes](https://huggingface.co/unsloth/Inkling-2-Mini-GGUF/tree/ae9327c5fbc7333116ba32eee78bb5d2bbefae4f/Mainline_Support) · [Pinned llama.cpp release](https://github.com/ggml-org/llama.cpp/releases/tag/b12613)

## Delete a model

- Choose **Installed Ollama → Delete from Ollama** to remove a model registration shared with other apps using that service.
- Choose **Saved downloads → Delete saved file** to remove a standalone GGUF. This is where the compatible Inkling copy appears, as well as any separately downloaded original.
- Choose **Saved downloads → Clear unfinished** to clear resumable partial downloads you no longer need.
