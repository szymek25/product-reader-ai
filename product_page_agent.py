"""
Specialized sub-agent for analysing a single product page.

Pipeline
────────
1. extract_page_elements(html)
     → flat JSON array of every meaningful element
       {"index":0,"tag":"h1","text":"iPhone 15 Pro","classes":["title"],"id":""}

2. The sub-agent's LLM classifies each element as one of:
     product_name | short_description | description |
     gallery_image | attribute_table  | attribute

3. extract_element_selectors(html, classified_elements_json)
     → CSS selector, extraction type (TEXT / HTML / LINK), surrounding HTML,
       and sample text for every classified element

4. The returned JSON is handed back to the main agent in agent.py, which uses
   it to write the profile file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag
from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager

from model_factory import build_model, product_page_agent_model_id
from state import resolve_slug as _slug_from_url

# Root state directory — mirrors state.py so artifacts live alongside other slug data.
_STATE_ROOT = Path(tempfile.gettempdir()) / "product-reader-ai"

# Per-run URL → HTML cache so a page is only fetched once per analyze call.
_html_cache: dict[str, str] = {}

# Per-run URL → full element list saved to disk (artifact path)
_artifact_cache: dict[str, Path] = {}

# HTTP request timeout in seconds
_HTTP_TIMEOUT = 15

# How many elements the LLM sees — enough to classify without blowing context.
# The full list is saved to disk; build_selectors reads from there.
_LLM_ELEMENT_CAP = 80

# Tag priority: lower = more important, shown first / within cap.
_TAG_PRIORITY: dict[str, int] = {
    "h1": 0, "h2": 1, "h3": 2, "h4": 3, "h5": 4, "h6": 5,
    "td": 6, "th": 7, "p": 8, "img": 9,
    "li": 10, "a": 11,
}

# HTML tags that carry product-relevant content
_CONTENT_TAGS = frozenset(
    ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "img", "a"]
)

# Extraction-type per classification role
_ROLE_TYPE: dict[str, str] = {
    "product_name": "TEXT",
    "short_description": "TEXT",
    "description": "HTML",
    "gallery_image": "LINK",
    "attribute_table": "TEXT",
    "attribute": "TEXT",
}

# Maximum characters kept for text snippets / surrounding HTML in tool output
_MAX_TEXT = 300
_MAX_SURROUNDING = 2_000


# ── Private helpers ─────────────────────────────────────────────────────────


def _elements_dir(slug: str) -> Path:
    """Return (and create) the page-elements directory for *slug*."""
    d = _STATE_ROOT / slug / "page_elements"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _artifact_path(url: str, slug: str) -> Path:
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    return _elements_dir(slug) / f"{url_hash}_elements.json"


def _save_elements_artifact(url: str, slug: str, elements: list[dict[str, Any]]) -> Path:
    path = _artifact_path(url, slug)
    path.write_text(json.dumps(elements, ensure_ascii=False, indent=2), encoding="utf-8")
    _artifact_cache[url] = path
    return path


def _load_elements_artifact(url: str, slug: str) -> list[dict[str, Any]] | None:
    path = _artifact_cache.get(url) or _artifact_path(url, slug)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _fetch_html(url: str) -> str:
    """Fetch *url* and return HTML, using the per-run cache."""
    if url in _html_cache:
        return _html_cache[url]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; product-reader-ai/1.0; "
            "+https://github.com/szymek25/product-reader-ai)"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    try:
        response = httpx.get(
            url, headers=headers, timeout=_HTTP_TIMEOUT, follow_redirects=True
        )
        response.raise_for_status()
        _html_cache[url] = response.text
        return response.text
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"HTTP {exc.response.status_code} fetching {url}"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Request error fetching {url}: {exc}") from exc


# ── Private helpers ────────────────────────────────────────────────────────


def _element_text(elem: Tag) -> str:
    """Return the most representative text for an element."""
    if elem.name == "img":
        return elem.get("alt", "") or elem.get("src", "")
    if elem.name == "a":
        t = elem.get_text(strip=True)
        return t or elem.get("href", "")
    return elem.get_text(strip=True)


def _iter_content_elements(soup: BeautifulSoup):
    """Yield (index, Tag) for every meaningful element in document order."""
    idx = 0
    for elem in soup.find_all(_CONTENT_TAGS):
        if _element_text(elem):
            yield idx, elem
            idx += 1


def _build_css_selector(elem: Tag) -> str:
    """
    Build a reasonably specific CSS selector for *elem*.

    Strategy (first match wins):
      1. If element has an id  →  #id
      2. If element has classes  →  tag.class1.class2  (≤ 3 classes)
      3. If parent has an id  →  #parent-id tag[.classes]
      4. If parent has classes  →  .parent-class tag[.classes]
      5. Fall back to plain tag name
    """
    tag: str = elem.name
    elem_id: str = elem.get("id", "")
    classes: list[str] = elem.get("class", [])

    if elem_id:
        return f"#{elem_id}"

    local = tag
    if classes:
        local = tag + "." + ".".join(classes[:3])

    parent = elem.parent
    if parent and parent.name not in {None, "html", "body", "[document]"}:
        parent_id: str = parent.get("id", "")
        parent_classes: list[str] = parent.get("class", [])
        if parent_id:
            return f"#{parent_id} {local}"
        if parent_classes:
            return f".{parent_classes[0]} {local}"

    return local


# ── Strands tools ──────────────────────────────────────────────────────────


@tool
def fetch_and_extract_elements(url: str, slug: str = "") -> str:
    """
    Fetch a product page and return a flat JSON array of meaningful elements.

    Fetches the page via HTTP internally — never exposes raw HTML to the LLM.
    The full element list is saved as an artifact under the slug’s state
    directory (e.g. ``<TMPDIR>/product-reader-ai/<slug>/page_elements/``).
    Every entry describes one visible element that might carry product data::

        {
            "index": 0,
            "tag": "h1",
            "text": "iPhone 15 Pro",
            "classes": ["text-body-2xl"],
            "id": ""
        }

    Call this first to get the numbered element list, then classify each entry
    and pass the classifications to build_selectors.

    Args:
        url:  Absolute URL of the product page.
        slug: Webshop slug used to organise artifacts alongside other state
              (e.g. products.json, run_state.json).  Defaults to a name
              derived from the URL hostname when omitted.

    Returns:
        JSON array string – one object per meaningful element (capped at
        {_LLM_ELEMENT_CAP} for context efficiency).
    """
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    all_elements: list[dict[str, Any]] = []
    for idx, elem in _iter_content_elements(soup):
        text = _element_text(elem)
        if len(text) > _MAX_TEXT:
            text = text[:_MAX_TEXT] + "…"
        all_elements.append(
            {
                "index": idx,
                "tag": elem.name,
                "text": text,
                "classes": elem.get("class", []),
                "id": elem.get("id", ""),
            }
        )

    # Persist full element list as artifact — build_selectors reads from here.
    effective_slug = slug or _slug_from_url(url)
    artifact_path = _save_elements_artifact(url, effective_slug, all_elements)

    # Return a priority-sorted, capped subset so the LLM stays within context.
    # Indices are preserved so build_selectors can look them up in the artifact.
    prioritised = sorted(all_elements, key=lambda e: (_TAG_PRIORITY.get(e["tag"], 99), e["index"]))
    llm_view = prioritised[:_LLM_ELEMENT_CAP]
    # Re-sort by original index so the LLM sees document order.
    llm_view.sort(key=lambda e: e["index"])

    total = len(all_elements)
    shown = len(llm_view)
    note = (
        f"// Showing {shown} of {total} elements (prioritised by tag importance). "
        f"Full list saved to: {artifact_path}"
    )
    return note + "\n" + json.dumps(llm_view, ensure_ascii=False)


@tool
def build_selectors(url: str, classified_elements_json: str, slug: str = "") -> str:
    """
    Resolve classified elements to CSS selectors and surrounding HTML.

    Uses the same page fetched by fetch_and_extract_elements (cached by URL).
    Never receives raw HTML as a parameter.

    For each entry in *classified_elements_json*, the tool locates the
    corresponding element and returns:

    * ``selector``        – CSS selector that targets the element
    * ``type``            – TEXT | HTML | LINK
    * ``surrounding_html``– the element's direct parent HTML (capped at 2 000 chars)
    * ``sample_text``     – a short text sample from the element

    Args:
        url:
            Absolute URL of the product page (same URL passed to
            fetch_and_extract_elements).
        classified_elements_json:
            JSON array produced by the LLM after reviewing
            fetch_and_extract_elements output, e.g.::

                [
                    {"index": 0,  "role": "product_name"},
                    {"index": 4,  "role": "short_description"},
                    {"index": 7,  "role": "description"},
                    {"index": 12, "role": "gallery_image"},
                    {"index": 20, "role": "attribute_table"},
                    {"index": 21, "role": "attribute"}
                ]
        slug:
            Webshop slug (same value passed to fetch_and_extract_elements).

            Valid roles: ``product_name``, ``short_description``,
            ``description``, ``gallery_image``, ``attribute_table``,
            ``attribute``.

    Returns:
        JSON array – one object per classified element::

            {
                "role":            "product_name",
                "selector":        "h1.text-body-2xl",
                "type":            "TEXT",
                "surrounding_html": "<div class=\\"product-header\\">…</div>",
                "sample_text":     "iPhone 15 Pro"
            }
    """
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    classifications: list[dict[str, Any]] = json.loads(classified_elements_json)

    # Build index → Tag mapping by traversing the DOM in document order.
    # This matches the indexing used by fetch_and_extract_elements.
    index_map: dict[int, Tag] = {
        idx: elem for idx, elem in _iter_content_elements(soup)
    }

    result: list[dict[str, Any]] = []
    for item in classifications:
        idx = item.get("index")
        role: str = item.get("role", "")
        elem = index_map.get(idx)
        if elem is None:
            continue

        selector = _build_css_selector(elem)

        # For gallery images: if the element is <img>, prefer the parent <a>
        # because profiles track the link that wraps the image.
        if role == "gallery_image" and elem.name == "img":
            parent = elem.parent
            if parent and parent.name == "a":
                selector = _build_css_selector(parent)

        content_type = _ROLE_TYPE.get(role, "TEXT")

        # Surrounding HTML: direct parent gives structural context
        parent = elem.parent
        if parent and parent.name not in {"html", "body", None}:
            surrounding = str(parent)
        else:
            surrounding = str(elem)
        if len(surrounding) > _MAX_SURROUNDING:
            surrounding = str(elem)

        result.append(
            {
                "role": role,
                "selector": selector,
                "type": content_type,
                "surrounding_html": surrounding,
                "sample_text": _element_text(elem)[:200],
            }
        )

    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Sub-agent definition ────────────────────────────────────────────────────

PRODUCT_PAGE_SYSTEM_PROMPT = """\
You are a specialized product page analyser.

