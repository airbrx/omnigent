# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Omnigent is an open-source AI agent framework and **meta-harness**: a common orchestration layer over many agent runtimes (Claude Code, Codex, Cursor, Pi, OpenAI Agents, Google Antigravity, and YAML-defined custom agents). The same session can run on any harness, follow the user across devices (terminal / web / phone), be governed by policies, and run in local or cloud sandboxes. The Python package is `omnigent`; the CLI ships as both `omnigent` and the short alias `omni` (identical entry point, `omnigent.cli:main`).

## Environment & common commands

Python 3.12+ managed with `uv`. First-time setup:

```bash
uv python install
uv venv --python "$(cat .python-version)"
uv sync --extra all --extra dev
source .venv/bin/activate            # or prefix everything with `uv run`
```

Checks:

```bash
uv run pytest                        # unit + inner suites (e2e/e2e_ui/e2e_live/integration ignored by default — see addopts)
uv run pytest tests/path/test_x.py::test_name   # single test
uv run pytest tests/server/ -k some_filter
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files
```

Run a live e2e test (needs a real key):

```bash
uv run pytest tests/e2e/ --llm-api-key $LLM_API_KEY        # or -m mock_only to run mock-LLM e2e without a key
```

Frontend (`ap-web/`, only when touched):

```bash
cd ap-web && npm install && npm run lint && npm run build   # lint = oxlint; npm test = vitest
```

Running locally takes three terminals: `omnigent server` (local server + web UI on :6767), `omnigent host --server http://localhost:6767` (registers this machine so the UI can browse the FS and start sessions), and `cd ap-web && npm run dev` (Vite on :5173).

## Test conventions (enforced in review)

- A behavior change under `omnigent/` ships with a test in the **mirroring** suite — `omnigent/server/` → `tests/server/`, `omnigent/runner/` → `tests/runner/`, etc. Prefer the smallest unit test; reach for `tests/integration/` only when behavior spans components and `tests/e2e/` only for full-stack live-LLM flows.
- A PR adding **new user-facing functionality** must include at least one `tests/e2e/` happy-path test (Copilot review enforces this). A UI behavior change additionally needs a Playwright test under `tests/e2e_ui/` (mechanically enforced by the `E2E UI Required` check).
- Pure refactors, renames, type-only changes, dep bumps, and no-observable-change edits are exempt.
- `@pytest.mark.skip` is banned by a pre-commit lint (`dev/lint/lint_no_skipped_tests.py`). Key markers (see `pyproject.toml`): `live`/`live_app`, `model(...)`, `flaky` (timing races), `llm_flaky` (real-LLM nondeterminism, rotates models), `mock_only`, `nightly`.
- Commits are sign-off required: `git commit -s` (DCO). Branch from `main`.

## Architecture

The flow is **spec → runtime → (server | runner | host)**, with the runtime delegating actual agent execution to a pluggable **harness/executor**.

- **`spec/`** — The portable agent format. An *agent image* is a directory (`config.yaml` + optional `AGENTS.md`, `skills/`, `tools/`, `agents/` sub-agents); the server stores it as a tarball. `types.py`/`parser.py`/`validator.py`/`tar_utils.py` parse it into a typed `AgentSpec`. `AGENTSPEC.md` is the authoritative format doc; user-facing schema is `docs/AGENT_YAML_SPEC.md`.

- **`runtime/`** — The execution engine: a *library*, not a service. Given a spec + user input, it drives the reasoning loop (LLM calls, tool calls, skills, compaction, telemetry). `runtime/harnesses/` adapts the runtime to a chosen executor. The server is its primary host, but it's usable directly in tests or embedded.

- **`inner/`** — Pre-integration Omnigent code merged in during "unification." Holds the **harness implementations**: each runtime has a `*_harness.py` + `*_executor.py` pair (`claude_sdk_*`, `codex_*`, `cursor_*`, `pi_*`, `openai_agents_sdk_*`, `antigravity_*`, `databricks_executor.py`). Imports *within* `inner/` use relative syntax; imports *into* `inner/` from the rest of the package use the explicit `omnigent.inner.X` path so the dependency is grep-findable.

