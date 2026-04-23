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

import json
import os
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag
from strands import Agent, tool

from model_factory import build_model, product_page_agent_model_id

# Per-run URL → HTML cache so a page is only fetched once per analyze call.
_html_cache: dict[str, str] = {}

# HTTP request timeout in seconds
_HTTP_TIMEOUT = 15

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


# ── Private HTTP helper ─────────────────────────────────────────────────────


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
def fetch_and_extract_elements(url: str) -> str:
    """
    Fetch a product page and return a flat JSON array of meaningful elements.

    Fetches the page via HTTP internally — never exposes raw HTML to the LLM.
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
        url: Absolute URL of the product page.

    Returns:
        JSON array string – one object per meaningful element.
    """
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    result: list[dict[str, Any]] = []
    for idx, elem in _iter_content_elements(soup):
        text = _element_text(elem)
        if len(text) > _MAX_TEXT:
            text = text[:_MAX_TEXT] + "…"
        result.append(
            {
                "index": idx,
                "tag": elem.name,
                "text": text,
                "classes": elem.get("class", []),
                "id": elem.get("id", ""),
            }
        )
    return json.dumps(result, ensure_ascii=False)


@tool
def build_selectors(url: str, classified_elements_json: str) -> str:
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

    # Build index → Tag mapping using the same traversal as fetch_and_extract_elements
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

Your job: given a product page URL, identify the CSS selectors for each
product data category so that a scraping profile can be generated.
NEVER handle raw HTML — use the provided tools which fetch pages internally.

Strict workflow — follow exactly:
1. Call fetch_and_extract_elements(url) to receive a numbered list of elements.
2. Review the list and assign each relevant element index to a role:
     product_name      – main product title (typically the h1)
     short_description – brief summary near the title
     description       – full long description section
     gallery_image     – product images or the anchors wrapping them
     attribute_table   – the container of the specification / attribute table
     attribute         – individual label cells inside the attribute table
   Multiple indices may share the same role (e.g. several gallery images,
   several attribute rows).
3. Call build_selectors(url, classified_elements_json) where
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
    )


# ── Public tool exposed to the main agent ──────────────────────────────────


@tool
def analyze_product_page(url: str) -> str:
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

    Use the returned selectors directly when writing the profile JSON file.

    Args:
        url: Absolute URL of the product page.

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
    agent = build_product_page_agent()
    result = agent(url)
    return str(result)


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
    args = parser.parse_args()

    if args.html_file:
        with open(args.html_file, encoding="utf-8") as fh:
            _html_cache[args.url] = fh.read()
        print(f"Loaded HTML from {args.html_file} (cached as {args.url})")
    else:
        print(f"Will fetch {args.url} on first tool call …")

    print(f"\nRunning product-page sub-agent …\n{'=' * 60}")
    _html_cache.clear() if not args.html_file else None  # keep pre-loaded cache
    agent = build_product_page_agent()
    result = str(agent(args.url))
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
