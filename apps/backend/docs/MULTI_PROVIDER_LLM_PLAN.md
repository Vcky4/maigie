# Multi-provider LLM and backend hardening plan

This document tracks the roadmap for **codebase improvements** first, then **OpenAI / Anthropic** (and similar) alongside Google Gemini.

## Goals

1. **Single source of truth** for API keys (typed `Settings`), default model IDs per *logical task*, and billing alignment.
2. **Provider-agnostic boundary** so chat, tools, streaming, and batch jobs can swap implementations without rewriting product logic.
3. **Safe rollout** via feature flags, allowlists, and tests on the agentic tool loop. 

## Phase A — Foundations (done)

| ID | Work | Status |
|----|------|--------|
| A1 | Add `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and Gemini rotation envs to `src/config.py` `Settings`; replace `os.getenv("GEMINI_API_KEY")` in application `src/` code | Done |
| A2 | Add `src/services/llm_registry.py`: `LlmTask`, `default_model_for()`, `gemini_api_key()` | Done |
| A3 | Align `cost_calculator.py` with registry model IDs and current pricing | Done |
| A4 | Extract large chunks from `routes/chat.py` and `routes/courses.py` (WS loop, context builder, persistence) | Done (`chat_ws`, `chat_helpers`, `chat_greeting`, `chat_sessions` + `chat_session_service` for REST session CRUD; `courses_helpers`; `llm_chat_context.py`) |
| A5 | Standardize imports (`src.` vs relative) on touched files | Done (`courses.py`, `courses_helpers.py`, `credit_service.py`) |
| A6 | Add tests: tool handler routing, fake LLM tool round-trip, credit path | Done (existing handler/context tests + `test_llm_agentic_roundtrip.py`, `test_credit_service.py`) |

**Exit criteria:** No production `src/` path reads `GEMINI_API_KEY` via raw `os.getenv`; default model strings for supported tasks live in `llm_registry`.

**Unit tests without DB:** set `SKIP_DB_FIXTURE=1` (see `tests/conftest.py`) to skip the autouse Prisma lifecycle, e.g. `SKIP_DB_FIXTURE=1 pytest tests/test_cost_calculator.py`.

## Phase B — LLM adapter (Gemini-only implementation)

- Introduce `src/services/llm/` (or equivalent): neutral types (`Message`, `ToolDefinition`, `Usage`), `Protocol` for `complete` / `stream` / `complete_with_tools`.
- Move Google GenAI construction into `gemini_adapter.py`; keep `gemini_tool_handlers.py` as execution (same tool names).

**Status (incremental):** `src/services/llm/` includes `prompts.py` (Maigie system instruction + `build_personalized_system_instruction`), `gemini_sdk.py`, `streaming.py`, `gemini_chat_tools.py`, `types.py`, `protocol.py`. Agentic chat lives in `gemini_chat_tools.py`; REST/WS call sites use `chat_with_tools_provider()` (`llm_service.py`) for a typed `ChatWithToolsProvider`. Next: feature-flag alternate providers behind the same accessor.

**Exit criteria:** Chat entry points call the adapter, not `genai.Client` directly in routes.

## Phase C — OpenAI

1. Non-streaming, no tools (smoke + analytics-style calls).
2. Streaming without tools.
3. Tools + streaming with normalized tool_calls → existing `handle_tool_call`.

## Phase D — Anthropic

Same adapter interface; map Claude message/tool blocks and streaming events.

## Phase E — Routing and policy

- Per-user or per-tier allowed models; default and fallback chains.
- Persist `provider`, `model_id`, and token usage per turn.

## Phase F — Embeddings (optional)

Multi-vendor embeddings imply **new vector index** (or namespace) per embedding model/dimension, or a re-embed migration. Chat-only multi-model does not require this immediately.

## References

- Implementation: `src/services/llm_registry.py`, `src/config.py`
- Primary integration today: `src/services/llm_service.py`, `src/services/gemini_tool_handlers.py`
