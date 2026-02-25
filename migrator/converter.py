"""Convert GitBook HTML content to Mintlify MDX."""

import re
from urllib.parse import urlparse, urljoin
from typing import Optional

from bs4 import BeautifulSoup, Tag, NavigableString

from .utils import url_to_filepath, is_internal_link


# GitBook hint types → Mintlify callout components
HINT_MAP = {
    'info': 'Info',
    'tip': 'Tip',
    'success': 'Check',
    'warning': 'Warning',
    'danger': 'Warning',
    'note': 'Note',
}


class GitBookConverter:
    """Converts GitBook HTML content to Mintlify-compatible MDX."""

    def __init__(self, base_url: str, image_handler=None):
        self.base_url = base_url.rstrip('/')
        self.image_handler = image_handler  # callback: (src, page_url) -> local_path
        self.qa_issues = []  # Track issues for QA report

    def convert_page(self, html_content: Tag, page_url: str, title: str, description: str) -> str:
        """Convert a page's HTML content to Mintlify MDX."""
        self.qa_issues = []
        self._current_page_url = page_url

        # Build frontmatter
        frontmatter = self._build_frontmatter(title, description)

        # Convert content
        mdx_content = self._convert_element(html_content)

        # Clean up the output
        mdx_content = self._clean_output(mdx_content)

        return frontmatter + mdx_content

    def _build_frontmatter(self, title: str, description: str) -> str:
        """Generate MDX frontmatter."""
        lines = ['---']
        lines.append(f'title: "{self._escape_yaml(title)}"')
        if description:
            lines.append(f'description: "{self._escape_yaml(description)}"')
        lines.append('---')
        lines.append('')
        return '\n'.join(lines)

    def _escape_yaml(self, text: str) -> str:
        """Escape special characters for YAML strings."""
        return text.replace('"', '\\"').replace('\n', ' ').strip()

    def _convert_element(self, element) -> str:
        """Recursively convert an HTML element to MDX."""
        if isinstance(element, NavigableString):
            text = str(element)
            if not text.strip():
                return text
            return text

        if not isinstance(element, Tag):
            return ''

        tag = element.name

        # Skip script, style, nav elements
        if tag in ('script', 'style', 'nav', 'header', 'footer', 'noscript'):
            return ''

        # Skip hidden elements
        if element.get('hidden') is not None:
            return ''
        style = element.get('style', '')
        if 'display:none' in style.replace(' ', '') or 'display: none' in style:
            return ''

        # Check for GitBook-specific components first
        result = self._try_gitbook_component(element)
        if result is not None:
            return result

        # Standard HTML → MDX conversion
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            return self._convert_heading(element)
        elif tag == 'p':
            return self._convert_paragraph(element)
        elif tag in ('ul', 'ol'):
            return self._convert_list(element)
        elif tag == 'li':
            return self._convert_list_item(element)
        elif tag == 'pre':
            return self._convert_code_block(element)
        elif tag == 'code':
            # Inline code (block code is handled via <pre>)
            return f'`{element.get_text()}`'
        elif tag == 'table':
            return self._convert_table(element)
        elif tag == 'img':
            return self._convert_image(element)
        elif tag == 'a':
            return self._convert_link(element)
        elif tag == 'strong' or tag == 'b':
            inner = self._convert_children(element)
            return f'**{inner.strip()}**' if inner.strip() else ''
        elif tag == 'em' or tag == 'i':
            inner = self._convert_children(element)
            return f'*{inner.strip()}*' if inner.strip() else ''
        elif tag == 'del' or tag == 's':
            inner = self._convert_children(element)
            return f'~~{inner.strip()}~~' if inner.strip() else ''
        elif tag == 'br':
            return '\n'
        elif tag == 'hr':
            return '\n---\n\n'
        elif tag == 'blockquote':
            return self._convert_blockquote(element)
        elif tag == 'details':
            return self._convert_details(element)
        elif tag == 'figure':
            return self._convert_figure(element)
        elif tag == 'video':
            return self._convert_video(element)
        elif tag == 'iframe':
            return self._convert_iframe(element)
        elif tag == 'sup':
            return f'<sup>{self._convert_children(element)}</sup>'
        elif tag == 'sub':
            return f'<sub>{self._convert_children(element)}</sub>'
        elif tag in ('div', 'section', 'span', 'main', 'article'):
            return self._convert_children(element)
        else:
            # Generic: just convert children
            return self._convert_children(element)

    def _convert_children(self, element: Tag) -> str:
        """Convert all children of an element."""
        parts = []
        for child in element.children:
            parts.append(self._convert_element(child))
        return ''.join(parts)

    def _try_gitbook_component(self, element: Tag) -> Optional[str]:
        """Check if element is a GitBook-specific component and convert it."""
        classes = ' '.join(element.get('class', []))
        data_attrs = {k: v for k, v in element.attrs.items() if k.startswith('data-')}

        # GitBook Hints/Callouts
        # Pattern: div with class containing "hint" and a type indicator
        if 'hint' in classes:
            return self._convert_hint(element, classes)

        # Check for data-testid patterns
        testid = element.get('data-testid', '')

        # GitBook Tabs
        if 'tabs' in classes or 'tab' in testid:
            return self._convert_tabs(element)

        # GitBook Expandable/Toggle
        if 'expandable' in classes or element.name == 'details':
            return self._convert_details(element)

        # GitBook Code tabs (multiple code blocks in tabs)
        if 'code-tabs' in classes or 'code-group' in classes:
            return self._convert_code_group(element)

        # GitBook API method blocks
        if 'api-method' in classes or 'swagger' in classes:
            return self._convert_api_block(element)

        # GitBook embed
        if 'embed' in classes:
            return self._convert_embed(element)

        # GitBook file download
        if 'file' in classes and element.find('a'):
            link = element.find('a')
            href = link.get('href', '')
            text = link.get_text(strip=True) or 'Download file'
            return f'\n[{text}]({href})\n\n'

        return None  # Not a GitBook component

    def _convert_hint(self, element: Tag, classes: str) -> str:
        """Convert GitBook hint to Mintlify callout."""
        # Determine hint type from classes
        hint_type = 'info'  # default
        for htype in HINT_MAP:
            if htype in classes:
                hint_type = htype
                break

        # Also check for style attribute or data attribute
        style = element.get('data-hint', '') or element.get('style', '')
        for htype in HINT_MAP:
            if htype in style.lower():
                hint_type = htype
                break

        component = HINT_MAP.get(hint_type, 'Note')
        inner = self._convert_children(element).strip()

        # Remove any leading emoji that GitBook adds to hints
        inner = re.sub(r'^[^\w<*\[`#]*', '', inner)

        return f'\n<{component}>\n\n{inner}\n\n</{component}>\n\n'

    def _convert_heading(self, element: Tag) -> str:
        """Convert heading element."""
        level = int(element.name[1])
        text = self._convert_children(element).strip()
        # Remove any anchor links GitBook adds inside headings
        text = re.sub(r'\[?\]?\(#.*?\)', '', text).strip()
        prefix = '#' * level
        return f'\n{prefix} {text}\n\n'

    def _convert_paragraph(self, element: Tag) -> str:
        """Convert paragraph element."""
        inner = self._convert_children(element)
        text = inner.strip()
        if not text:
            return ''
        return f'{text}\n\n'

    def _convert_list(self, element: Tag, indent: int = 0) -> str:
        """Convert ordered or unordered list."""
        items = []
        is_ordered = element.name == 'ol'
        start = int(element.get('start', 1))

        for i, li in enumerate(element.find_all('li', recursive=False)):
            prefix = f'{start + i}. ' if is_ordered else '- '
            indent_str = '  ' * indent

            # Get direct text content (not nested lists)
            text_parts = []
            nested_list = None
            for child in li.children:
                if isinstance(child, Tag) and child.name in ('ul', 'ol'):
                    nested_list = child
                else:
                    text_parts.append(self._convert_element(child))

            text = ''.join(text_parts).strip()
            items.append(f'{indent_str}{prefix}{text}')

            if nested_list:
                items.append(self._convert_list(nested_list, indent + 1))

        return '\n'.join(items) + '\n\n'

    def _convert_list_item(self, element: Tag) -> str:
        """Convert a standalone list item (shouldn't normally happen)."""
        return f'- {self._convert_children(element).strip()}\n'

    def _convert_code_block(self, element: Tag) -> str:
        """Convert a code block."""
        code_el = element.find('code')
        if not code_el:
            code_text = element.get_text()
        else:
            code_text = code_el.get_text()

        # Detect language from class
        language = ''
        classes = code_el.get('class', []) if code_el else element.get('class', [])
        for cls in classes:
            if cls.startswith('language-'):
                language = cls.replace('language-', '')
                break
            elif cls.startswith('lang-'):
                language = cls.replace('lang-', '')
                break

        # Check for title/filename
        title = element.get('data-title', '') or element.get('title', '')
        title_attr = f' {title}' if title else ''

        return f'\n```{language}{title_attr}\n{code_text}```\n\n'

    def _convert_code_group(self, element: Tag) -> str:
        """Convert GitBook code tabs to Mintlify CodeGroup."""
        code_blocks = element.find_all('pre')
        if not code_blocks:
            return self._convert_children(element)

        parts = ['\n<CodeGroup>\n\n']
        for pre in code_blocks:
            code_el = pre.find('code')
            code_text = code_el.get_text() if code_el else pre.get_text()

            language = ''
            classes = code_el.get('class', []) if code_el else []
            for cls in classes:
                if cls.startswith('language-'):
                    language = cls.replace('language-', '')
                    break

            title = pre.get('data-title', '') or language or 'code'
            parts.append(f'```{language} {title}\n{code_text}```\n\n')

        parts.append('</CodeGroup>\n\n')
        return ''.join(parts)

    def _convert_table(self, element: Tag) -> str:
        """Convert HTML table to markdown table."""
        rows = []

        # Extract header
        thead = element.find('thead')
        if thead:
            header_cells = []
            for th in thead.find_all(['th', 'td']):
                header_cells.append(self._convert_children(th).strip())
            if header_cells:
                rows.append('| ' + ' | '.join(header_cells) + ' |')
                rows.append('| ' + ' | '.join(['---'] * len(header_cells)) + ' |')

        # Extract body rows
        tbody = element.find('tbody') or element
        for tr in tbody.find_all('tr'):
            cells = []
            for td in tr.find_all(['td', 'th']):
                cell_text = self._convert_children(td).strip().replace('\n', ' ')
                cells.append(cell_text)
            if cells:
                # If no header yet, treat first row as header
                if not rows:
                    rows.append('| ' + ' | '.join(cells) + ' |')
                    rows.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
                else:
                    rows.append('| ' + ' | '.join(cells) + ' |')

        if rows:
            return '\n' + '\n'.join(rows) + '\n\n'
        return ''

    def _convert_image(self, element: Tag) -> str:
        """Convert an image element."""
        src = element.get('src', '')
        alt = element.get('alt', '')
        title = element.get('title', '')

        if not src:
            return ''

        # Resolve relative URLs
        if not src.startswith(('http://', 'https://', '//')):
            src = urljoin(self._current_page_url, src)

        # Use image handler to download and get local path
        if self.image_handler:
            local_path = self.image_handler(src, self._current_page_url)
            if local_path:
                src = local_path

        if title:
            return f'![{alt}]({src} "{title}")\n\n'
        return f'![{alt}]({src})\n\n'

    def _convert_figure(self, element: Tag) -> str:
        """Convert a figure element (image with caption)."""
        img = element.find('img')
        caption = element.find('figcaption')

        result = ''
        if img:
            result = self._convert_image(img)

        if caption:
            caption_text = self._convert_children(caption).strip()
            if caption_text:
                result += f'*{caption_text}*\n\n'

        return result

    def _convert_link(self, element: Tag) -> str:
        """Convert a link element."""
        href = element.get('href', '')
        text = self._convert_children(element).strip()

        if not href or not text:
            return text or ''

        # Rewrite internal links to Mintlify paths
        if is_internal_link(href, self.base_url):
            resolved = urljoin(self._current_page_url, href)
            new_path = url_to_filepath(resolved, self.base_url)
            if new_path:
                # Preserve anchor fragments
                fragment = ''
                if '#' in href:
                    fragment = '#' + href.split('#')[1]
                href = f'/{new_path}{fragment}'

        return f'[{text}]({href})'

    def _convert_blockquote(self, element: Tag) -> str:
        """Convert blockquote to markdown."""
        inner = self._convert_children(element).strip()
        lines = inner.split('\n')
        quoted = '\n'.join(f'> {line}' for line in lines)
        return f'\n{quoted}\n\n'

    def _convert_details(self, element: Tag) -> str:
        """Convert details/summary (expandable) to Mintlify Accordion."""
        summary = element.find('summary')
        title = summary.get_text(strip=True) if summary else 'Details'

        # Get content (everything except summary)
        content_parts = []
        for child in element.children:
            if isinstance(child, Tag) and child.name == 'summary':
                continue
            content_parts.append(self._convert_element(child))
        content = ''.join(content_parts).strip()

        return f'\n<Accordion title="{self._escape_yaml(title)}">\n\n{content}\n\n</Accordion>\n\n'

    def _convert_tabs(self, element: Tag) -> str:
        """Convert GitBook tabs to Mintlify Tabs."""
        # GitBook tabs structure varies — look for tab labels and panels
        tab_labels = []
        tab_contents = []

        # Try to find tab buttons/labels
        buttons = element.find_all(['button', 'a'], class_=lambda c: c and 'tab' in str(c).lower())
        if not buttons:
            buttons = element.find_all(['button', 'a'], role='tab')

        for btn in buttons:
            tab_labels.append(btn.get_text(strip=True))

        # Try to find tab panels
        panels = element.find_all(['div', 'section'], class_=lambda c: c and 'panel' in str(c).lower())
        if not panels:
            panels = element.find_all(['div', 'section'], role='tabpanel')

        for panel in panels:
            tab_contents.append(self._convert_children(panel).strip())

        if not tab_labels or not tab_contents:
            return self._convert_children(element)

        # Build Mintlify Tabs
        parts = ['\n<Tabs>\n\n']
        for i, (label, content) in enumerate(zip(tab_labels, tab_contents)):
            parts.append(f'<Tab title="{self._escape_yaml(label)}">\n\n')
            parts.append(f'{content}\n\n')
            parts.append(f'</Tab>\n\n')
        parts.append('</Tabs>\n\n')

        return ''.join(parts)

    def _convert_video(self, element: Tag) -> str:
        """Convert video element."""
        src = element.get('src', '')
        source = element.find('source')
        if not src and source:
            src = source.get('src', '')
        if src:
            return f'\n<video src="{src}" controls />\n\n'
        return ''

    def _convert_iframe(self, element: Tag) -> str:
        """Convert iframe (embedded content) to Mintlify Frame."""
        src = element.get('src', '')
        title = element.get('title', '')
        if src:
            self.qa_issues.append(f'Embedded iframe: {src} — verify rendering')
            return f'\n<Frame>\n  <iframe src="{src}" title="{title}" />\n</Frame>\n\n'
        return ''

    def _convert_embed(self, element: Tag) -> str:
        """Convert GitBook embed block."""
        link = element.find('a')
        if link:
            href = link.get('href', '')
            text = link.get_text(strip=True) or href
            self.qa_issues.append(f'Embedded content: {href} — verify rendering')
            return f'\n[{text}]({href})\n\n'
        return self._convert_children(element)

    def _convert_api_block(self, element: Tag) -> str:
        """Flag API reference blocks for manual handling."""
        method = ''
        path = ''

        # Try to extract API method and path
        method_el = element.find(class_=lambda c: c and 'method' in str(c).lower())
        path_el = element.find(class_=lambda c: c and ('path' in str(c).lower() or 'url' in str(c).lower()))

        if method_el:
            method = method_el.get_text(strip=True).upper()
        if path_el:
            path = path_el.get_text(strip=True)

        self.qa_issues.append(f'API reference block: {method} {path} — needs manual conversion to OpenAPI spec')

        content = self._convert_children(element).strip()
        return f'\n{{/* API Reference: {method} {path} — flagged for manual review */}}\n\n{content}\n\n'

    def _clean_output(self, text: str) -> str:
        """Clean up the final MDX output."""
        # Remove excessive blank lines (more than 2 consecutive)
        text = re.sub(r'\n{4,}', '\n\n\n', text)
        # Remove trailing whitespace on lines
        text = '\n'.join(line.rstrip() for line in text.split('\n'))
        # Ensure file ends with single newline
        text = text.strip() + '\n'
        return text
