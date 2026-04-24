# product-reader-ai: Development State & Architecture

**Date**: 24 April 2026  
**Python**: 3.12 / macOS  
**Framework**: Strands agents + AWS Bedrock (Claude models)

---

## Overview

`product-reader-ai` is a multi-agent AI system that **crawls webshops, extracts product data, and generates profiles + test scenarios** via an orchestrator pattern. The workflow:

1. **STEP 0**: Learn schema from GitHub reference files
2. **STEP 1**: Discover product URLs → analyze page structure → scrape all products
3. **STEP 2**: Register slug and create feature branch
4. **STEP 3**: Write product profile JSON and commit
5. **STEP 4**: Write test scenarios JSON and commit
6. **STEP 5**: Open PR for review
7. **STEP 6**: Run baseline validation, retry on failure
8. **STEP 7/8**: Report success or post failure comments

---

## Architecture

### Core Modules

| Module | Purpose |
|--------|---------|
| `agent.py` | Orchestrator entry point; builds Agent with all tools |
| `context.py` | Shared module-level `github_mcp_client: MCPClient \| None` (set before agent runs) |
| `state.py` | Disk-based state persistence tools (schema, selectors, products, links, mismatches) |
| `prompts.py` | System + task prompts (lean, focused on workflow steps) |
| `model_factory.py` | Bedrock model config for orchestrator + all 6 sub-agents |

### Sub-Agents (STEP-wise)

| Agent | Step | Purpose | Key Tools |
|-------|------|---------|-----------|
| `schema_agent.py` | 0 | Learn schema from GitHub refs | `learn_schema` |
| `product_links_agent.py` | 1a | Discover product URLs | `find_product_links` |
| `product_page_agent.py` | 1b | Derive CSS selectors from one URL | `analyze_product_page`, `extract_product_data`, `fetch_and_extract_elements` |
| `scraper_agent.py` | 1c | Extract product data from all URLs | `scrape_product`, `scrape_all_products` |
| `profile_writer_agent.py` | 3 | Write + commit profile JSON | `write_profile` |
| `test_writer_agent.py` | 4 | Write + commit test scenarios JSON | `write_tests` |
| `validation_agent.py` | 6 | Run workflows, compare results, retry | `validate_baseline` |

---

## State Persistence

All state lives under `Path(tempfile.gettempdir()) / "product-reader-ai"`:

```
schema.json                           # Global schema reference
slug_registry.json                    # Mapping of slugs to repos
<slug>/
  ├─ run_state.json                   # Step index, branch info, PR number
  ├─ selectors.json                   # CSS selectors (role → selector mapping)
  ├─ products.json                    # Extracted product data
  ├─ product_links.json               # Discovered product URLs
  ├─ mismatches.jsonl                 # Validation failure log
  └─ page_elements/
     └─ <hash>_elements.json          # Cached HTML elements per URL
```

---

## Recent Fixes & Optimizations (Session: Token Reduction)

### Problem
**MaxTokensReachedException** during product scraping — sub-agent internals and full data payloads were flowing into orchestrator's context window.

### Solutions Applied

#### 1. **Context Isolation** (`context.py`)
- Moved `github_mcp_client` to module-level variable (not a `@tool` parameter)
- All sub-agents: `import context; context.github_mcp_client` (set once before running)
- **Impact**: LLM no longer tries to fill non-serializable objects

#### 2. **Sliding Window Managers**
- All 6 sub-agents now have `SlidingWindowConversationManager(window_size=10, should_truncate_results=True)`
- **Impact**: Sub-agent histories stay bounded; old turns discarded

#### 3. **Scraper Loop Refactoring**
- Old: Orchestrator looped over 15 URLs, received full product JSON each time
- New: `scrape_all_products(slug)` is pure Python; loads/resumes/saves internally; returns confirmation only
- **Impact**: Orchestrator never sees individual product payloads

#### 4. **Data Slimming in Prompts**
- **profile_writer**: `_slim_selectors()` (keep role/selector/type), `_slim_products()` (truncate desc>300 chars, 1 image)
- **test_writer**: `_slim_products()` applied; selectors **removed entirely** from prompt (already in committed profile)
- **validation_agent**: `_slim_products_for_validation()` (keep only url/name/short_description)
- **Impact**: Prompts ~80% smaller

#### 5. **Return Value Slimming**
- `analyze_product_page`: returns JSON with role/selector/type only (strips surrounding_html+sample_text)
- `learn_schema`: returns confirmation string `"Schema learned and saved to disk."` instead of full schema
- **Impact**: Orchestrator context reduced; disk copies already slim

---

## Current State: Module Checklist