Your job: given a product page URL and an optional webshop slug, identify the
CSS selectors for each product data category so that a scraping profile can be
generated.
NEVER handle raw HTML — use the provided tools which fetch pages internally.

Strict workflow — follow exactly:
1. Call fetch_and_extract_elements(url, slug) to receive a numbered list of elements.
   Pass the slug value you were given (it organises artifacts with the rest of the
   webshop state such as products.json and run_state.json).
2. Review the list and assign each relevant element index to a role:
     product_name      – main product title (typically the h1)
     short_description – brief summary near the title
     description       – full long description section
     gallery_image     – product images or the anchors wrapping them
     attribute_table   – the container of the specification / attribute table
     attribute         – individual label cells inside the attribute table
   Multiple indices may share the same role (e.g. several gallery images,
   several attribute rows).
3. Call build_selectors(url, classified_elements_json, slug) where
   classified_elements_json is a JSON array:
       [{"index": N, "role": "role_name"}, ...]
4. Output the JSON returned by build_selectors verbatim.
   Do NOT add prose, markdown formatting, or extra keys.
"""


def build_product_page_agent() -> Agent:
    """Construct the product-page analysis sub-agent."""
    model = build_model(product_page_agent_model_id(), max_tokens=4096)
    return Agent(
        model=model,
        system_prompt=PRODUCT_PAGE_SYSTEM_PROMPT,
        tools=[fetch_and_extract_elements, build_selectors],
        conversation_manager=SlidingWindowConversationManager(
            window_size=10, should_truncate_results=True
        ),
    )


# ── Public tool exposed to the main agent ──────────────────────────────────


@tool
def analyze_product_page(url: str, slug: str = "") -> str:
    """
    Analyse a product page and return CSS selectors for every product data field.

    Fetches the page internally — the main agent only passes the URL so no
    raw HTML ever enters the main context window.

    Internally this tool runs a specialised sub-agent that:
      1. Fetches the page and extracts all meaningful elements (no HTML to LLM).
      2. Classifies them as product_name, short_description, description,
         gallery_image, attribute_table, or attribute.
      3. Resolves each classification to a CSS selector, extraction type
         (TEXT / HTML / LINK), surrounding HTML context, and sample text.

    Artifacts (full element lists) are stored under
    ``<TMPDIR>/product-reader-ai/<slug>/page_elements/`` alongside the
    webshop’s products.json, schema.json, and run_state.json.

    Use the returned selectors directly when writing the profile JSON file.

    Args:
        url:  Absolute URL of the product page.
        slug: Webshop slug (same value used in state.py, e.g. "fluffypet-pl").
              Derived from the URL hostname when omitted.

    Returns:
        JSON array – one object per classified element, e.g.::

            [
              {"role": "product_name",      "selector": "h1.text-body-2xl",
               "type": "TEXT",              "surrounding_html": "…", "sample_text": "…"},
              {"role": "short_description", "selector": "p.line-clamp",
               "type": "TEXT",              "surrounding_html": "…", "sample_text": "…"},
              {"role": "description",       "selector": "#product-description .content",
               "type": "HTML",              "surrounding_html": "…", "sample_text": "…"},
              {"role": "gallery_image",     "selector": ".product-gallery__link",
               "type": "LINK",              "surrounding_html": "…", "sample_text": "…"}
            ]
    """
    _html_cache.clear()  # fresh cache per analyze call
    effective_slug = slug or _slug_from_url(url)
    agent = build_product_page_agent()
    prompt = (
        f"Product page URL: {url}\n"
        f"Webshop slug: {effective_slug}\n"
        "Follow the workflow in your system prompt. "
        f"Start by calling fetch_and_extract_elements({url!r}, {effective_slug!r})."
    )
    result = str(agent(prompt))
    # Strip bulk fields that are not needed after selector derivation.
    try:
        import json as _json
        slim = [
            {"role": s.get("role"), "selector": s.get("selector"), "type": s.get("type")}
            for s in _json.loads(result)
        ]
        return _json.dumps(slim, ensure_ascii=False)
    except (Exception,):
        return result


# ── Product data extraction ────────────────────────────────────────────────

_FIELD_ROLES = {
    "product_name",
    "short_description",
    "description",
    "gallery_image",
    "attribute_table",
    "attribute",
}


@tool
def extract_product_data(url: str, selectors_json: str) -> str:
    """
    Fetch a product page and extract structured data using saved CSS selectors.

    Fetches the page via HTTP internally — no HTML is ever passed to the LLM.
    Use this for every product URL after the selectors have been derived and
    saved by analyze_product_page / save_selectors.

    Args:
        url:            Absolute URL of the product page.
        selectors_json: JSON array previously returned by analyze_product_page
                        (or loaded via load_selectors), e.g.::

                            [
                              {"role": "product_name",  "selector": "h1.title",
                               "type": "TEXT", ...},
                              {"role": "gallery_image", "selector": "a.thumb",
                               "type": "LINK", ...}
                            ]

    Returns:
        JSON object with extracted product fields::

            {
                "url": "https://…",
                "name": "Product Title",
                "short_description": "…",
                "description": "…",
                "image_urls": ["https://…", …],
                "attributes": [{"label": "…", "value": "…"}, …]
            }

        Missing fields are omitted rather than returned as null.
    """
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    selectors: list[dict[str, Any]] = json.loads(selectors_json)

    result: dict[str, Any] = {"url": url}
    image_urls: list[str] = []
    attributes: list[dict[str, str]] = []

    for entry in selectors:
        role: str = entry.get("role", "")
        selector: str = entry.get("selector", "")
        kind: str = entry.get("type", "TEXT")
        if not selector or role not in _FIELD_ROLES:
            continue

        elements = soup.select(selector)
        if not elements:
            continue

        if role == "gallery_image":
            for elem in elements:
                if elem.name == "a":
                    href = elem.get("href", "")
                    if href:
                        image_urls.append(href)
                elif elem.name == "img":
                    src = elem.get("src", "")
                    if src:
                        image_urls.append(src)
                else:
                    src = elem.get("src", "") or elem.get("href", "")
                    if src:
                        image_urls.append(src)

        elif role == "attribute":
            # Attributes come in pairs (label / value) or as table rows.
            # Try to group by parent row first.
            parents_seen: set[int] = set()
            for elem in elements:
                parent = elem.parent
                pid = id(parent)
                if pid in parents_seen:
                    continue
                parents_seen.add(pid)
                cells = parent.find_all(True, recursive=False) if parent else [elem]
                texts = [c.get_text(strip=True) for c in cells if c.get_text(strip=True)]
                if len(texts) >= 2:
                    attributes.append({"label": texts[0], "value": texts[1]})
                elif texts:
                    attributes.append({"label": texts[0], "value": ""})

        elif role == "attribute_table":
            # Skip — we extract attributes from individual attribute cells above.
            continue

        else:
            # TEXT / HTML scalar roles
            elem = elements[0]
            if kind == "HTML":
                text = elem.decode_contents().strip()
            elif kind == "LINK":
                text = elem.get("href", "") or elem.get_text(strip=True)
            else:
                text = elem.get_text(strip=True)

            field_map = {
                "product_name": "name",
                "short_description": "short_description",
                "description": "description",
            }
            field = field_map.get(role)
            if field and text:
                result[field] = text

    if image_urls:
        result["image_urls"] = image_urls
    if attributes:
        result["attributes"] = attributes

    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Standalone entry point ──────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json as _json

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run product_page_agent standalone against a live product URL or a local HTML file."
    )
    parser.add_argument(
        "url",
        help="Absolute URL of the product page (e.g. https://shop.example.com/product/foo)",
    )
    parser.add_argument(
        "--html-file",
        metavar="FILE",
        help="Load page HTML from this local file instead of fetching the URL.",
    )
    parser.add_argument(
        "--slug",
        metavar="SLUG",
        default="",
        help=(
            "Webshop slug used to organise artifacts (e.g. 'fluffypet-pl'). "
            "Derived from the URL hostname when omitted."
        ),
    )
    args = parser.parse_args()

    effective_slug = args.slug or _slug_from_url(args.url)

    if args.html_file:
        with open(args.html_file, encoding="utf-8") as fh:
            _html_cache[args.url] = fh.read()
        print(f"Loaded HTML from {args.html_file} (cached as {args.url})")
    else:
        print(f"Will fetch {args.url} on first tool call …")

    print(f"Slug: {effective_slug}")
    print(f"\nRunning product-page sub-agent …\n{'=' * 60}")
    if not args.html_file:
        _html_cache.clear()  # ensure fresh fetch
    agent = build_product_page_agent()
    prompt = (
        f"Product page URL: {args.url}\n"
        f"Webshop slug: {effective_slug}\n"
        "Follow the workflow in your system prompt. "
        f"Start by calling fetch_and_extract_elements({args.url!r}, {effective_slug!r})."
    )
    result = str(agent(prompt))

    artifact = _artifact_path(args.url, effective_slug)
    if artifact.exists():
        print(f"\nFull element artifact: {artifact}")

    print("\n" + "=" * 60)
    print("Result:")
    try:
        selectors = _json.loads(result)
        for entry in selectors:
            role = entry.get("role", "?")
            selector = entry.get("selector", "?")
            sample = entry.get("sample_text", "")[:60]
            print(f"  {role:<22} {selector:<40} {sample!r}")
    except Exception:
        print(result)
