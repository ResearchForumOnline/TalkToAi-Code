# TalkToAi Code 9.0.7

## Inkling GPU repair on Windows x64

Open **Models and setup → Recommended → Test Inkling GPU** to check the saved Inkling model. This tries GPU acceleration with the existing Q4 or Q8 weights. The first CUDA setup may download the verified runner. The result shows the backend, device and physical layer count. **Copy GPU details** copies the app version and the result so you can share an error.

On an 8 GB GeForce RTX, the app tries NVIDIA CUDA with a compact memory profile. It places the last six transformer blocks and the output layer on the GPU and keeps expert weights and the context cache in system RAM. A successful compact check shows **GPU · 7/25 layers**. CPU activity is expected with both 16.6 GB Q4 and 22.9 GB Q8. If the GPU check fails, **Automatic** can start the CPU runner. **Settings → Inkling acceleration → Require GPU** keeps the failure visible and lets you retry.

Two issues affected 9.0.6. The runner's previous log setting hid the lines TalkToAi needed to verify GPU use. Its eight-layer request also counted repeated passes in Inkling's loop and missed the physical transformer blocks. The updated check uses native model-loader evidence and requires the selected GPU to complete a short generation.

[Upstream logger](https://github.com/ggml-org/llama.cpp/blob/b12613/common/log.cpp#L548-L565) · [Inkling loop implementation](https://github.com/ggml-org/llama.cpp/blob/b12613/src/models/inkling2-loop.cpp) · [Inkling setup and model removal](DESKTOP-9.0.4.md)
