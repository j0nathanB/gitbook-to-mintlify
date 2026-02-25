"""Build docs.json configuration for Mintlify."""

import json
from typing import Optional

from .crawler import NavItem
from .branding import BrandAssets, generate_dark_variant


def build_docs_json(
    nav_tree: list[NavItem],
    assets: BrandAssets,
    pages_written: list[str],
) -> dict:
    """Build a complete docs.json configuration."""

    config = {
        "$schema": "https://mintlify.com/docs.json",
        "theme": "mint",
        "name": assets.site_name or "Documentation",
        "colors": _build_colors(assets),
        "navigation": _build_navigation(nav_tree, pages_written),
    }

    # Logo
    logo = _build_logo(assets)
    if logo:
        config["logo"] = logo

    # Favicon
    if assets.favicon_path:
        config["favicon"] = assets.favicon_path

    # Font
    if assets.font_family:
        config["fonts"] = {
            "heading": {"family": assets.font_family},
            "body": {"family": assets.font_family},
        }

    return config


def _build_colors(assets: BrandAssets) -> dict:
    """Build the colors configuration."""
    primary = assets.primary_color or "#0D9373"  # Mintlify default green

    colors = {"primary": primary}

    # Generate light/dark variants
    if primary:
        colors["light"] = primary
        colors["dark"] = generate_dark_variant(primary)

    return colors


def _build_logo(assets: BrandAssets) -> Optional[dict]:
    """Build the logo configuration."""
    logo = {}

    if assets.logo_light_path:
        # Light logo shows on dark background, dark logo on light background
        # GitBook convention: the "main" logo is typically for light backgrounds
        logo["dark"] = assets.logo_light_path
        logo["light"] = assets.logo_dark_path or assets.logo_light_path
    elif assets.logo_dark_path:
        logo["light"] = assets.logo_dark_path
        logo["dark"] = assets.logo_dark_path

    return logo if logo else None


def _build_navigation(nav_tree: list[NavItem], pages_written: list[str]) -> list:
    """Build the navigation array for docs.json."""
    # If we have a nav tree from the sidebar, use it
    if nav_tree:
        return _nav_tree_to_config(nav_tree, pages_written)

    # Fallback: group pages by directory
    return _pages_to_nav_groups(pages_written)


def _nav_tree_to_config(items: list[NavItem], pages_written: list[str]) -> list:
    """Convert NavItem tree to docs.json navigation format."""
    nav = []

    for item in items:
        if item.children:
            # This is a group
            group = {
                "group": item.title,
                "pages": [],
            }

            # If the group itself has a page, add it first
            if item.path and item.path in pages_written:
                group["pages"].append(item.path)

            # Add children
            for child in item.children:
                if child.children:
                    # Nested group
                    sub_group = {
                        "group": child.title,
                        "pages": [],
                    }
                    if child.path and child.path in pages_written:
                        sub_group["pages"].append(child.path)
                    for sub in child.children:
                        if sub.path and sub.path in pages_written:
                            sub_group["pages"].append(sub.path)
                    if sub_group["pages"]:
                        group["pages"].append(sub_group)
                elif child.path and child.path in pages_written:
                    group["pages"].append(child.path)

            if group["pages"]:
                nav.append(group)

        elif item.path and item.path in pages_written:
            # Standalone page — put in a default group
            nav.append(item.path)

    # If we got nothing from nav tree, fall back
    if not nav:
        return _pages_to_nav_groups(pages_written)

    # Wrap standalone pages in a group
    standalone = [item for item in nav if isinstance(item, str)]
    groups = [item for item in nav if isinstance(item, dict)]

    if standalone:
        groups.insert(0, {
            "group": "Overview",
            "pages": standalone,
        })
        nav = groups

    return nav


def _pages_to_nav_groups(pages: list[str]) -> list:
    """Group pages by their directory into navigation groups."""
    groups = {}

    for page_path in sorted(pages):
        parts = page_path.split('/')
        if len(parts) > 1:
            group_name = parts[0].replace('-', ' ').title()
        else:
            group_name = 'Overview'

        if group_name not in groups:
            groups[group_name] = []
        groups[group_name].append(page_path)

    nav = []
    for group_name, group_pages in groups.items():
        nav.append({
            "group": group_name,
            "pages": group_pages,
        })

    return nav


def write_docs_json(config: dict, output_path: str):
    """Write docs.json to disk."""
    with open(output_path, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"  Generated {output_path}")
