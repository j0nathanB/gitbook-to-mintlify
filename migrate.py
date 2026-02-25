#!/usr/bin/env python3
"""
GitBook to Mintlify Migration Tool

Converts a GitBook documentation site into a Mintlify-ready project directory.
Handles content, structure, and branding — getting you ~85% to go-live.

Supports two input modes:
  URL mode:       python migrate.py https://docs.example.com
  Directory mode: python migrate.py ./path/to/gitbook-repo

URL mode scrapes a live GitBook site. Directory mode reads source markdown
from a local GitBook repo (with SUMMARY.md). Directory mode is more reliable
and handles GitBook template tags directly.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from urllib.parse import urlparse, urljoin

import requests

from migrator.crawler import GitBookCrawler
from migrator.scraper import GitBookScraper
from migrator.converter import GitBookConverter
from migrator.branding import BrandingExtractor, BrandAssets, generate_dark_variant
from migrator.config import build_docs_json, write_docs_json
from migrator.summary_parser import parse_summary, build_nav_from_summary, inject_nav_icons
from migrator.markdown_converter import MarkdownConverter
from migrator.utils import ensure_dir


def create_session() -> requests.Session:
    """Create an HTTP session with appropriate headers."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Mintlify Migration Tool) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    })
    return session


def prompt_user(prompt: str, default: str = '') -> str:
    """Prompt the user for input with an optional default."""
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"  {prompt}: ").strip()


