# product-reader-ai

A [Strands](https://strandsagents.com/) multi-agent system that crawls webshops, extracts product data, and generates **profile JSON** and **test scenario JSON** files.

---

## Development phases

| Phase | Status | Description |
|-------|--------|-------------|
| **Phase 1 — Local** | ✅ Active | Run fully locally; artifacts written to disk. No GitHub required. |
| **Phase 2 — AWS** | Planned | Deploy agent to AWS (Lambda / ECS). |
| **Phase 3 — GitHub** | Planned | Commit artifacts to GitHub, open PRs, trigger validation workflows. |

> **Current focus is Phase 1.** Set `LOCAL_FLOW=true` in your `.env` and the agent writes everything to disk without touching GitHub.

---

## How it works (local flow)

1. **Discover products** — crawls the webshop via HTTP, finds 15 representative product URLs.
2. **Analyse page structure** — derives CSS selectors for each product data field (name, description, images, attributes).
3. **Scrape all products** — extracts structured data from every URL using the saved selectors.
4. **Write profile** — generates `profile.json` conforming to the schema and saves it to disk.
5. **Write test scenarios** — generates `tests.json` (one entry per product) and saves it to disk.

Output lands in `<TMPDIR>/product-reader-ai/<slug>/`.

---

## Prerequisites

| Tool | Version | Required for |
|------|---------|-------------|
| Python | ≥ 3.12 | always |
| AWS credentials | Bedrock access | always (LLM calls) |
| `github-mcp-server` | latest | Phase 3 only |

---

## Setup

```bash
# 1. Clone the repository
git clone https://github.com/szymek25/product-reader-ai.git
cd product-reader-ai

# 2. Install Python dependencies
pip install -e .

# 3. Configure environment variables
cp .env.example .env
$EDITOR .env
```

Minimum `.env` for local mode:
```env
LOCAL_FLOW=true
WEBSHOP_URLS=https://example-shop.com
AWS_REGION=us-east-1
```

---

## Running the agent

```bash
# Local mode — no GitHub needed
LOCAL_FLOW=true python agent.py

# Or via the installed script
product-reader-ai
```

Artifacts are written to:
```
/tmp/product-reader-ai/<slug>/
  ├── profile.json     ← product profile
  ├── tests.json       ← test scenarios
  ├── products.json    ← raw scraped data
  ├── selectors.json   ← CSS selectors
  └── product_links.json
```

---

## Configuration

All configuration is done via environment variables (see `.env.example`):

### Always required

| Variable | Description | Default |
|----------|-------------|---------|
| `WEBSHOP_URLS` | Comma-separated list of webshop URLs to process | *required* |
| `AWS_REGION` | AWS region for Bedrock | `us-east-1` |
| `BEDROCK_MODEL_ID` | Amazon Bedrock model ID | `us.anthropic.claude-sonnet-4-5-v1:0` |

### Local flow

| Variable | Description | Default |
|----------|-------------|---------|
| `LOCAL_FLOW` | Set to `true` to write artifacts locally and skip all GitHub steps | `false` |

### Phase 3 (GitHub flow) — not needed in local mode

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | GitHub PAT with `repo` + `workflow` scopes | *required* |
| `TARGET_REPO` | `owner/repo` to commit files into | *required* |
| `BASE_BRANCH` | Branch to create the new branch from | `main` |
| `PROFILES_PATH` | Output path in `TARGET_REPO` for generated profile files | `validation/profiles` |
| `TESTS_PATH` | Output path in `TARGET_REPO` for generated test scenario files | `validation/tests` |
| `GENERATE_BASELINE_WORKFLOW` | Workflow name that generates baseline artifacts | `Validation — Generate Baseline` |
| `ACCEPT_BASELINE_WORKFLOW` | Workflow name that accepts a validated baseline | `Validation — Accept Baseline` |
| `GITHUB_MCP_COMMAND` | Full launch command for the GitHub MCP server | *(calls `github-mcp-server stdio`)* |

---

## Agent workflow

### Local flow (`LOCAL_FLOW=true`)

```
crawl webshop via HTTP → collect 15 products
      │
      ▼
analyse one product page → derive CSS selectors
      │
      ▼
scrape all 15 products using saved selectors
      │
      ├─► write  <slug>/profile.json  (to disk)
      └─► write  <slug>/tests.json    (to disk)
```

### GitHub flow (`LOCAL_FLOW=false`, Phase 3)

```
crawl + scrape (same as above)
      │
      ▼
create branch feature/{slug} + commit profile + commit tests
      │
      ▼
open pull request
      │
      ▼
trigger GENERATE_BASELINE_WORKFLOW → verify results → retry up to 3×
      │
   passed?  ──► trigger ACCEPT_BASELINE_WORKFLOW → report success
   failed?  ──► post PR comment with mismatch details
```

---

## Token usage

| Mode | Tokens per run (measured) | Notes |
|------|--------------------------|-------|
| Local flow | ~200k | No GitHub MCP calls |
| GitHub flow | ~12M | GitHub MCP responses (file reads, search results, commits) dominate |

The 60× difference comes entirely from GitHub MCP tool responses accumulating in the orchestrator context. Local mode bypasses all of that.

---

## Project structure

```
product-reader-ai/
├── agent.py                  # Orchestrator entry point
├── context.py                # Shared MCP client holder
├── state.py                  # Disk I/O tools
├── prompts.py                # System + task prompt templates
├── model_factory.py          # Bedrock model config
├── schemas.py                # Embedded profile + test schemas
├── product_links_agent.py    # Discover product URLs
├── product_page_agent.py     # Derive CSS selectors from a product page
├── scraper_agent.py          # Extract product data from all URLs
├── profile_writer_agent.py   # Write profile JSON (local + GitHub variants)
├── test_writer_agent.py      # Write test scenarios JSON (local + GitHub variants)
├── validation_agent.py       # Run baseline workflows + verify (Phase 3)
├── pyproject.toml
├── README.md
├── CLAUDE.md                 # Developer reference
└── tests/
    └── test_agent.py
```

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```