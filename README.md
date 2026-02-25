# GitBook → Mintlify Migration Tool

A CLI tool that converts GitBook documentation sites into Mintlify-ready project directories — handling content, structure, and branding to get customers ~85% to go-live.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Directory mode (recommended) — from a GitBook repo with SUMMARY.md
python migrate.py ./path/to/gitbook-repo --output ./my-docs

# URL mode — scrape a live GitBook site
python migrate.py https://docs.example.com --output ./my-docs

# Non-interactive mode (skip prompts, use auto-detected values)
python migrate.py ./gitbook-repo --output ./my-docs --non-interactive
```

**Tested against GitBook's own documentation** (154 pages, 240 images): 152/154 pages converted successfully, with 10 navigation groups and full component mapping.

## Two Input Modes

### Directory Mode (recommended)
Point the tool at a local GitBook repository (any repo with `SUMMARY.md`). This is the most reliable mode — it parses SUMMARY.md for navigation, reads source markdown directly, and converts GitBook template tags (`{% hint %}`, `{% tabs %}`, `{% stepper %}`, etc.) to Mintlify components.

```bash
# Clone a GitBook repo and migrate
git clone https://github.com/company/docs.git
python migrate.py ./docs --output ./mintlify-docs
```

### URL Mode
Point the tool at a live GitBook URL. It scrapes the site via HTTP, extracts branding from the live page, and converts HTML to MDX. Works best with GitBook sites that have sitemaps or server-rendered HTML. Modern GitBook SPAs may require directory mode instead.

```bash
python migrate.py https://docs.company.com --output ./mintlify-docs
```

## What It Does

1. **Discovers all pages** — parses SUMMARY.md (directory mode) or crawls sitemap/navigation (URL mode)
2. **Extracts branding** — logos, favicon, primary color, fonts (auto-detected or prompted)
3. **Converts content** — GitBook markdown/HTML to Mintlify MDX with component mapping
4. **Maps GitBook components** to Mintlify equivalents (hints → callouts, tabs → Tabs, expandables → Accordions, steppers → Steps, code tabs → CodeGroup, content-refs → Cards)
5. **Rewrites internal links** — `.md` file paths to Mintlify-compatible routes
6. **Handles images** — copies from `.gitbook/assets/` or downloads from URLs to `/images/`
7. **Generates `docs.json`** — complete Mintlify config with navigation, branding, and theme
8. **Produces a QA report** — flags unconverted components, missing branding, and items needing review

### Output Structure

```
output/
├── docs.json              # Complete Mintlify config (nav, branding, theme)
├── QA-REPORT.md           # Checklist of items needing manual review
├── images/
│   ├── logo-light.png     # Auto-extracted logos
│   ├── logo-dark.png
│   ├── favicon.ico
│   └── img-001.png ...    # Page images
├── index.mdx              # Homepage
├── getting-started/
│   ├── quickstart.mdx
│   └── installation.mdx
└── guides/
    └── ...
