"""
Local file-based persistence tools for the product-reader-ai agent.

All data is stored under a session directory in the system temp folder so
that a crashed or interrupted run can be resumed without re-browsing the
webshop or re-reading reference files.

Directory layout:
  <TMPDIR>/product-reader-ai/
    slug_registry.json              # webshop URL → slug mapping (persists across runs)
    <slug>/
      selectors.json                # CSS selectors derived by analyze_product_page
      products.json                 # 15 collected product records
      run_state.json                # current step, branch, PR URL, attempt count
      mismatches.jsonl              # one JSON object per line – mismatch log
"""

import json
import tempfile
from pathlib import Path

from strands import tool

# Root directory for all persisted state
STATE_ROOT = Path(tempfile.gettempdir()) / "product-reader-ai"
_STATE_ROOT = STATE_ROOT  # backward-compat alias


def _slug_dir(slug: str) -> Path:
    """Return (and create) the per-slug state directory."""
    d = _STATE_ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────


@tool
def save_schema(schema_json: str) -> str:
    """
    Persist the shared profile/test-scenario schema.

    The schema is not webshop-specific — it is derived from the repository’s
    reference files and reused across all slugs.  Call this once in STEP 0
    so the schema does not have to be re-fetched on subsequent runs.

    Args:
        schema_json: A JSON string containing the learned schema data.
            Recommended structure::

                {
                  "profile_schema": { ... },
                  "test_scenario_structure": { ... }
                }

    Returns:
        Confirmation message with the file path.
    """
    _STATE_ROOT.mkdir(parents=True, exist_ok=True)
    path = _STATE_ROOT / "schema.json"
    path.write_text(schema_json, encoding="utf-8")
    return f"Schema saved to {path}"


@tool
def load_schema() -> str:
    """
    Load the shared profile/test-scenario schema.

    Call this at the start of every run before STEP 0.  If the result is
    non-empty the schema is already known and STEP 0 can be skipped.

    Returns:
        The JSON string that was passed to save_schema, or an empty string if
        no schema has been saved yet.
    """
    path = _STATE_ROOT / "schema.json"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# Product links (URL list)
# ─────────────────────────────────────────────


@tool
def save_product_links(slug: str, links_json: str) -> str:
    """
    Persist the list of product URLs discovered in STEP 1a.

    Call this immediately after find_product_links so the URL list survives a
    run interruption and the discovery sub-agent does not need to re-crawl.

    Args:
        slug:       The webshop slug (e.g. "acme-store").
        links_json: JSON array string of absolute product URL strings.

    Returns:
        Confirmation message with the file path.
    """
    path = _slug_dir(slug) / "product_links.json"
    path.write_text(links_json, encoding="utf-8")
    return f"Product links saved to {path}"


@tool
def load_product_links(slug: str) -> str:
    """
    Load the previously saved product URL list for a webshop slug.

    Call this at the start of STEP 1 before find_product_links.  If the result
    is non-empty, skip find_product_links entirely.

    Args:
        slug: The webshop slug (e.g. "acme-store").

    Returns:
        The JSON array string that was passed to save_product_links, or an
        empty string if no link list has been saved for this slug yet.
    """
    path = _slug_dir(slug) / "product_links.json"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# Page selectors
# ─────────────────────────────────────────────


@tool
def save_selectors(slug: str, selectors_json: str) -> str:
    """
    Persist the CSS selectors derived by analyze_product_page for a webshop.

    Call this immediately after the first call to analyze_product_page so the
    selectors survive a run interruption and do not need to be re-derived.

    Args:
        slug:           The webshop slug (e.g. "acme-store").
        selectors_json: The JSON array string returned by analyze_product_page.

    Returns:
        Confirmation message with the file path.
    """
    path = _slug_dir(slug) / "selectors.json"
    path.write_text(selectors_json, encoding="utf-8")
    return f"Selectors saved to {path}"