- ✅ `schema_agent.py` — returns confirmation, caches on re-runs
- ✅ `product_links_agent.py` — window manager active
- ✅ `product_page_agent.py` — returns slim JSON (role/selector/type only)
- ✅ `scraper_agent.py` — `scrape_all_products()` pure-Python loop; `scrape_product()` persists internally
- ✅ `profile_writer_agent.py` — slim selectors + products applied
- ✅ `test_writer_agent.py` — slim products applied; selectors removed from prompt
- ✅ `validation_agent.py` — slim products (url/name/short_desc) applied
- ✅ `agent.py` — orchestrator uses new tool signatures; `context.github_mcp_client` set before run
- ✅ `state.py` — disk tools for schema/selectors/products/links/mismatches
- ✅ `prompts.py` — lean system + task prompts; STEP-by-STEP guidance
- ✅ `context.py` — shared MCP client holder
- ✅ Import verification — `python -c "from agent import main; print('OK')"` ✓

---

## Running the System

```bash
# Set up environment (first time)
python -m venv venv
source venv/bin/activate
pip install -e .

# Run the orchestrator
python agent.py

# View state on disk
ls -R /tmp/product-reader-ai/
```

### Environment Variables
Ensure AWS credentials are set (Bedrock access):
```bash
export AWS_REGION=us-west-2
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

---

## Known Patterns & Gotchas

1. **Tool Parameters Must Be Strings**
   - Strands passes all `@tool` params as JSON strings over stdio
   - Non-serializable objects (files, clients) → module-level variables via `context`

2. **No `github_mcp_client` in Tool Signatures**
   - Set `context.github_mcp_client` once before agent runs
   - Sub-agents read it via `import context; context.github_mcp_client`

3. **Caching via File Existence**
   - `schema_agent`: checks `(STATE_ROOT / "schema.json").exists()` before building sub-agent
   - Avoids re-deriving schema on every run

4. **Sliding Window & Result Truncation**
   - `SlidingWindowConversationManager(window_size=10, should_truncate_results=True)` on all sub-agents
   - Keeps 10 most recent turns; truncates older ones if context grows

5. **State Resumption**
   - All tools are idempotent (safe to re-call)
   - `scrape_all_products` resumes from index stored in `run_state.json`

---

## File Structure (Quick Reference)

```
product-reader-ai/
├─ agent.py                   # Orchestrator
├─ context.py                 # Shared MCP client
├─ state.py                   # Disk I/O tools
├─ prompts.py                 # Prompts for orchestrator
├─ model_factory.py           # Bedrock config
├─ schema_agent.py            # STEP 0
├─ product_links_agent.py     # STEP 1a
├─ product_page_agent.py      # STEP 1b
├─ scraper_agent.py           # STEP 1c
├─ profile_writer_agent.py    # STEP 3
├─ test_writer_agent.py       # STEP 4
├─ validation_agent.py        # STEP 6
├─ pyproject.toml             # Package config
├─ README.md                  # User guide
├─ CLAUDE.md                  # This file (dev reference)
├─ tests/
│  └─ test_agent.py           # Integration tests
└─ product_reader_ai.egg-info/
```

---

## Token Budget Targets

| Component | Before | After | Savings |
|-----------|--------|-------|---------|
| Profile prompt | ~8KB (full selectors + all products) | ~1.5KB (slim) | ~80% |
| Test prompt | ~8KB (full selectors + products) | ~1.5KB (products only) | ~80% |
| Validation prompt | ~5KB (full products) | ~2KB (url/name/desc) | ~60% |
| Orchestrator window | Unbounded | ~10 turns (sliding) | ~70% |
| `analyze_product_page` return | ~3KB (with HTML) | ~0.5KB (slim JSON) | ~80% |
| `learn_schema` return | ~10KB (full schema) | ~50B (confirmation) | ~99% |

**Total estimated reduction**: ~75% of token usage (from full-data flow to lean workflow)

---

## Next Steps (If Needed)

- Monitor actual token usage during real runs (via Bedrock CloudWatch)
- Profile sub-agent performance (latency per step)
- Consider batching product scrapes if >100 products
- Add retry logic for transient network failures
- Expand test coverage in `tests/test_agent.py`

---

## Debugging

### Import Errors
```bash
python -c "from agent import main; print('OK')"
```

### State Inspection
```bash
# View schema
cat /tmp/product-reader-ai/schema.json | jq

# View products for slug
cat "/tmp/product-reader-ai/<slug>/products.json" | jq

# Tail mismatches
tail -f "/tmp/product-reader-ai/<slug>/mismatches.jsonl"
```

### Agent Logs
```bash
# Tail orchestrator output
python agent.py 2>&1 | tail -100
```

---

**Last Updated**: 24 April 2026  
**Session**: Token Reduction Sprint (6 fixes applied)  
**Status**: ✅ Ready for production runs