```

---

## Design Decisions

### Why Option A (GitBook-specific) over Option B (general-purpose)

I chose to go deep on GitBook for three reasons:

1. **Immediate business value.** GitBook is Mintlify's most direct competitor. Mintlify's existing `@mintlify/scraping` tool supports Docusaurus and ReadMe but lists GitBook as "coming soon." This tool fills that gap.

2. **Migration quality over breadth.** A general-purpose converter would need to handle dozens of content patterns loosely. A GitBook-specific converter can handle GitBook's exact component library (hints, tabs, expandables, code tabs, API blocks) with precise mappings to Mintlify equivalents. The difference between 60% and 85% to go-live is in these details.

3. **Realistic scoping for 24-48 hours.** Going deep on one platform and doing it well demonstrates better judgment than going broad and delivering a shallow tool. In a real migration workflow, the team runs the same converter repeatedly — quality on one platform matters more than partial coverage of many.

### Why Python

- The team running migrations needs something they can modify and extend. Python is readable, debuggable, and widely known.
- No build step, no compilation. `pip install` and run.
- BeautifulSoup is the best HTML parsing library for this kind of structural conversion — forgiving, flexible, well-documented.

### Why CLI (not a web app)

Migrations are an internal operations workflow, not a customer-facing product. A CLI tool:
- Integrates into existing migration scripts and automation
- Can be run in CI/CD or batch-processed
- Is easier to debug, modify, and version control
- Doesn't need hosting or authentication

A web UI could wrap this later, but the CLI is the right foundation.

---

## How Branding Parity Works

Branding is the most under-discussed part of a migration. Customers see their docs site and immediately notice if the logo is wrong, the colors are off, or the fonts don't match. This tool handles branding at three levels:

### What it automates
- **Logo extraction** — finds logo images from the GitBook site's header/navbar (checks alt text, CSS classes, src paths)
- **Favicon detection** — extracts from `<link rel="icon">` tags
- **Primary color** — extracts from CSS custom properties (`--primary`, `--brand`, `--accent`), meta theme-color, or inline styles on prominent elements
- **Font family** — detects from Google Fonts imports, `@import` statements, or body font-family CSS
- **Dark mode variant** — auto-generates a lighter variant of the primary color for dark mode using HSL adjustment

### What it prompts for
When the tool can't confidently detect a branding element, it **prompts the user** rather than silently using a default:
- Site name confirmation (always prompted in interactive mode)
- Primary color if not found in CSS
- Logo URLs if not detected in the page header
- Dark mode logo if only one logo variant is found

### Where it falls short
- **SVG logos embedded inline** — detected but not extractable as files. The tool flags these for manual export.
- **Complex color systems** — sites with multiple brand colors (secondary, accent) beyond a single primary. The tool extracts one primary; additional colors need manual configuration.
- **Custom CSS** — GitBook custom CSS blocks (spacing, custom components) don't carry over. These need to be rebuilt in Mintlify's theming system or global CSS.
- **Dark mode parity** — the auto-generated dark variant is a reasonable approximation, not an exact match. Should be verified visually.

---

## What Breaks at Scale

At 6+ migrations per week across varied GitBook configurations:

### Known limitations

1. **Non-standard GitBook customizations.** GitBook allows custom CSS and HTML blocks. Sites with heavy customization will have elements that don't match expected selectors, producing incomplete conversions. The tool degrades gracefully (falls back to text content) but the QA burden increases.

2. **API reference pages.** GitBook's Swagger/OpenAPI rendering produces complex HTML that doesn't map cleanly to markdown. The tool flags these for manual conversion to Mintlify's OpenAPI integration. At scale, this is the biggest QA bottleneck — a future improvement would be to detect and extract the source OpenAPI spec directly.

3. **GitBook HTML structure changes.** GitBook updates their rendering periodically. CSS class names and HTML structure can shift without notice. The tool uses multiple fallback selectors, but a version that works today may need selector updates in 3 months. This is the key maintenance cost.

4. **Rate limiting and large sites.** The tool adds a 300ms delay between page scrapes. Sites with 200+ pages take several minutes. GitBook may rate-limit aggressive crawling. For very large sites, the tool should add configurable delays and resume-from-checkpoint capability.

5. **Image hosting.** Images are downloaded locally, which is correct for a migration. But GitBook CDN URLs sometimes require authentication or have expiring tokens. Failed image downloads fall back to the original URL (which will break after migration).

### What would break in production

- **No retry logic.** Network failures during a long scrape lose progress. Need: checkpoint/resume.
- **No parallel processing.** Pages are scraped sequentially. For 100+ page sites, this is slow. Need: async/concurrent scraping with rate limiting.
- **No diff/update mode.** Currently runs as a one-shot. If the customer updates their GitBook during review, you'd re-run from scratch. Need: incremental mode that only re-scrapes changed pages.

---

## What I'd Improve With More Time

**In the next sprint (immediate value):**
- **Async page scraping** with `httpx` and `asyncio` for 5-10x speed improvement on large sites
- **Checkpoint/resume** so failed migrations can pick up where they left off
- **Automatic OpenAPI spec detection** — if a GitBook site links to an OpenAPI/Swagger file, extract and wire it into `docs.json` instead of converting the rendered HTML
- **`mintlify dev` validation** — run the Mintlify CLI on the output automatically and report any build errors

**For production readiness (operational scale):**
- **Template library** — pre-built `docs.json` templates for common doc patterns (API-first, product docs, knowledge base) so the output starts closer to a polished site
- **Migration manifest** — a structured JSON log of what was converted, what was skipped, and what needs review. Enables handoff to QA or offshore teams with clear action items
- **Web wrapper** — a simple UI where a CSM can paste a GitBook URL, see a preview, adjust branding, and trigger the migration without touching the CLI
- **GitBook API integration** — GitBook has a content API. Using it instead of HTML scraping would be more reliable and handle private/authenticated sites
- **Automated visual regression** — screenshot the original GitBook page and the converted Mintlify page, diff them, flag pages with visual divergence above a threshold

---

## Component Mapping Reference

| GitBook | Mintlify | Notes |
|---|---|---|
| `{% hint style="info" %}` | `<Info>` | Blue callout |
| `{% hint style="warning" %}` | `<Warning>` | Yellow callout |
| `{% hint style="danger" %}` | `<Warning>` | Red callout (Mintlify has no `Danger`) |
| `{% hint style="success" %}` | `<Check>` | Green callout |
| `{% hint style="tip" %}` | `<Tip>` | — |
| `{% tabs %}` / `{% tab %}` | `<Tabs>` / `<Tab>` | Tab labels preserved |
| `{% stepper %}` / `{% step %}` | `<Steps>` / `<Step>` | Title extracted from first heading |
| `<details>` / `{% expand %}` | `<Accordion>` | Title from summary |
| `{% code %}` (multiple) | `<CodeGroup>` | Language + filename preserved |
| `{% content-ref %}` | `<Card>` | Linked with title and href |
| `{% embed url="..." %}` | `<Frame>` (video) or link | Flagged for QA |
| `{% swagger %}` / `{% api-method %}` | Flagged in QA report | Recommend OpenAPI spec |
| `{% file src="..." %}` | `[caption](src)` | Download link preserved |
| Code blocks | Fenced code blocks | Language class detected |
| Tables | Markdown tables | Complex tables may need review |
| Images (`.gitbook/assets/`) | `![alt](/images/...)` | Copied locally, paths rewritten |
| Internal links (`.md`) | Rewritten relative paths | `/getting-started/quickstart` |
| `{% columns %}`, `{% if %}`, etc. | Flagged in QA report | No direct Mintlify equivalent |

---

## Requirements

- Python 3.10+
- Dependencies: `requests`, `beautifulsoup4`, `lxml`, `Pillow`

```bash
pip install -r requirements.txt
```