@tool
def load_selectors(slug: str) -> str:
    """
    Load the CSS selectors previously saved for a webshop.

    Call this at the start of STEP 1 before processing product pages.  If the
    result is non-empty, skip the analyze_product_page call entirely.

    Args:
        slug: The webshop slug (e.g. "acme-store").

    Returns:
        The JSON array string that was passed to save_selectors, or an empty
        string if no selectors have been saved for this slug yet.
    """
    path = _slug_dir(slug) / "selectors.json"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# Collected products
# ─────────────────────────────────────────────


@tool
def add_product(slug: str, product_json: str) -> str:
    """
    Append a single product record to the saved products list for a webshop slug.

    Call this immediately after extracting each product in STEP 1.  The tool
    reads the current list from disk, appends the new product, and writes it
    back — so the agent never needs to keep the growing array in its context.

    Args:
        slug: The webshop slug (e.g. "acme-store").
        product_json: A JSON string of a single product object with at minimum:
            name, short_description, long_description, images, features.
            Additional fields (price, sku, url, …) are welcome.

    Returns:
        Confirmation message including the new total product count.
    """
    path = _slug_dir(slug) / "products.json"
    existing: list = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    existing.append(json.loads(product_json))
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"Product appended. Total saved: {len(existing)}"


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
def resolve_slug(url: str) -> str:
    """
    Return the canonical slug for *url*, creating and persisting one if needed.

    Resolution order:
      1. Check the slug registry — return the stored slug if present.
      2. Derive a slug from the URL hostname (strip ``www.``, replace ``.`` with
         ``-``, lowercase, keep only ``[a-z0-9-]``).
      3. Register the new mapping so all future calls return the same slug.

    Always prefer this over manual slug derivation: it is
    deterministic, consistent across runs, and automatically persists the mapping.

    Args:
        url: Any URL belonging to the webshop (scheme + host is sufficient,
             e.g. ``"https://balticapets.pl"`` or a full product URL).

    Returns:
        A filesystem-safe, stable slug string (e.g. ``"balticapets-pl"``).
    """
    import re
    from urllib.parse import urlparse

    # Normalise to bare URL so https://balticapets.pl and
    # https://balticapets.pl/products/foo both resolve to the same slug.
    parsed = urlparse(url)
    canonical_url = f"{parsed.scheme}://{parsed.netloc}"

    # 1 — registry look-up
    registry_path = _STATE_ROOT / "slug_registry.json"
    registry: dict[str, str] = {}
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            registry = {}

    if canonical_url in registry:
        return registry[canonical_url]

    # Also accept an exact match on the raw URL (legacy entries).
    if url in registry:
        return registry[url]

    # 2 — derive a canonical slug
    host = parsed.netloc.lower()
    host = re.sub(r"^www\.", "", host)        # strip www.
    host = host.replace(".", "-")             # dots → hyphens
    slug = re.sub(r"[^a-z0-9-]", "", host)   # keep only safe chars
    slug = re.sub(r"-{2,}", "-", slug).strip("-") or "unknown"

    # 3 — persist so every future call returns the same slug
    _STATE_ROOT.mkdir(parents=True, exist_ok=True)
    registry[canonical_url] = slug
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")

    return slug


# ─────────────────────────────────────────────
# Generic local file I/O (used by local-flow writers)
# ─────────────────────────────────────────────


@tool
def write_file_to_disk(path: str, content: str) -> str:
    """
    Write *content* to *path* on the local filesystem.

    Creates parent directories as needed.  Used in local-flow mode so that
    profile and test-scenario JSON files are written to disk rather than
    committed to GitHub.

    Args:
        path:    Absolute path of the file to write.
        content: UTF-8 text content to write.

    Returns:
        Confirmation string with the file path and byte count.
    """
    from pathlib import Path as _Path

    p = _Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content.encode())} bytes to {path}"


@tool
def read_file_from_disk(path: str) -> str:
    """
    Read and return the content of a local file.

    Used in local-flow mode so the test-scenario writer can read the profile
    JSON that was written by ``write_profile_local`` without going to GitHub.

    Args:
        path: Absolute path of the file to read.

    Returns:
        UTF-8 text content of the file, or an error message if not found.
    """
    from pathlib import Path as _Path

    p = _Path(path)
    if not p.exists():
        return f"File not found: {path}"
    return p.read_text(encoding="utf-8")