def download_page_image(src: str, page_url: str, session: requests.Session, output_dir: str, image_counter: dict) -> str:
    """Download an image and return the local path."""
    if not src or src.startswith('data:'):
        return src

    try:
        if not src.startswith(('http://', 'https://')):
            src = urljoin(page_url, src)

        resp = session.get(src, timeout=10)
        resp.raise_for_status()

        content_type = resp.headers.get('content-type', '')
        ext_map = {
            'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif',
            'image/svg+xml': '.svg', 'image/webp': '.webp',
        }
        ext = ext_map.get(content_type.split(';')[0].strip(), '')
        if not ext:
            for e in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp']:
                if src.lower().split('?')[0].endswith(e):
                    ext = e
                    break
            if not ext:
                ext = '.png'

        image_counter['count'] = image_counter.get('count', 0) + 1
        filename = f"img-{image_counter['count']:03d}{ext}"
        images_dir = os.path.join(output_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)

        filepath = os.path.join(images_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(resp.content)

        return f'/images/{filename}'

    except Exception:
        return src


# ============================================================
#  DIRECTORY MODE — reads from a local GitBook repo
# ============================================================

def run_directory_migration(source_dir: str, output_dir: str, interactive: bool = True):
    """Migrate from a local GitBook directory (with SUMMARY.md)."""
    print()
    print("=" * 60)
    print("  GitBook → Mintlify Migration Tool (Directory Mode)")
    print("=" * 60)
    print()

    source_dir = os.path.abspath(source_dir)

    # Step 1: Validate source
    print("[1/5] Validating source directory...")
    summary_path = os.path.join(source_dir, 'SUMMARY.md')
    if not os.path.isfile(summary_path):
        print(f"  ✗ No SUMMARY.md found in {source_dir}")
        print("    This doesn't look like a GitBook repository.")
        sys.exit(1)
    print(f"  ✓ Found SUMMARY.md in {source_dir}")

    os.makedirs(output_dir, exist_ok=True)

    # Step 2: Parse SUMMARY.md
    print()
    print("[2/5] Parsing navigation structure...")
    with open(summary_path, 'r') as f:
        summary_content = f.read()

    groups, all_pages = parse_summary(summary_content)
    navigation = build_nav_from_summary(groups)

    print(f"  ✓ Found {len(groups)} groups, {len(all_pages)} pages")

    # Step 3: Branding
    print()
    print("[3/5] Configuring branding...")
    assets = BrandAssets()

    # Try to detect from .gitbook.yaml or README
    gitbook_yaml = os.path.join(source_dir, '.gitbook.yaml')
    if os.path.isfile(gitbook_yaml):
        with open(gitbook_yaml, 'r') as f:
            yaml_content = f.read()
        # Simple parsing for root/structure
        print(f"  Found .gitbook.yaml")

    # Try to get site name from README
    readme_path = os.path.join(source_dir, 'README.md')
    if os.path.isfile(readme_path):
        with open(readme_path, 'r') as f:
            readme = f.read()
        h1_match = re.search(r'^#\s+(.+)$', readme, re.MULTILINE)
        if h1_match:
            assets.site_name = h1_match.group(1).strip()
            assets.auto_detected.append('site_name')

    # Copy .gitbook/assets images if they exist
    gitbook_assets = os.path.join(source_dir, '.gitbook', 'assets')
    images_dir = os.path.join(output_dir, 'images')
    image_count = 0
    if os.path.isdir(gitbook_assets):
        os.makedirs(images_dir, exist_ok=True)
        for fname in sorted(os.listdir(gitbook_assets)):
            src = os.path.join(gitbook_assets, fname)
            if os.path.isfile(src):
                # Clean filename
                clean_name = re.sub(r'[^\w.\-]', '-', fname)
                dst = os.path.join(images_dir, clean_name)
                try:
                    shutil.copy2(src, dst)
                    image_count += 1
                except OSError as e:
                    print(f"  ⚠ Failed to copy {fname}: {e}")
        print(f"  ✓ Copied {image_count} images from .gitbook/assets/")

    if interactive:
        if not assets.site_name:
            assets.site_name = prompt_user("Site/company name")
        else:
            assets.site_name = prompt_user("Confirm site name", assets.site_name)

        color = prompt_user("Primary brand color (hex)", "#0D9373")
        assets.primary_color = color

        logo_path = prompt_user("Path to logo file (or press Enter to skip)", "")
        if logo_path and os.path.isfile(logo_path):
            os.makedirs(images_dir, exist_ok=True)
            ext = os.path.splitext(logo_path)[1]
            dst = os.path.join(images_dir, f'logo{ext}')
            shutil.copy2(logo_path, dst)
            assets.logo_light_path = f'/images/logo{ext}'
            assets.logo_dark_path = f'/images/logo{ext}'

        favicon_path = prompt_user("Path to favicon file (or press Enter to skip)", "")
        if favicon_path and os.path.isfile(favicon_path):
            os.makedirs(images_dir, exist_ok=True)
            ext = os.path.splitext(favicon_path)[1]
            dst = os.path.join(images_dir, f'favicon{ext}')
            shutil.copy2(favicon_path, dst)
            assets.favicon_path = f'/images/favicon{ext}'
    else:
        if not assets.site_name:
            assets.site_name = os.path.basename(source_dir)
        if not assets.primary_color:
            assets.primary_color = "#0D9373"

    print(f"  ✓ Site name: {assets.site_name}")
    print(f"  ✓ Primary color: {assets.primary_color}")

    # Step 4: Convert pages
    print()
    print(f"[4/5] Converting {len(all_pages)} pages...")
    converter = MarkdownConverter(base_path=source_dir)
    pages_written = []
    all_qa_issues = []
    failed_pages = []

    for i, page in enumerate(all_pages):
        progress = f"  [{i+1}/{len(all_pages)}]"
        print(f"{progress} {page.title}...", end='', flush=True)

        # Read source file
        source_file = os.path.join(source_dir, page.path)
        if not os.path.isfile(source_file):
            print(" ✗ (file not found)")
            failed_pages.append(page)
            continue

        with open(source_file, 'r', encoding='utf-8', errors='replace') as f:
            md_content = f.read()

        # Convert
        mdx = converter.convert(md_content, title=page.title, page_path=page.path)

        if converter.qa_issues:
            all_qa_issues.extend(
                [(page.mintlify_path, issue) for issue in converter.qa_issues]
            )

        # Write output
        output_file = os.path.join(output_dir, f"{page.mintlify_path}.mdx")
        ensure_dir(output_file)
        with open(output_file, 'w') as f:
            f.write(mdx)

        pages_written.append(page.mintlify_path)
        print(" ✓")

    print(f"\n  ✓ Converted {len(pages_written)}/{len(all_pages)} pages")
    if failed_pages:
        print(f"  ⚠ Failed: {len(failed_pages)} pages")

    # Step 5: Generate docs.json + QA report
    print()
    print("[5/5] Generating docs.json and QA report...")

    # Add icons to navigation groups from converted page frontmatter
    navigation = inject_nav_icons(navigation, output_dir)

    # Build mint.json using the SUMMARY.md navigation
    config = {
        "$schema": "https://mintlify.com/schema.json",
        "name": assets.site_name,
        "theme": "quill",
        "colors": {
            "primary": assets.primary_color or "#0D9373",
            "light": assets.primary_color or "#0D9373",
            "dark": generate_dark_variant(assets.primary_color or "#0D9373"),
        },
        "navigation": navigation,
    }

    if assets.logo_light_path:
        config["logo"] = {
            "light": assets.logo_light_path,
            "dark": assets.logo_dark_path or assets.logo_light_path,
        }
    config["favicon"] = assets.favicon_path or "/images/favicon.svg"

    mint_json_path = os.path.join(output_dir, 'mint.json')
    with open(mint_json_path, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"  ✓ Generated {mint_json_path}")

    # QA report
    qa_report = generate_qa_report(
        pages_written, failed_pages, all_qa_issues, assets, [], source_dir
    )
    qa_path = os.path.join(output_dir, 'QA-REPORT.txt')
    with open(qa_path, 'w') as f:
        f.write(qa_report)
    print(f"  ✓ Generated {qa_path}")

    # Summary
    print()
    print("=" * 60)
    print("  Migration Complete!")
    print("=" * 60)
    print()
    print(f"  Output directory: {os.path.abspath(output_dir)}")
    print(f"  Pages converted:  {len(pages_written)}")
    print(f"  Images copied:    {image_count}")
    print(f"  QA issues found:  {len(all_qa_issues)}")
    print()
    print("  Next steps:")
    print("  1. Review QA-REPORT.txt for items needing manual attention")
    print("  2. Run `mintlify dev` to preview the site locally")
    print("  3. Verify navigation structure in docs.json")
    print("  4. Add branding assets (logos, favicon) if not provided")
    print("  5. Test all internal links and images")
    print("  6. Push to GitHub and connect to Mintlify")
    print()


# ============================================================
#  URL MODE — scrapes a live GitBook site
# ============================================================

def run_url_migration(url: str, output_dir: str, interactive: bool = True):
    """Migrate from a live GitBook URL."""
    print()
    print("=" * 60)
    print("  GitBook → Mintlify Migration Tool (URL Mode)")
    print("=" * 60)
    print()

    session = create_session()
    base_url = url.rstrip('/')

    print("[1/6] Verifying source site...")
    try:
        resp = session.get(base_url, timeout=15)
        resp.raise_for_status()
        homepage_html = resp.text
        print(f"  ✓ Connected to {base_url}")
    except requests.RequestException as e:
        print(f"  ✗ Failed to connect to {base_url}: {e}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    # Branding
    print()
    print("[2/6] Extracting branding...")
    brand_extractor = BrandingExtractor(base_url, session)
    assets = brand_extractor.extract(homepage_html)
    assets = brand_extractor.download_assets(assets, output_dir)

    print(f"  Auto-detected: {', '.join(assets.auto_detected) if assets.auto_detected else 'none'}")

    if interactive and assets.needs_input:
        print(f"  Needs input: {', '.join(assets.needs_input)}")
        print()
        if 'site_name' in assets.needs_input:
            assets.site_name = prompt_user("Site/company name", assets.site_name or urlparse(base_url).netloc)
        if 'primary_color' in assets.needs_input:
            color = prompt_user("Primary brand color (hex, e.g. #3B82F6)", "#0D9373")
            if color:
                assets.primary_color = color
        if 'logo_light' in assets.needs_input:
            logo_url = prompt_user("Logo URL for light background (or Enter to skip)", "")
            if logo_url:
                assets.logo_light_url = logo_url
                assets = brand_extractor.download_assets(assets, output_dir)
        if 'logo_dark' in assets.needs_input:
            logo_url = prompt_user("Logo URL for dark background (or Enter to skip)", "")
            if logo_url:
                assets.logo_dark_url = logo_url
                assets = brand_extractor.download_assets(assets, output_dir)
    elif not interactive:
        if not assets.site_name:
            assets.site_name = urlparse(base_url).netloc
        if not assets.primary_color:
            assets.primary_color = "#0D9373"

    if interactive and assets.site_name:
        confirmed_name = prompt_user("Confirm site name", assets.site_name)
        assets.site_name = confirmed_name

    print(f"  ✓ Site name: {assets.site_name}")
    print(f"  ✓ Primary color: {assets.primary_color or 'default (#0D9373)'}")
    print(f"  ✓ Font: {assets.font_family or 'default (system)'}")

    # Crawl
    print()
    print("[3/6] Discovering pages...")
    crawler = GitBookCrawler(base_url, session)
    pages, nav_tree = crawler.crawl()

    if not pages:
        print("  ✗ No pages discovered.")
        print("    Tip: Modern GitBook sites render client-side. For better results,")
        print("    clone the GitBook repo and use directory mode:")
        print(f"    python migrate.py ./path/to/repo --output {output_dir}")
        sys.exit(1)

    # Scrape + convert
    print()
    print(f"[4/6] Scraping and converting {len(pages)} pages...")
    scraper = GitBookScraper(session)
    image_counter = {'count': 0}

    def image_handler(src, page_url):
        return download_page_image(src, page_url, session, output_dir, image_counter)

    converter = GitBookConverter(base_url, image_handler=image_handler)
    pages_written = []
    all_qa_issues = []
    failed_pages = []

    for i, page in enumerate(pages):
        progress = f"  [{i+1}/{len(pages)}]"
        print(f"{progress} {page.title}...", end='', flush=True)

        content = scraper.scrape_page(page.url)
        if not content:
            print(" ✗ (failed to scrape)")
            failed_pages.append(page)
            continue

        title = content.title or page.title
        description = content.description
        mdx = converter.convert_page(content.html_content, page.url, title, description)

        if converter.qa_issues:
            all_qa_issues.extend(
                [(page.path, issue) for issue in converter.qa_issues]
            )

        filepath = os.path.join(output_dir, f"{page.path}.mdx")
        ensure_dir(filepath)
        with open(filepath, 'w') as f:
            f.write(mdx)

        pages_written.append(page.path)
        print(" ✓")
        time.sleep(0.3)

    print(f"\n  ✓ Converted {len(pages_written)}/{len(pages)} pages")

    # docs.json
    print()
    print("[5/6] Generating docs.json...")
    config = build_docs_json(nav_tree, assets, pages_written)
    write_docs_json(config, os.path.join(output_dir, 'docs.json'))

    # QA report
    print()
    print("[6/6] Generating QA report...")
    qa_report = generate_qa_report(
        pages_written, failed_pages, all_qa_issues, assets, nav_tree, base_url
    )
    qa_path = os.path.join(output_dir, 'QA-REPORT.txt')
    with open(qa_path, 'w') as f:
        f.write(qa_report)
    print(f"  ✓ Generated {qa_path}")

    # Summary
    print()
    print("=" * 60)
    print("  Migration Complete!")
    print("=" * 60)
    print()
    print(f"  Output directory: {os.path.abspath(output_dir)}")
    print(f"  Pages converted:  {len(pages_written)}")
    print(f"  Images downloaded: {image_counter.get('count', 0)}")
    print(f"  QA issues found:  {len(all_qa_issues)}")
    print()
    print("  Next steps:")
    print("  1. Review QA-REPORT.txt for items needing manual attention")
    print("  2. Run `mintlify dev` to preview the site locally")
    print("  3. Verify navigation structure in docs.json")
    print("  4. Check branding (logos, colors, fonts) match the original")
    print("  5. Test all internal links and images")
    print("  6. Push to GitHub and connect to Mintlify")
    print()


def generate_qa_report(
    pages_written: list,
    failed_pages: list,
    qa_issues: list,
    assets: BrandAssets,
    nav_tree: list,
    source: str,
) -> str:
    """Generate a QA report for the migration."""
    lines = [
        "# Migration QA Report",
        "",
        f"**Source:** {source}",
        f"**Pages migrated:** {len(pages_written)}",
        f"**Pages failed:** {len(failed_pages)}",
        f"**Issues flagged:** {len(qa_issues)}",
        "",
        "---",
        "",
        "## Branding Checklist",
        "",
        f"- [{'x' if assets.logo_light_path else ' '}] Logo (light background): {assets.logo_light_path or 'MISSING — add manually to docs.json'}",
        f"- [{'x' if assets.logo_dark_path else ' '}] Logo (dark background): {assets.logo_dark_path or 'MISSING — add manually to docs.json'}",
        f"- [{'x' if assets.favicon_path else ' '}] Favicon: {assets.favicon_path or 'MISSING — add manually to docs.json'}",
        f"- [{'x' if assets.primary_color else ' '}] Primary color: {assets.primary_color or 'MISSING — set in docs.json'}",
        f"- [{'x' if assets.font_family else ' '}] Font family: {assets.font_family or 'Using system default'}",
        "",
        "**Action:** Open the migrated site alongside the original and verify visual parity.",
        "Check light mode AND dark mode.",
        "",
    ]

    if failed_pages:
        lines.extend(["## Failed Pages (need manual migration)", ""])
        for page in failed_pages:
            title = page.title if hasattr(page, 'title') else str(page)
            url = page.url if hasattr(page, 'url') else page.path if hasattr(page, 'path') else ''
            lines.append(f"- [ ] {title} — `{url}`")
        lines.append("")

    if qa_issues:
        lines.extend(["## Content Issues", ""])
        for page_path, issue in qa_issues:
            lines.append(f"- [ ] `{page_path}`: {issue}")
        lines.append("")

    lines.extend([
        "## General QA Checklist",
        "",
        "- [ ] Run `mintlify dev` — does the site build without errors?",
        "- [ ] Navigation matches original sidebar structure",
        "- [ ] All internal links resolve correctly",
        "- [ ] All images render (no broken images)",
        "- [ ] Code blocks have correct syntax highlighting",
        "- [ ] Callouts (Note, Warning, Tip, Info) render with correct styling",
        "- [ ] Tables are formatted correctly",
        "- [ ] No raw HTML or unconverted GitBook template tags visible",
        "- [ ] API reference pages handled (or flagged above)",
        "- [ ] Search works for key terms",
        "",
        "## Pages Migrated",
        "",
    ])
    for path in sorted(pages_written):
        lines.append(f"- [x] `{path}.mdx`")

    lines.append("")
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Migrate a GitBook site to Mintlify',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Input modes:
  URL mode:       python migrate.py https://docs.example.com
  Directory mode: python migrate.py ./path/to/gitbook-repo

Examples:
  python migrate.py https://docs.example.com --output ./my-docs
  python migrate.py ./gitbook-repo --output ./mintlify-docs --non-interactive
        """,
    )
    parser.add_argument(
        'source',
        help='GitBook source: URL (https://...) or local directory path',
    )
    parser.add_argument(
        '--output', '-o',
        default='./output',
        help='Output directory (default: ./output)',
    )
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Skip all prompts and use auto-detected/default values',
    )

    args = parser.parse_args()
    interactive = not args.non_interactive

    # Detect input mode
    if os.path.isdir(args.source):
        run_directory_migration(args.source, args.output, interactive)
    elif args.source.startswith(('http://', 'https://')):
        run_url_migration(args.source, args.output, interactive)
    else:
        # Could be a path that doesn't exist yet, or a malformed URL
        if os.path.exists(args.source):
            run_directory_migration(args.source, args.output, interactive)
        else:
            print(f"Error: '{args.source}' is not a valid URL or directory.")
            print("  URL mode: python migrate.py https://docs.example.com")
            print("  Dir mode: python migrate.py ./path/to/gitbook-repo")
            sys.exit(1)


if __name__ == '__main__':
    main()
