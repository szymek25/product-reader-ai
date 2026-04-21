# product-reader-ai

A [Strands](https://strandsagents.com/) agent that:

1. **Browses online webshops** using a headless Chromium browser and extracts structured product data.
2. **Generates a `profile.json`** file from the collected data.
3. **Commits the file to a GitHub repository** via the [GitHub MCP server](https://github.com/github/github-mcp-server) on a new branch.
4. **Triggers a validation workflow** and waits for it to succeed.
5. **Downloads and inspects the workflow artifacts** to confirm quality.
6. **Triggers a publish workflow and opens a pull request** once validation passes.

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | ≥ 3.12 |
| `github-mcp-server` | latest (`npm install -g @github/mcp-server`) |
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

# 4. Install the GitHub MCP server
npm install -g @github/mcp-server

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
| `TARGET_REPO` | `owner/repo` to commit the profile into | *required* |
| `WEBSHOP_URLS` | Comma-separated list of webshop URLs to browse | *required* |
| `BASE_BRANCH` | Branch to create the new branch from | `main` |
| `NEW_BRANCH` | Name of the new feature branch | `feature/product-profile` |
| `VALIDATE_WORKFLOW` | Validation workflow filename or name | `validate.yml` |
| `PUBLISH_WORKFLOW` | Publish workflow filename or name | `publish.yml` |
| `BEDROCK_MODEL_ID` | Amazon Bedrock model ID | `us.anthropic.claude-sonnet-4-5-v1:0` |
| `AWS_REGION` | AWS region for Bedrock | `us-east-1` |
| `STRANDS_BROWSER_HEADLESS` | Run browser headless (`true`/`false`) | `true` |

---

## Agent workflow

```
browse webshops
      │
      ▼
generate profile.json
      │
      ▼
create branch in TARGET_REPO
      │
      ▼
commit profile.json
      │
      ▼
trigger VALIDATE_WORKFLOW ──► wait ──► check artifacts
      │                                      │
      │                               validation OK?
      │                                ↙         ↘
      │                              NO          YES
      │                           stop     trigger PUBLISH_WORKFLOW
      │                                          │
      │                                          ▼
      │                                  open pull request
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