"""
Specialized sub-agent for discovering product links from a webshop.

Pipeline
────────
1. extract_links(html, base_url)
     → flat JSON array of every anchor on the page with heuristic flags:
       {
         "index": 0,
         "href": "https://shop.com/category/tools",
         "text": "Tools",
         "tag_context": "nav",           # nearest meaningful ancestor tag
         "class_context": "main-menu",   # first class of that ancestor
         "is_likely_category": true,
         "is_likely_product": false
       }
   Full HTML is never sent to the LLM.

2. The sub-agent's LLM reviews the link list and picks category URLs to explore.

3. fetch_page_html(url)
     → fetches a category/listing page with a plain HTTP GET and returns its HTML.

4. Repeat extract_links on each fetched page and collect product links.

5. The sub-agent's LLM selects a diverse final set (products from different
   categories) and returns them as a JSON array of URLs.

6. The returned list is handed back to the main agent in agent.py for browsing.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

# ── Configuration ──────────────────────────────────────────────────────────
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-v1:0"
)

# Maximum links returned per extract_links call (keeps LLM context small)
_MAX_LINKS = 200

# URL path fragments that strongly suggest a category page
_CATEGORY_PATH_HINTS = re.compile(
    r"/(category|categor|cat|c|department|dept|section|collection|"
    r"genre|typ|rodzaj|kategoria|dzial|dział|sklep|shop|store|browse)[/\-_]",
    re.IGNORECASE,
)

# URL path fragments that strongly suggest a product page
_PRODUCT_PATH_HINTS = re.compile(
    r"/(product|products|item|items|p|sku|towar|produkt|artykul|artykuł|"
    r"detail|details|pd|goods)[/\-_]",
    re.IGNORECASE,
)

# HTML ancestors that indicate navigation / menu context
_NAV_ANCESTORS = {"nav", "header", "aside", "ul", "ol"}

# HTML ancestors that indicate product card context
_CARD_ANCESTORS = {"article", "li", "div"}

# Class name substrings that suggest product cards
_PRODUCT_CLASS_HINTS = re.compile(
    r"product|item|card|tile|grid|listing|offer|thumb|thumbnail|towar",
    re.IGNORECASE,
)

# Class name substrings that suggest navigation menus
_NAV_CLASS_HINTS = re.compile(
    r"menu|nav|navigation|breadcrumb|sidebar|category|categor|header",
    re.IGNORECASE,
)

# HTTP request timeout in seconds
_HTTP_TIMEOUT = 15


# ── Private helpers ────────────────────────────────────────────────────────


def _resolve_href(href: str, base_url: str) -> str | None:
    """Return an absolute URL or None if the link is not navigable."""
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    return urljoin(base_url, href)


def _ancestor_context(elem: Tag) -> tuple[str, str]:
    """
    Walk up the DOM and return (tag_name, first_class) of the nearest
    ancestor that gives meaningful context.
    """
    for ancestor in elem.parents:
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name in {"html", "body", "[document]"}:
            break
        classes = ancestor.get("class", [])
        class_str = classes[0] if classes else ""
        return ancestor.name, class_str
    return "", ""


def _classify_link(href: str, anchor: Tag, ancestor_tag: str, ancestor_class: str) -> tuple[bool, bool]:
    """
    Return (is_likely_category, is_likely_product) based on URL patterns
    and DOM context heuristics.
    """
    path = urlparse(href).path

    # URL-based signals
    url_category = bool(_CATEGORY_PATH_HINTS.search(href))
    url_product = bool(_PRODUCT_PATH_HINTS.search(href))

    # DOM context signals
    in_nav = ancestor_tag in _NAV_ANCESTORS or bool(
        _NAV_CLASS_HINTS.search(ancestor_class)
    )
    in_card = bool(_PRODUCT_CLASS_HINTS.search(ancestor_class))

    # Path segment count: products usually have deeper paths (≥ 3 segments)
    segments = [s for s in path.split("/") if s]
    deep_path = len(segments) >= 3

    # Adjacent image – product cards almost always have an <img>
    has_img = bool(anchor.find("img"))

    is_category = url_category or (in_nav and not url_product and not in_card)
    is_product = url_product or (in_card and deep_path) or (has_img and deep_path and not in_nav)

    # Avoid marking the same link as both
    if is_category and is_product:
        is_category = False

    return is_category, is_product


# ── Strands tools ──────────────────────────────────────────────────────────


@tool
def extract_links(html: str, base_url: str) -> str:
    """
    Parse a page and return a flat JSON array of all navigable links with
    heuristic classification – without sending any raw HTML to the LLM.

    Each entry::

        {
            "index": 0,
            "href": "https://shop.com/category/tools",
            "text": "Tools",
            "tag_context": "nav",
            "class_context": "main-menu",
            "is_likely_category": true,
            "is_likely_product": false
        }

    Use this output to decide which category pages to explore and which
    product links to collect.

    Args:
        html: Full HTML source of the page.
        base_url: Absolute URL of the page (used to resolve relative hrefs).

    Returns:
        JSON array string, at most 200 entries, deduplicated by href.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    for anchor in soup.find_all("a", href=True):
        href = _resolve_href(anchor["href"], base_url)
        if not href or href in seen:
            continue
        # Only follow links on the same domain
        if urlparse(href).netloc != urlparse(base_url).netloc:
            continue
        seen.add(href)

        text = anchor.get_text(strip=True)[:120]
        ancestor_tag, ancestor_class = _ancestor_context(anchor)
        is_cat, is_prod = _classify_link(href, anchor, ancestor_tag, ancestor_class)

        result.append(
            {
                "index": len(result),
                "href": href,
                "text": text,
                "tag_context": ancestor_tag,
                "class_context": ancestor_class,
                "is_likely_category": is_cat,
                "is_likely_product": is_prod,
            }
        )

        if len(result) >= _MAX_LINKS:
            break

    return json.dumps(result, ensure_ascii=False)


