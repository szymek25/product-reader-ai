"""
Hardcoded profile and test-scenario schemas for product-reader-ai.

These replace the STEP 0 schema-learning agent.  The schema is stable and
does not need to be derived from GitHub reference files on every run.
"""

# ─────────────────────────────────────────────
# Profile JSON schema
# ─────────────────────────────────────────────

PROFILE_SCHEMA = """\
## Profile JSON — structure rules

A profile file is a single JSON object saved as <profiles_path>/<slug>.json.

### Always-present fields (required for every profile)
```json
{
  "id": "<slug>",
  "product_name":        [{ "selector": "<css or xpath>", "type": "<SELECTOR_TYPE>" }],
  "short_description":   [{ "selector": "<css or xpath>", "type": "<SELECTOR_TYPE>" }],
  "description":         [{ "selector": "<css or xpath>", "type": "<SELECTOR_TYPE>" }],
  "image_urls":          [{ "selector": "<css or xpath>", "type": "<SELECTOR_TYPE>" }]
}
```
Each of these fields is an **array** containing one or more selector objects.

### Selector types (SELECTOR_TYPE values)
| Value       | Meaning |
|-------------|---------|
| TEXT        | Plain text content of the matched element |
| HTML        | Raw inner HTML of the matched element |
| FIRST       | First found matching element |
| LAST        | Last found matching element |
| LINK        | The `href` attribute of the matched element |
| ATTRIBUTE   | A specific attribute value of the matched element |
| LABEL_MATCH | Sibling value matched by a label text (used for attribute table rows) |

### Optional attribute fields
If the product page contains a table of technical attributes, add:
```json
{
  "attribute_table": "<css selector for the table/container>",
  "attributes": [
    {
      "group": "default",
      "key": "<snake_case_key>",
      "type": "LABEL_MATCH",
      "label": "<label text as it appears on the page>"
    }
  ]
}
```
- `attribute_table`: CSS selector pointing to the element that wraps the attribute rows.
- Each entry in `attributes` maps one label→value pair found in the table.
- `group` is always `"default"`.
- `key` is a snake_case identifier (e.g. `motor_type`, `cutting_width`).
- `label` is the exact human-readable label text from the page (may be in any language).

### Example
```json
{
  "id": "hechtpolska",
  "product_name":      [{ "selector": "h1.text-body-2xl",                    "type": "TEXT" }],
  "short_description": [{ "selector": "p.line-clamp",                        "type": "TEXT" }],
  "description":       [{ "selector": "#product-description .product-text-content", "type": "HTML" }],
  "image_urls":        [{ "selector": ".product-gallery__link",               "type": "LINK" }],
  "attribute_table": ".detail-table",
  "attributes": [
    { "group": "default", "key": "motor_type",    "type": "LABEL_MATCH", "label": "Rodzaj silnika" },
    { "group": "default", "key": "cutting_width", "type": "LABEL_MATCH", "label": "Szerokość koszenia" },
    { "group": "default", "key": "power",         "type": "LABEL_MATCH", "label": "Moc" },
    { "group": "default", "key": "weight",        "type": "LABEL_MATCH", "label": "Waga" }
  ]
}
```
"""

# ─────────────────────────────────────────────
# Test scenarios JSON schema
# ─────────────────────────────────────────────

TEST_SCHEMA = """\
## Test scenarios JSON — structure rules

A test file is a single JSON object saved as <tests_path>/<slug>.json.

### Top-level structure
```json
{
  "profile": "<slug>",
  "tests": [ <test_object>, ... ]
}
```
- `profile`: must match the `id` in the corresponding profile file.
- `tests`: one entry per scraped product (typically 15 items).

### Test object structure
```json
{
  "name": "<product name>",
  "url":  "<full product page URL>",
  "fields": [
    { "field": "<field_path>", "type": "<COMPARISON_TYPE>" }
  ]
}
```

### Field paths
- `product_name`, `short_description`, `description`, `image_urls` — always included.
- `attributes.<key>` — one entry for each attribute the product actually has (use the
  keys defined in the profile `attributes` array).

### Comparison types (COMPARISON_TYPE values)
| Value | Meaning |
|-------|---------|
| TEXT  | Plain text content — compared as-is after whitespace normalisation |
| HTML  | Raw inner HTML — tags stripped before hash comparison |
| LINK  | URL/href value — compared as a plain string hash |

### Field-to-type mapping
| Field path             | type |
|------------------------|------|
| product_name           | TEXT |
| short_description      | TEXT |
| description            | HTML |
| image_urls             | LINK |
| attributes.*           | TEXT |

### Example
```json
{
  "profile": "hechtpolska",
  "tests": [
    {
      "name": "Robot koszący HECHT 5605",
      "url": "https://www.hechtpolska.pl/Robot-koszacy-HECHT-5605",
      "fields": [
        { "field": "product_name",        "type": "TEXT" },
        { "field": "short_description",   "type": "TEXT" },
        { "field": "description",         "type": "HTML" },
        { "field": "image_urls",          "type": "LINK" },
        { "field": "attributes.motor_type",    "type": "TEXT" },
        { "field": "attributes.cutting_width", "type": "TEXT" },
        { "field": "attributes.power",         "type": "TEXT" },
        { "field": "attributes.weight",        "type": "TEXT" }
      ]
    }
  ]
}
```
Only include `attributes.*` fields that are actually present for the specific product.
"""
