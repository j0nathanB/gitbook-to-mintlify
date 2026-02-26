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
    sidebar_title: str = ''  # Short title from link title attr (e.g., "Toolbar")


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

        # Extract markdown link title attribute as sidebar title (e.g., 'path.md "Toolbar"')
        sidebar_title = ''
        title_attr_match = re.search(r'\s+"([^"]*)"\s*$', path)
        if title_attr_match:
            sidebar_title = title_attr_match.group(1).strip()
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
            sidebar_title=sidebar_title,
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
    """Add icons to navigation groups and set sidebarTitle for parent pages."""
    import os

    def _read_frontmatter(page_path: str) -> Optional[str]:
        """Read raw frontmatter string from a converted page."""
        mdx_path = os.path.join(output_dir, f"{page_path}.mdx")
        if not os.path.isfile(mdx_path):
            return None
        try:
            with open(mdx_path, 'r') as f:
                content = f.read()
            if not content.startswith('---'):
                return None
            end = content.index('---', 3)
            return content[3:end].strip()
        except (ValueError, IOError):
            return None

    def _extract_field(frontmatter: str, field: str) -> Optional[str]:
        """Extract a field value from frontmatter text."""
        for line in frontmatter.split('\n'):
            m = re.match(rf'^{field}:\s*"?([^"]+)"?\s*$', line)
            if m:
                return m.group(1).strip()
        return None

    def _add_sidebar_title(page_path: str, group_title: str):
        """Add sidebarTitle: "Overview" to a page whose title matches its group."""
        mdx_path = os.path.join(output_dir, f"{page_path}.mdx")
        if not os.path.isfile(mdx_path):
            return
        try:
            with open(mdx_path, 'r') as f:
                content = f.read()
            if not content.startswith('---'):
                return
            end = content.index('---', 3)
            frontmatter = content[3:end].strip()

            # Check if title matches group title
            page_title = _extract_field(frontmatter, 'title')
            if not page_title or page_title.lower() != group_title.lower():
                return

            # Don't add if sidebarTitle already exists
            if 'sidebarTitle:' in frontmatter:
                return

            # Insert sidebarTitle after title line and remove icon
            new_frontmatter = frontmatter.replace(
                f'title: "{page_title}"',
                f'title: "{page_title}"\nsidebarTitle: "Overview"',
            )
            new_frontmatter = re.sub(r'\n?icon:\s*"?[^"\n]+"?\s*$', '', new_frontmatter, flags=re.MULTILINE)
            new_content = f'---\n{new_frontmatter}\n---{content[end + 3:]}'
            with open(mdx_path, 'w') as f:
                f.write(new_content)
        except (ValueError, IOError):
            pass

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
                    frontmatter = _read_frontmatter(first_page)
                    if frontmatter:
                        icon = _extract_field(frontmatter, 'icon')
                        if icon:
                            item['icon'] = icon
                    # Rename parent page to "Overview" in sidebar
                    _add_sidebar_title(first_page, item['group'])
                # Recurse into nested groups
                _process_pages(item['pages'])
        return pages

    for group in nav:
        _process_pages(group.get('pages', []))

    return nav
