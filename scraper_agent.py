"""
Specialized sub-agent for extracting structured product data from a single URL.

Responsibility
──────────────
Given a product page URL and the saved CSS selectors, fetch the page via HTTP
and return a structured product record JSON.  No HTML ever reaches the main
agent's context.

If the primary selectors return empty for key fields (name, description), the
agent may attempt one fallback: re-inspect the page elements and try alternative
selectors before giving up.

Artifact contract
─────────────────
IN  : product_url (str), selectors_json (str)
OUT : JSON object — { url, name, short_description, description,
                      image_urls, attributes }
      (returned in-band; caller persists via add_product)

A2A readiness
─────────────
The public entry point is the @tool `scrape_product`.  All parameters are plain
strings so they can be passed over an A2A task card unchanged.
"""

from __future__ import annotations

import json

from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager

from model_factory import build_model, product_page_agent_model_id
from product_page_agent import extract_product_data, fetch_and_extract_elements
from state import add_product

SCRAPER_AGENT_SYSTEM_PROMPT = """\
You are a product data extraction agent.

You receive a product page URL and a JSON array of CSS selectors (each with a
"role", "selector", "type" field).  Your job is to extract clean product data.

Workflow:
1. Call extract_product_data(url, selectors_json).
2. If the returned JSON has a non-empty "name" field → output it verbatim and stop.
3. If "name" is empty or missing, the selectors may be stale:
   a. Call fetch_and_extract_elements(url) to get the current element list.
   b. Re-identify the correct indices for product_name and any other missing roles.
   c. Call extract_product_data again with an updated selectors_json covering only
      the roles that were empty.
   d. Merge the two results and output the combined JSON.
4. Output ONLY the raw JSON object — no prose, no markdown fences.
"""


def _build_scraper_agent() -> Agent:
    model = build_model(product_page_agent_model_id(), max_tokens=2048)
    return Agent(
        model=model,
        system_prompt=SCRAPER_AGENT_SYSTEM_PROMPT,
        tools=[extract_product_data, fetch_and_extract_elements],
        conversation_manager=SlidingWindowConversationManager(
            window_size=10, should_truncate_results=True
        ),
    )


@tool
def scrape_product(url: str, selectors_json: str, slug: str) -> str:
    """
    Extract structured product data from a single product page URL and persist it.

    Fetches the page internally — no HTML enters the main context window.
    Attempts a selector-fallback pass if key fields are empty.
    The extracted product is appended to the on-disk product list via add_product.

    Args:
        url:            Absolute URL of the product page.
        selectors_json: JSON array previously returned by analyze_product_page
                        (or loaded via load_selectors).
        slug:           Webshop slug used to persist the product record.

    Returns:
        Confirmation string from add_product (e.g. "Total saved: 3").
    """
    # Strip surrounding_html from selectors — not needed for data extraction
    # and would inflate the context with 2000-char HTML snippets.
    try:
        slim = [
            {k: v for k, v in s.items() if k != "surrounding_html"}
            for s in json.loads(selectors_json)
        ]
        selectors_json = json.dumps(slim, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass

    # Fast path: try direct extraction first without spinning up the sub-agent.
    raw = extract_product_data._tool_func(url, selectors_json)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = {}

    if data.get("name"):
        return add_product._tool_func(slug, raw)

    # Slow path: delegate to the sub-agent for fallback selector discovery.
    agent = _build_scraper_agent()
    prompt = (
        f"Product URL: {url}\n"
        f"Current selectors:\n{selectors_json}\n\n"
        "The name field is empty. Follow the fallback workflow in your system "
        "prompt and return a complete product JSON."
    )
    result = str(agent(prompt))
    return add_product._tool_func(slug, result)


@tool
def scrape_all_products(slug: str) -> str:
    """
    Scrape all remaining product URLs for a webshop and persist each record.

    Loads the product URL list and selectors entirely from disk — nothing large
    is passed through the LLM context.  Resumes automatically from the last
    saved position so interrupted runs are not restarted from scratch.

    Call this once after ``find_product_links`` / ``save_product_links`` and
    ``analyze_product_page`` / ``save_selectors`` have both been completed.
    The tool iterates internally and returns only when all 15 products are saved
    (or the URL list is exhausted).

    Args:
        slug: Webshop slug (e.g. ``"acme-store"``).

    Returns:
        A short summary such as ``"Scraped 15 products for acme-store"``.
    """
    from state import load_product_links, load_products, load_selectors

    links_raw = load_product_links._tool_func(slug)
    if not links_raw:
        return "No product links found — run find_product_links first."

    selectors_raw = load_selectors._tool_func(slug)
    if not selectors_raw:
        return "No selectors found — run analyze_product_page first."

    try:
        urls: list[str] = json.loads(links_raw)
    except json.JSONDecodeError:
        return f"Could not parse product links for {slug}."

    # Determine how many products are already saved so we can resume.
    existing_raw = load_products._tool_func(slug)
    try:
        already_saved = len(json.loads(existing_raw)) if existing_raw else 0
    except (json.JSONDecodeError, TypeError):
        already_saved = 0

    saved = already_saved
    for url in urls[already_saved:]:
        if saved >= 15:
            break
        confirmation = scrape_product._tool_func(
            url=url, selectors_json=selectors_raw, slug=slug
        )
        # confirmation is "Product appended. Total saved: N"
        try:
            saved = int(confirmation.split("Total saved:")[-1].strip())
        except (ValueError, IndexError):
            saved += 1

    return f"Scraped {saved} products for {slug}."


if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run scraper_agent standalone against a product URL."
    )
    parser.add_argument("url", help="Absolute product page URL")
    parser.add_argument(
        "selectors_file",
        help="Path to a local selectors.json file (output of analyze_product_page)",
    )
    args = parser.parse_args()

    with open(args.selectors_file, encoding="utf-8") as fh:
        selectors_json = fh.read()

    result = scrape_product._tool_func(args.url, selectors_json)
    print(result)
