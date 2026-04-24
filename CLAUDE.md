# product-reader-ai: Development State & Architecture

**Date**: 24 April 2026  
**Python**: 3.12 / macOS  
**Framework**: Strands agents + AWS Bedrock (Claude models)

---

## Development Phases

| Phase | Status | Description |
|-------|--------|-------------|
| **Phase 1 — Local** | ✅ Active | Run fully locally; `LOCAL_FLOW=true`; artifacts on disk. |
| **Phase 2 — AWS** | Planned | Deploy to AWS (Lambda / ECS / Fargate). |
| **Phase 3 — GitHub** | Planned | GitHub integration: commits, PRs, baseline validation. |

**Current focus: Phase 1** — getting output quality right before deployment.

---

## Overview

`product-reader-ai` is a multi-agent AI system that **crawls webshops, extracts product data, and generates profiles + test scenarios** via an orchestrator pattern.

### Local flow (Phase 1)

1. **STEP 1**: Discover product URLs → analyse page structure → scrape all products
2. **STEP 2**: Write product profile JSON to disk
3. **STEP 3**: Write test scenarios JSON to disk

### GitHub flow (Phase 3)

1. **STEP 1**: same as local
2. **STEP 2**: Register slug and create feature branch
3. **STEP 3**: Write product profile JSON and commit
4. **STEP 4**: Write test scenarios JSON and commit
5. **STEP 5**: Open PR for review
6. **STEP 6**: Run baseline validation, retry on failure
7. **STEP 7/8**: Report success or post failure comments

---

## Architecture

### Core Modules

| Module | Purpose |
|--------|---------|
| `agent.py` | Orchestrator entry point; branches on `LOCAL_FLOW` |
| `context.py` | Shared module-level `github_mcp_client: MCPClient \| None` (set before agent runs; `None` in local mode) |
| `state.py` | Disk-based state persistence tools (schema, selectors, products, links, mismatches, generic file I/O) |
| `prompts.py` | System + task prompts — `TASK_PROMPT_TEMPLATE` (GitHub) and `LOCAL_TASK_PROMPT_TEMPLATE` (local) |
| `model_factory.py` | Bedrock model config for orchestrator + all sub-agents |
| `schemas.py` | Embedded profile and test scenario schemas |

### Sub-Agents

| Agent | Phase | Purpose | Key Tools |
|-------|-------|---------|-----------|
| `product_links_agent.py` | Both | Discover product URLs | `find_product_links` |
| `product_page_agent.py` | Both | Derive CSS selectors from one URL | `analyze_product_page`, `fetch_and_extract_elements`, `build_selectors` |
| `scraper_agent.py` | Both | Extract product data from all URLs | `scrape_product`, `scrape_all_products` |
| `profile_writer_agent.py` | Both | Write profile JSON | `write_profile` (GitHub), `write_profile_local` (local) |
| `test_writer_agent.py` | Both | Write test scenarios JSON | `write_tests` (GitHub), `write_tests_local` (local) |
| `validation_agent.py` | Phase 3 | Run workflows, compare results, retry | `validate_baseline` |

---

## State Persistence

All state lives under `Path(tempfile.gettempdir()) / "product-reader-ai"`:

```
slug_registry.json                    # Mapping of slugs to repos
<slug>/
  ├─ run_state.json                   # Step index, branch info, PR number
  ├─ selectors.json                   # CSS selectors (role → selector mapping)
  ├─ products.json                    # Extracted product data
  ├─ product_links.json               # Discovered product URLs
  ├─ profile.json                     # Generated profile (local flow output)
  ├─ tests.json                       # Generated test scenarios (local flow output)
  ├─ mismatches.jsonl                 # Validation failure log (Phase 3)
  └─ page_elements/
     └─ <hash>_elements.json          # Cached 80-element LLM view per URL
```

---

## Token Usage (Measured)

| Mode | Tokens per run | Root cause |
|------|---------------|-----------|
| Local flow (`LOCAL_FLOW=true`) | ~200k | LLM calls only |
| GitHub flow | ~12M | GitHub MCP responses dominate (file reads, search results, commit responses accumulate in orchestrator context) |

**The 60× difference is entirely from GitHub MCP tool responses.** The local flow eliminates all of that. This is why Phase 1 focuses on local correctness first.

---

## Token Optimisations Applied

### 1. Context Isolation (`context.py`)
- `github_mcp_client` is a module-level variable, never a `@tool` parameter
- Set to `None` in local mode — sub-agents that check it simply skip GitHub calls

### 2. Sliding Window Managers
- All sub-agents: `SlidingWindowConversationManager(window_size=10, should_truncate_results=True)`
- Orchestrator: `window_size=40, should_truncate_results=True`
- Keeps histories bounded; truncates oversized tool results

### 3. Scraper Loop as Pure Python
- `scrape_all_products(slug)` iterates URLs internally; orchestrator never sees individual product payloads
- Returns only a count confirmation string

