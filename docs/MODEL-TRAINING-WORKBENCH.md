# Model training workbench

The workbench trains separate candidate weights. It does not replace the active model, modify its GGUF, download models or automatically promote candidates.

## CPU fixture: implemented and tested

The built-in standard-library fixture performs gradient descent on seven rank-one adapter parameters over a frozen twelve-parameter base. It uses 48 synthetic training examples and 24 separate evaluation examples, exports `candidate/adapter.json`, reloads the weights and checks predictions. This is real numerical training of a small linear model, **not an LLM or evidence of improved assistant intelligence**.

The measured 250-step fixture reduced held-out mean squared error from 0.6266666674 to 0.000000228649. Base weights stayed unchanged, adapter weights changed and reload predictions matched. Maximum fixture steps: 2,000.

## Optional local Hugging Face LoRA

This backend requires an existing environment containing torch, transformers, peft and safetensors. Missing dependencies are reported without installing anything. The packaged desktop app supports the CPU fixture; HF jobs use a separate Python environment.

Supply an absolute local model directory with config.json, local tokenizer assets and Safetensors weights, plus an explicitly reviewed JSONL dataset. Each line must contain only a nonempty text field. Limits: 10–2,000 rows, 4 MiB total, 4,000 characters per row.

The loader is offline, local-files-only and does not trust remote model code. CPU limits are 512 MiB of base weights, 125 million model parameters, 100 training steps, rank 1–8, sequence length 16–256 and two CPU threads. A meta-device parameter count precedes allocation. Expert/MoE bases are rejected. This backend cannot train the served 30B model on the observed CPU-only server.

The final 20 percent of rows form a held-out split, with evaluation capped at 32 examples. Duplicate data can contaminate this split; loss improvement is not a capability certification. Results report held-out loss, adapter changes, unchanged base-file hashes and PEFT artifacts. The optional HF backend was **not executed in this release environment**, because its dependencies and an approved local base were absent.

No Ollama import or model promotion occurs. Architecture-specific conversion requires a separate compatibility check.

## Shared CLI

OpenZero source:

```text
python brain/training_workbench.py status --root ./training-jobs
python brain/training_workbench.py fixture --root ./training-jobs --steps 250
python brain/training_workbench.py lora --root ./training-jobs --request ./reviewed-training-request.json
```

TalkToAi Code includes the identical module at its source root:

```text
python training_workbench.py status --root ./training-jobs
python training_workbench.py fixture --root ./training-jobs --steps 250
```

Optional request shape:

```json
{"backend":"lora","base_model_path":"/absolute/local/model","dataset_path":"/absolute/reviewed-data.jsonl","steps":10,"rank":4,"max_length":128,"learning_rate":0.001}
```

Ctrl+C requests cancellation. Keep datasets and generated jobs outside public source commits.

## Authenticated API

Register with `register_training_routes(app, authorized_callback, work_root)`. Every route uses the hosting application's owner-authentication callback.

- GET `/api/training/status`: backend availability, limits and jobs.
- GET `/api/training/jobs`: recent jobs.
- POST `/api/training/jobs`: explicitly start a fixture or LoRA job; returns HTTP 202 and a job object.
- GET `/api/training/jobs/<id>`: status, progress and results.
- POST `/api/training/jobs/<id>/cancel`: request cancellation.
- GET `/api/training/jobs/<id>/artifacts/<name>`: export a completed artifact after verifying its recorded SHA-256.

States: queued, running, completed, failed, cancelled. One job owns the workbench at a time. Results have `promoted: false`; completion means the declared training process finished, not that assistant performance improved.

Eight tests cover real fixture training, reload/export hashes, cancellation, cross-instance job locking, missing dependencies, input limits, packaged fixture operation, authentication, changed-artifact rejection and invalid job identifiers.

## Primary references

- [PEFT quicktour](https://huggingface.co/docs/peft/main/quicktour)
- [PEFT custom model adaptation](https://huggingface.co/docs/peft/en/developer_guides/custom_models)
- [Transformers CPU training](https://huggingface.co/docs/transformers/en/perf_train_cpu)
- [Ollama model import](https://docs.ollama.com/import)
- [Official LoRA conversion source](https://github.com/ggml-org/llama.cpp/blob/master/convert_lora_to_gguf.py)