@tool
def fetch_page_html(url: str) -> str:
    """
    Fetch a URL with a plain HTTP GET and return its HTML source.

    Use this to retrieve category / listing pages so their links can be
    extracted with extract_links without opening a browser.

    Args:
        url: Absolute URL of the page to fetch.

    Returns:
        HTML source of the page.

    Raises:
        RuntimeError: If the server returns a non-2xx status code or the
            request times out.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; product-reader-ai/1.0; "
            "+https://github.com/szymek25/product-reader-ai)"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl,en;q=0.5",
    }
    try:
        response = httpx.get(
            url, headers=headers, timeout=_HTTP_TIMEOUT, follow_redirects=True
        )
        response.raise_for_status()
        return response.text
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"HTTP {exc.response.status_code} fetching {url}"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Request error fetching {url}: {exc}") from exc


# ── Sub-agent definition ────────────────────────────────────────────────────

PRODUCT_LINKS_SYSTEM_PROMPT = """\
You are a specialized product-link discovery agent.

Your job: given the HTML of a webshop entry page (homepage or any listing),
return a JSON array of product page URLs that cover as many DIFFERENT categories
as possible.

Strict workflow — follow exactly:

1. Call extract_links(html, base_url) on the provided page.
   Review the returned link list (is_likely_category / is_likely_product flags).

2. From the category links found, pick up to 5 promising categories that look
   distinct from each other. Prefer top-level navigation categories.

3. For each chosen category URL:
     a. Call fetch_page_html(url) to retrieve the listing page.
     b. Call extract_links(html_returned, category_url) on the result.
     c. Note the product links (is_likely_product == true).

4. Also note any product links directly visible on the original page.

5. Select a final diverse set of product URLs:
   - Aim for the requested count spread across different categories.
   - Prefer links whose text or context clearly identifies them as individual
     products rather than sub-category pages.
   - Do NOT include duplicate URLs.

6. Output ONLY a JSON array of absolute product URL strings, e.g.:
   ["https://shop.com/product/foo", "https://shop.com/product/bar"]
   No prose, no markdown, no extra keys.
"""


def build_product_links_agent() -> Agent:
    """Construct the product-link discovery sub-agent."""
    model = BedrockModel(
        model_id=BEDROCK_MODEL_ID,
        region_name=AWS_REGION,
        max_tokens=4096,
    )
    return Agent(
        model=model,
        system_prompt=PRODUCT_LINKS_SYSTEM_PROMPT,
        tools=[extract_links, fetch_page_html],
    )


# ── Public tool exposed to the main agent ──────────────────────────────────


@tool
def find_product_links(html: str, base_url: str, count: int = 15) -> str:
    """
    Discover product page URLs from a webshop, sourced from different categories.

    Internally this tool runs a specialised sub-agent that:
      1. Extracts all links from the entry page without sending HTML to the LLM.
      2. Identifies category links and fetches each category listing via HTTP.
      3. Extracts product links from each listing.
      4. Selects a diverse set of ``count`` product URLs spread across categories.

    Use this at the start of STEP 1 (before collecting products) to get a
    pre-screened, category-diverse list of product URLs to visit.

    Args:
        html:     Full HTML source of the webshop entry/homepage.
        base_url: Absolute URL of the entry page (e.g. "https://shop.com/").
        count:    Number of product URLs to return (default 15).

    Returns:
        JSON array string of absolute product URL strings, e.g.::

            [
              "https://shop.com/product/lawnmower-x200",
              "https://shop.com/product/electric-bike-pro",
              ...
            ]
    """
    agent = build_product_links_agent()
    prompt = (
        f"Entry page URL: {base_url}\n"
        f"Requested product count: {count}\n\n"
        f"Entry page HTML follows:\n{html}"
    )
    result = agent(prompt)
    return str(result)
