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

    # Stack tracks (indent_level, pages_list_ref) for nesting
    nesting_stack = []

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
            nesting_stack = []
            continue

        # Page entries: * [Title](path.md) or  * [Title](path.md "optional title")
        link_match = re.match(r'^(\s*)\*\s+\[([^\]]+)\]\(([^)]+)\)', line)
        if not link_match:
            continue

        indent = len(link_match.group(1))
        title = link_match.group(2).strip()
        path = link_match.group(3).strip()

        # Strip markdown link title attribute (e.g., 'path.md "Title"')
        path = re.sub(r'\s+"[^"]*"\s*$', '', path)

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

        if current_group is None:
            # No group yet — create an implicit overview group
            if not groups or groups[0].title != 'Overview':
                current_group = SummaryGroup(title='Overview')
                groups.insert(0, current_group)
            current_group = groups[0]

        # Pop stack entries at same or deeper indent level
        while nesting_stack and nesting_stack[-1][0] >= indent:
            nesting_stack.pop()

        if not nesting_stack:
            # Top-level page in group
            current_group.pages.append(page)
            nesting_stack.append((indent, current_group.pages))
        else:
            # This page is a child — nest under the last item in the parent container
            parent_list = nesting_stack[-1][1]
            last_item = parent_list[-1] if parent_list else None

            if isinstance(last_item, SummaryGroup):
                # Already a sub-group, add to it
                last_item.pages.append(page)
                nesting_stack.append((indent, last_item.pages))
            elif isinstance(last_item, SummaryPage):
                # Convert the parent page into a sub-group containing itself and this child
                sub_group = SummaryGroup(
                    title=last_item.title,
                    pages=[last_item, page],
                )
                parent_list[-1] = sub_group
                nesting_stack.append((indent, sub_group.pages))
            else:
                # Fallback
                current_group.pages.append(page)
                nesting_stack.append((indent, current_group.pages))

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
    """Convert parsed summary groups into mint.json navigation format."""
    nav = []
    for group in groups:
        nav_group = {
            "group": group.title,
            "pages": _build_pages(group.pages),
        }
        if nav_group["pages"]:
            nav.append(nav_group)
    return nav


def _build_pages(items: list) -> list:
    """Recursively convert a list of SummaryPage/SummaryGroup into nav pages."""
    pages = []
    for item in items:
        if isinstance(item, SummaryPage):
            pages.append(item.mintlify_path)
        elif isinstance(item, SummaryGroup):
            sub = {
                "group": item.title,
                "pages": _build_pages(item.pages),
            }
            if sub["pages"]:
                pages.append(sub)
    return pages


def inject_nav_icons(nav: list, output_dir: str) -> list:
    """Add icons to navigation groups by reading page frontmatter from output."""
    import os

    def _extract_icon(page_path: str) -> Optional[str]:
        """Read icon from a converted page's frontmatter."""
        mdx_path = os.path.join(output_dir, f"{page_path}.mdx")
        if not os.path.isfile(mdx_path):
            return None
        try:
            with open(mdx_path, 'r') as f:
                content = f.read()
            if not content.startswith('---'):
                return None
            end = content.index('---', 3)
            frontmatter = content[3:end].strip()
            for line in frontmatter.split('\n'):
                m = re.match(r'^icon:\s*"?([^"]+)"?\s*$', line)
                if m:
                    return m.group(1).strip()
        except (ValueError, IOError):
            pass
        return None

    def _process_pages(pages: list) -> list:
        for item in pages:
            if isinstance(item, dict) and 'group' in item:
                # Find the first string page path in the group
                first_page = None
                for p in item['pages']:
                    if isinstance(p, str):
                        first_page = p
                        break
                if first_page:
                    icon = _extract_icon(first_page)
                    if icon:
                        item['icon'] = icon
                # Recurse into nested groups
                _process_pages(item['pages'])
        return pages

    for group in nav:
        _process_pages(group.get('pages', []))

    return nav
