# product-reader-ai

A [Strands](https://strandsagents.com/) agent that:

1. **Learns the profile schema** from example files in the target repository.
2. **Browses online webshops** using a headless Chromium browser and collects 15 representative products across categories.
3. **Generates a profile file and test scenarios** that match the repository's expected format.
4. **Commits both files to a new branch** via the [GitHub MCP server](https://github.com/github/github-mcp-server) and opens a pull request.
5. **Triggers the *Generate Baseline* workflow** and waits for it to complete.
6. **Verifies the baseline artifacts** by comparing `preview` field values against what was observed on the webshop; retries if there are mismatches.
7. **Triggers the *Accept Baseline* workflow** once the baseline looks correct, or leaves a PR comment for human review if it cannot be resolved automatically.

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | ≥ 3.12 |
| `github-mcp-server` | latest (see [Install GitHub MCP server](#install-github-mcp-server) below) |
| Docker, Podman, **or** Go ≥ 1.22 | one of these is needed to run `github-mcp-server` |
| AWS credentials | Bedrock access in `us-east-1` (or your chosen region) |

---

## Setup

```bash
# 1. Clone the repository
git clone https://github.com/szymek25/product-reader-ai.git
cd product-reader-ai

# 2. Install Python dependencies
pip install -e .

# 3. Install Playwright browsers
playwright install chromium

# 4. Install the GitHub MCP server  (choose one option)

# Option A – Docker
docker pull ghcr.io/github/github-mcp-server
# Then set in .env:  GITHUB_MCP_COMMAND=docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server

# Option B – Podman
podman pull ghcr.io/github/github-mcp-server
# Then set in .env:  GITHUB_MCP_COMMAND=podman run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server

# Option C – build from source with Go (binary goes on PATH automatically)
go install github.com/github/github-mcp-server/cmd/github-mcp-server@latest
# GITHUB_MCP_COMMAND can be left blank; the agent calls `github-mcp-server stdio`

# Option D – download a pre-built binary from GitHub Releases
#   https://github.com/github/github-mcp-server/releases
# Leave GITHUB_MCP_COMMAND blank if the binary is on your PATH, or set the full path:
#   GITHUB_MCP_COMMAND=/usr/local/bin/github-mcp-server stdio

# 5. Configure environment variables
cp .env.example .env
$EDITOR .env          # fill in GITHUB_TOKEN, TARGET_REPO, WEBSHOP_URLS, …
```

---

## Running the agent

```bash
python agent.py
```

Or via the installed script:

```bash
product-reader-ai
```

---

## Configuration

All configuration is done via environment variables (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | GitHub PAT with `repo` + `workflow` scopes | *required* |
| `TARGET_REPO` | `owner/repo` to commit files into | *required* |
| `WEBSHOP_URLS` | Comma-separated list of webshop URLs to browse | *required* |
| `BASE_BRANCH` | Branch to create the new branch from | `main` |
| `FEATURES_PATH` | Path in `TARGET_REPO` with product feature tests (profile schema reference) | `features/products` |
| `MOCKS_PATH` | Path in `TARGET_REPO` with mock HTML pages (test scenario structure reference) | `public/mock` |
| `PROFILES_PATH` | Output path in `TARGET_REPO` for generated profile files | `validation/profiles` |
| `TESTS_PATH` | Output path in `TARGET_REPO` for generated test scenario files | `validation/tests` |
| `GENERATE_BASELINE_WORKFLOW` | Workflow name that generates baseline artifacts | `Validation — Generate Baseline` |
| `ACCEPT_BASELINE_WORKFLOW` | Workflow name that accepts a validated baseline | `Validation — Accept Baseline` |
| `GITHUB_MCP_COMMAND` | Full launch command for the MCP server (see step 4) | *(calls `github-mcp-server stdio`)* |
| `BEDROCK_MODEL_ID` | Amazon Bedrock model ID | `us.anthropic.claude-sonnet-4-5-v1:0` |
| `AWS_REGION` | AWS region for Bedrock | `us-east-1` |
| `STRANDS_BROWSER_HEADLESS` | Run browser headless (`true`/`false`) | `true` |

---

## Agent workflow

```
read schema examples (FEATURES_PATH)
      │
      ▼
read mock pages (MOCKS_PATH)
      │
      ▼
browse webshop → collect 15 products across categories
      │
      ▼
derive slug (e.g. acme-store)
      │
      ├─► author {slug}.json  → commit to PROFILES_PATH
      └─► author {slug}.json  → commit to TESTS_PATH
      │
      ▼
create branch feature/{slug} + open pull request
      │
      ▼
trigger GENERATE_BASELINE_WORKFLOW ──► wait ──► download artifacts
      │                                               │
      │                                  preview values match website?
      │                                    ↙                    ↘
      │                           NO (retry ≤ 3×)             YES
      │                        fix profile, re-run      trigger ACCEPT_BASELINE_WORKFLOW
      │                               │                         │
      │                    still failing after 3×               ▼
      │                        leave PR comment             report success
      │
      ▼
  report result
```

---

## Project structure

```
product-reader-ai/
├── agent.py          # Main agent entry point
├── prompts.py        # System prompt and task prompt templates
├── pyproject.toml    # Project metadata and dependencies
├── .env.example      # Environment variable template
└── README.md
```

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```