"""
Local file-based persistence tools for the product-reader-ai agent.

All data is stored under a session directory in the system temp folder so
that a crashed or interrupted run can be resumed without re-browsing the
webshop or re-reading reference files.

Directory layout:
  <TMPDIR>/product-reader-ai/
    slug_registry.json              # webshop URL → slug mapping (persists across runs)
    <slug>/
      schema.json                   # learned profile + test-scenario schema
      products.json                 # 15 collected product records
      run_state.json                # current step, branch, PR URL, attempt count
      mismatches.jsonl              # one JSON object per line – mismatch log
"""

import json
import tempfile
from pathlib import Path

from strands import tool

# Root directory for all persisted state
_STATE_ROOT = Path(tempfile.gettempdir()) / "product-reader-ai"


def _slug_dir(slug: str) -> Path:
    """Return (and create) the per-slug state directory."""
    d = _STATE_ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────


@tool
def save_schema(slug: str, schema_json: str) -> str:
    """
    Persist the learned profile/test-scenario schema for a webshop slug.

    Call this immediately after reading the reference files in STEP 0 so the
    schema does not have to be re-fetched if the run is interrupted.

    Args:
        slug: The webshop slug (e.g. "acme-store").
        schema_json: A JSON string containing the learned schema data.
            Recommended structure:
              {
                "profile_schema": { ... },   // field names, types, nesting
                "test_scenario_structure": { ... }  // selectors, expected-value conventions
              }

    Returns:
        Confirmation message with the file path.
    """
    path = _slug_dir(slug) / "schema.json"
    path.write_text(schema_json, encoding="utf-8")
    return f"Schema saved to {path}"


@tool
def load_schema(slug: str) -> str:
    """
    Load the previously persisted schema for a webshop slug.

    Call this at the start of a run before STEP 0.  If the result is non-empty
    you can skip reading the reference files again.

    Args:
        slug: The webshop slug (e.g. "acme-store").

    Returns:
        The JSON string that was passed to save_schema, or an empty string if
        no schema has been saved for this slug yet.
    """
    path = _slug_dir(slug) / "schema.json"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# Collected products
# ─────────────────────────────────────────────


@tool
def save_products(slug: str, products_json: str) -> str:
    """
    Persist the 15 collected product records for a webshop slug.

    Call this immediately after STEP 1 (browsing) so the data is not lost if
    the run crashes before the files are committed.

    Args:
        slug: The webshop slug (e.g. "acme-store").
        products_json: A JSON string – an array of product objects, each with
            at minimum: name, short_description, long_description, images,
            features.  Additional fields (price, sku, url, …) are welcome.

    Returns:
        Confirmation message with the file path.
    """
    path = _slug_dir(slug) / "products.json"
    path.write_text(products_json, encoding="utf-8")
    return f"Products saved to {path}"


@tool
def load_products(slug: str) -> str:
    """
    Load previously collected product records for a webshop slug.

    Call this at the start of STEP 1.  If the result is non-empty you can skip
    browsing the webshop again.

    Args:
        slug: The webshop slug (e.g. "acme-store").

    Returns:
        The JSON string that was passed to save_products, or an empty string if
        no products have been saved for this slug yet.
    """
    path = _slug_dir(slug) / "products.json"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# Run state
# ─────────────────────────────────────────────


@tool
def save_run_state(slug: str, state_json: str) -> str:
    """
    Persist the current run state for a webshop slug.

    Update this after every major step so a restarted run can continue from
    where it left off.

    Args:
        slug: The webshop slug (e.g. "acme-store").
        state_json: A JSON string with at least the following fields:
              {
                "step": 5,               // last completed step number
                "branch": "feature/acme-store",
                "pr_url": "https://github.com/…",   // set once PR is opened
                "baseline_attempts": 1   // number of generate-baseline runs so far
              }

    Returns:
        Confirmation message with the file path.
    """
    path = _slug_dir(slug) / "run_state.json"
    path.write_text(state_json, encoding="utf-8")
    return f"Run state saved to {path}"


@tool
def load_run_state(slug: str) -> str:
    """
    Load the persisted run state for a webshop slug.

    Call this at the very start of a run.  If the result is non-empty, use the
    `step` field to decide which step to resume from.

    Args:
        slug: The webshop slug (e.g. "acme-store").  If the slug is not yet
            known, pass an empty string — an empty string is returned.

    Returns:
        The JSON string that was passed to save_run_state, or an empty string
        if no state has been saved for this slug yet.
    """
    if not slug:
        return ""
    path = _slug_dir(slug) / "run_state.json"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# Mismatch log
# ─────────────────────────────────────────────


@tool
def log_mismatch(slug: str, entry_json: str) -> str:
    """
    Append a mismatch entry to the log for a webshop slug.

    Call this in STEP 8 whenever a baseline verification fails, before
    attempting a correction.

    Args:
        slug: The webshop slug (e.g. "acme-store").
        entry_json: A JSON object string describing the mismatch, e.g.:
              {
                "attempt": 1,
                "product_url": "https://…",
                "field": "short_description",
                "expected": "Karma dla kota …",
                "actual": "…",
                "correction": "Updated field X to …"
              }

    Returns:
        Confirmation message with the file path.
    """
    path = _slug_dir(slug) / "mismatches.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry_json.rstrip("\n") + "\n")
    return f"Mismatch logged to {path}"


@tool
def load_mismatch_log(slug: str) -> str:
    """
    Load the full mismatch log for a webshop slug (JSONL format).

    Each line is a JSON object as written by log_mismatch.

    Args:
        slug: The webshop slug (e.g. "acme-store").

    Returns:
        The JSONL text of all logged mismatches, or an empty string if none.
    """
    path = _slug_dir(slug) / "mismatches.jsonl"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# Slug registry
# ─────────────────────────────────────────────


@tool
def register_slug(url: str, slug: str) -> str:
    """
    Register a webshop URL → slug mapping in the persistent slug registry.

    Call this in STEP 2 once the slug has been decided, so future runs for the
    same URL reuse the same slug automatically.

    Args:
        url: The canonical webshop URL (e.g. "https://fluffypet.pl/").
        slug: The derived slug (e.g. "fluffypet").

    Returns:
        Confirmation message.
    """
    _STATE_ROOT.mkdir(parents=True, exist_ok=True)
    registry_path = _STATE_ROOT / "slug_registry.json"
    registry: dict[str, str] = {}
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            registry = {}
    registry[url] = slug
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"Registered '{url}' → '{slug}'"


@tool
def lookup_slug(url: str) -> str:
    """
    Look up the slug previously registered for a webshop URL.

    Call this at the very start of a run, before deriving a new slug in STEP 2.
    If a slug is returned, use it (and check load_run_state for resume info).

    Args:
        url: The canonical webshop URL (e.g. "https://fluffypet.pl/").

    Returns:
        The previously registered slug, or an empty string if none is found.
    """
    registry_path = _STATE_ROOT / "slug_registry.json"
    if not registry_path.exists():
        return ""
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        return registry.get(url, "")
    except json.JSONDecodeError:
        return ""