### 4. Data Slimming in Prompts
- `_slim_selectors()` — keeps role/selector/type only
- `_slim_products()` — truncates description >300 chars, 1 image URL
- validation uses url/name/short_description only

### 5. Return Value Slimming
- `build_selectors` returns role/selector/type only (no `surrounding_html`, no `sample_text`)
- `page_elements` artifact stores only the 80-element LLM-visible subset, not all 500+ raw elements

---

## Running the System

```bash
# Set up environment (first time)
python -m venv venv
source venv/bin/activate
pip install -e .

# Local mode (Phase 1 — recommended)
LOCAL_FLOW=true python agent.py

# Full GitHub mode (Phase 3)
python agent.py

# View artifacts
ls /tmp/product-reader-ai/<slug>/
cat /tmp/product-reader-ai/<slug>/profile.json | jq
cat /tmp/product-reader-ai/<slug>/tests.json | jq
```

### Minimum `.env` for local mode
```env
LOCAL_FLOW=true
WEBSHOP_URLS=https://example-shop.com
AWS_REGION=us-east-1
```

### Additional variables for GitHub mode (Phase 3)
```env
LOCAL_FLOW=false
GITHUB_TOKEN=...
TARGET_REPO=owner/repo
BASE_BRANCH=main
```

---

## Known Patterns & Gotchas

1. **`LOCAL_FLOW` flag**
   - `LOCAL_FLOW=true` skips GitHub MCP client entirely (`context.github_mcp_client = None`)
   - `_validate_config()` does not require `GITHUB_TOKEN` or `TARGET_REPO` in local mode
   - `_build_task_prompt()` switches between `LOCAL_TASK_PROMPT_TEMPLATE` and `TASK_PROMPT_TEMPLATE`

2. **Tool Parameters Must Be Strings**
   - Strands passes all `@tool` params as JSON strings over stdio
   - Non-serializable objects → module-level variables via `context`

3. **Caching via File Existence**
   - `selectors.json`: if present, `analyze_product_page` is skipped entirely
   - `products.json`: if 15 items present, all of STEP 1 is skipped
   - All tools are idempotent — safe to re-run from any step

4. **Sliding Window & Result Truncation**
   - `should_truncate_results=True` is what caps large MCP responses in GitHub mode
   - In local mode this rarely triggers (tool outputs are compact)

5. **State Resumption**
   - `load_run_state(slug)` at startup; resume from the step after `state["step"]`
   - `scrape_all_products` uses the saved position internally

---

## File Structure (Quick Reference)

```
product-reader-ai/
├─ agent.py                   # Orchestrator (LOCAL_FLOW branch)
├─ context.py                 # Shared MCP client holder
├─ state.py                   # Disk I/O tools + write/read_file_to/from_disk
├─ prompts.py                 # TASK_PROMPT_TEMPLATE + LOCAL_TASK_PROMPT_TEMPLATE
├─ model_factory.py           # Bedrock config
├─ schemas.py                 # Embedded profile + test schemas
├─ product_links_agent.py     # Discover product URLs
├─ product_page_agent.py      # Derive CSS selectors
├─ scraper_agent.py           # Scrape all products
├─ profile_writer_agent.py    # write_profile (GitHub) + write_profile_local
├─ test_writer_agent.py       # write_tests (GitHub) + write_tests_local
├─ validation_agent.py        # Phase 3: baseline validation
├─ pyproject.toml
├─ README.md
├─ CLAUDE.md                  # This file
└─ tests/
   └─ test_agent.py
```

---

## Phase 2 Notes (AWS Deployment — Planned)

- Package as a container (Dockerfile) or Lambda handler
- State directory (`/tmp/product-reader-ai/`) maps naturally to Lambda's ephemeral `/tmp`
- For ECS/Fargate: mount an EFS volume or use S3 for state persistence across retries
- Bedrock credentials via IAM role (no `AWS_ACCESS_KEY_ID` needed)
- Consider SQS trigger: one message per webshop URL

## Phase 3 Notes (GitHub Integration — Planned)

- Requires `github-mcp-server` running as a sidecar or called via Docker/Podman
- Estimated token cost: ~12M per webshop run (measured)
- Orchestrator `window_size=40` with `should_truncate_results=True` is critical
- GitHub MCP responses are the dominant cost — consider caching schema reads

---

## Debugging

```bash
# Import check
python -c "from agent import main; print('OK')"

# View artifacts
cat /tmp/product-reader-ai/<slug>/profile.json | jq
cat /tmp/product-reader-ai/<slug>/tests.json | jq
cat /tmp/product-reader-ai/<slug>/products.json | jq

# Tail agent output
LOCAL_FLOW=true python agent.py 2>&1 | tail -100
```

---

**Last Updated**: 24 April 2026  
**Phase**: 1 — Local (active)  
**Status**: ✅ Local flow working; output quality refinement in progress
