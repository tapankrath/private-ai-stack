# ADR-001: Inference runtime

Status: Accepted (2026-10-19)

## Context
Small deployments need easy model management; multi-user ones need batching. Engines differ in
openness, platform support and maturity, and some are experimental or in maintenance.

## Decision
- Rules live in `config/runtimes.yaml`; first match wins.
- NVIDIA with 8 or more sessions (humans + agents) uses **vLLM** (SGLang is the alternative).
- Apple Silicon uses **Ollama, installed natively** (MLX is the faster alternative), because Docker on macOS cannot use the GPU.
- Non-technical owners get **Ollama + Open WebUI**.
- **HF TGI is never recommended**; it is in maintenance mode.
- **TensorFold** is allowed only with `runtime_preferences.allow_experimental: true`; its speedups are self-reported.
- **LM Studio** is allowed only with `accept_closed_source: true`; it is free but closed source, against principle 7.
- If vLLM would leave no room for a required specialist model, the plan falls back to Ollama and says so.

## Consequences
- One engine choice per plan, explained in the recommendation.
- TensorRT-LLM is listed for NVIDIA-specific formats such as NVFP4, but is not a default (setup cost).
