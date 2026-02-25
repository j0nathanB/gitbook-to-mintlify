"""Parse GitBook SUMMARY.md into navigation structure and page list."""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SummaryPage:
    """A page entry from SUMMARY.md."""
    title: str
    path: str  # GitBook .md file path
    mintlify_path: str  # Converted Mintlify path (no .md, no README)
    order: int = 0


@dataclass
class SummaryGroup:
    """A group/section from SUMMARY.md."""
    title: str
    pages: list = field(default_factory=list)  # SummaryPage or SummaryGroup


def parse_summary(content: str) -> tuple[list[SummaryGroup], list[SummaryPage]]:
    """
    Parse a SUMMARY.md file into navigation structure and page list.

    Returns:
        (nav_groups, all_pages) — navigation hierarchy and flat page list
    """
    lines = content.strip().split('\n')
    groups = []
    all_pages = []
    current_group = None
    page_order = 0

    for line in lines:
        stripped = line.strip()

        # Skip empty lines and the top-level heading
        if not stripped or stripped.startswith('# '):
            continue

        # Group headers: ## Group Name
        if stripped.startswith('## '):
            group_title = stripped[3:].strip()
            # Strip HTML anchor tags GitBook adds (e.g., ## Title <a href="..." id="..."></a>)
            group_title = re.sub(r'\s*<a[^>]*>.*?</a>\s*', '', group_title).strip()
            current_group = SummaryGroup(title=group_title)
            groups.append(current_group)
            continue

        # Page entries: * [Title](path.md) or  * [Title](path.md)
        link_match = re.match(r'^(\s*)\*\s+\[([^\]]+)\]\(([^)]+)\)', line)
        if not link_match:
            continue

        indent = len(link_match.group(1))
        title = link_match.group(2).strip()
        path = link_match.group(3).strip()

        # Skip external links
        if path.startswith(('http://', 'https://')):
            continue

        # Convert path to Mintlify format
        mintlify_path = _to_mintlify_path(path)

        page = SummaryPage(
            title=title,
            path=path,
            mintlify_path=mintlify_path,
            order=page_order,
        )
        page_order += 1
        all_pages.append(page)

        # Add to current group
        if current_group is not None:
            if indent <= 2:
                # Top-level page in group
                current_group.pages.append(page)
            else:
                # Nested page — find the parent
                # For simplicity, add to a sub-group based on the parent
                if current_group.pages:
                    last = current_group.pages[-1]
                    if isinstance(last, SummaryGroup):
                        last.pages.append(page)
                    else:
                        # Create a sub-group from the last page
                        sub_group = SummaryGroup(
                            title=last.title,
                            pages=[page],
                        )
                        # Replace the last page with the group (which implicitly includes it)
                        current_group.pages[-1] = sub_group
                        # The parent page itself should be in the group
                        sub_group.pages.insert(0, last)
                else:
                    current_group.pages.append(page)
        else:
            # No group yet — create an implicit overview group
            if not groups or groups[0].title != 'Overview':
                current_group = SummaryGroup(title='Overview')
                groups.insert(0, current_group)
            current_group = groups[0]
            current_group.pages.append(page)

    return groups, all_pages


def _to_mintlify_path(gitbook_path: str) -> str:
    """Convert a GitBook file path to a Mintlify page path."""
    path = gitbook_path

    # Remove .md extension
    path = re.sub(r'\.md$', '', path)

    # Handle README files → use directory name
    path = re.sub(r'/README$', '', path)
    if path == 'README':
        path = 'index'

    # Clean up
    path = path.strip('/')

    return path


def build_nav_from_summary(groups: list[SummaryGroup]) -> list[dict]:
    """Convert parsed summary groups into docs.json navigation format."""
    nav = []

    for group in groups:
        nav_group = {
            "group": group.title,
            "pages": [],
        }

        for item in group.pages:
            if isinstance(item, SummaryGroup):
                # Nested group
                sub_group = {
                    "group": item.title,
                    "pages": [],
                }
                for sub_item in item.pages:
                    if isinstance(sub_item, SummaryPage):
                        sub_group["pages"].append(sub_item.mintlify_path)
                    elif isinstance(sub_item, SummaryGroup):
                        # Deep nesting — flatten
                        inner = {
                            "group": sub_item.title,
                            "pages": [
                                p.mintlify_path for p in sub_item.pages
                                if isinstance(p, SummaryPage)
                            ],
                        }
                        if inner["pages"]:
                            sub_group["pages"].append(inner)
                if sub_group["pages"]:
                    nav_group["pages"].append(sub_group)
            elif isinstance(item, SummaryPage):
                nav_group["pages"].append(item.mintlify_path)

        if nav_group["pages"]:
            nav.append(nav_group)

    return nav