- **Native harnesses** (top-level `omnigent/claude_native*.py`, `codex_native*.py`, `cursor_native*.py`, `pi_native*.py`) are different from SDK harnesses: they boot a vendor TUI in a resident terminal, type user messages into it, and mirror the transcript back — so the runner must *not* replay history or treat a queued call as an in-process turn. See `harness_aliases.py` (`NATIVE_HARNESSES`, `canonicalize_harness`) for the canonical names and the alias map (`claude`→`claude-sdk`, `agy`/`google-antigravity`→`antigravity`, etc.). These wrap each terminal in an OS sandbox: `bwrap`/bubblewrap on Linux (mandatory), seatbelt on macOS; they require `tmux`.

- **`server/`** — The multi-tenant, always-on deployment (FastAPI/Starlette, `app.py` + `routes/`). Stores agent specs, manages sessions/presence/auth (OIDC, accounts, invites), and dispatches work to hosts/runners. Auth is off by default, gated by `OMNIGENT_AUTH_ENABLED=1` (on by default in the Docker deploy). `server/API.md` and `server/DBSPEC.md` document the HTTP surface and DB schema.

- **`runner/`** — Per-session execution transport: routing, tool dispatch, MCP management, pending approvals/policy enforcement at the point of a tool call. Bridges the server's session abstraction to the runtime.

- **`host/`** — A registered machine (e.g. the user's laptop) that the server can run sessions on. `omnigent host` registers it; this is what lets the web UI browse the local filesystem and start new sessions there. `host/local_server.py` owns the local daemon lifecycle (PID liveness via psutil).

- **`tools/`** — Tool system: `local_callable.py` (Python function → auto-schema), `mcp.py` (MCP servers), `client_specified/`, `builtins/`, plus `manager.py`. **`policies/`** — Layered governance (server / agent / session, stricter session rules first); builtins under `policies/builtins/` (cost caps, shell/file approval, tool limits). CEL (`cel-expr-python`) backs inline policy expressions and degrades gracefully when absent. Docs in `docs/POLICIES.md`.

- **`llms/`** — Provider-agnostic LLM client (`client.py`, `adapters/`, routing, summarization, context-window handling). `LLMCLIENT.md` documents it.

- **`onboarding/`** — Credential/model setup (`omnigent setup`): four credential kinds (API key, subscription via `claude`/`codex` CLIs, gateway base_url, Databricks profile). Secrets go to the OS keyring (`secrets.py`), falling back to a 0600 file when headless.

- **`sandbox/`** & **`environments/`** — Cloud sandbox launchers (Modal, Daytona, CoreWeave, Islo) selected via the matching optional extra; SDKs are imported lazily so only users of a provider need the extra.

- **`stores/`**, **`entities/`**, **`db/`** — Persistence (SQLAlchemy + Alembic migrations). A `db/` schema change warrants a `tests/db/` test.

- **`repl/`**, **`chat.py`**, **`terminals/`**, **`native_terminal.py`** — The interactive terminal/REPL frontends.

- **`sdks/`** — Sibling packages released in lockstep at the same version: `omnigent-client` (`sdks/python-client`) and `omnigent-ui-sdk` (`sdks/ui`), wired as editable path-deps via `[tool.uv.sources]`. `ap-web/` is the React/Vite web UI.

## Conventions & gotchas

- **Two CLIs, one entry point**: `omnigent` and `omni` are interchangeable. The click-based `omnigent/cli.py` absorbed the legacy argparse commands (see `designs/`/`UNIFICATION` references).
- **Optional extras are lazy**: provider SDKs (sandboxes, bedrock/vertex/s3, databricks) live behind extras and are imported lazily. Don't add a hard top-level import for an extra-gated dependency. `databricks-sdk` is in the `all` extra (used by CI/tests), not the base install.
- **uv dependency cooldown**: `uv.toml` sets `exclude-newer = "P7D"` — resolution never picks a distribution younger than 7 days. Don't fight it; per-package exemptions go in `exclude-newer-package`. A pre-commit hook (`scripts/normalize_uv_lock_registry.py`) normalizes the lock registry to pypi.org.
- **Ruff** is `target-version = py310` deliberately (avoids an out-of-scope `TimeoutError` migration) even though runtime is 3.12+. Vendored trees (`omnigent/inner/databricks_mcps/google`, `assistant-ui/`) are excluded — don't restyle them.
- **No global asyncio monkeypatching** — a pre-commit lint blocks it.
- Generated `omnigent/_build_info.py` is written at wheel-build time by `setup.py` (for the update-check); it's gitignored. `setup.py` also builds the web UI and materializes the `examples/{polly,debby}` symlinks into real dirs in the wheel.
- `openapi.json` is generated (`scripts/dump_openapi.py`); don't hand-edit.
