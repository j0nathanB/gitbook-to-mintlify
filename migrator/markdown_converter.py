"""Convert GitBook-flavored Markdown to Mintlify MDX.

GitBook uses {% %} template tags for custom components. This module
converts those to Mintlify MDX components while preserving standard markdown.
"""

import re
from typing import Optional

from .icons import validate_icon
from .utils import sanitize_filename


# GitBook hint styles → Mintlify callout components
HINT_MAP = {
    'info': 'Info',
    'tip': 'Tip',
    'success': 'Check',
    'warning': 'Warning',
    'danger': 'Warning',
}


class MarkdownConverter:
    """Converts GitBook-flavored Markdown to Mintlify MDX."""

    def __init__(self, base_path: str = ''):
        """
        Args:
            base_path: Base path for resolving relative links
        """
        self.base_path = base_path
        self.qa_issues = []
        self._current_page_dir = ''

    def convert(self, content: str, title: str = '', description: str = '', page_path: str = '',
                sidebar_title: str = '') -> str:
        """Convert a GitBook markdown file to Mintlify MDX.

        Args:
            page_path: Path of the current file relative to source root (e.g. 'publishing/settings.md')
            sidebar_title: Short title for sidebar display (from SUMMARY.md link title attr)
        """
        self.qa_issues = []
        self.page_hidden = False
        # Store current page's directory for resolving relative links
        import os
        self._current_page_dir = os.path.dirname(page_path) if page_path else ''

        # Extract existing frontmatter if present
        existing_fm, body = self._split_frontmatter(content)

        # If title not provided, try to get from first h1 or frontmatter
        if not title:
            title = existing_fm.get('title', '') or self._extract_h1(body)

        if not description:
            description = existing_fm.get('description', '')

        icon = self._validate_icon(existing_fm.get('icon', ''))
        hidden = existing_fm.get('hidden', '').lower() == 'true'
        self.page_hidden = hidden
        noindex = existing_fm.get('noIndex', '').lower() == 'true'

        # Resolve {% include %} tags by inlining included file content
        body = self._resolve_includes(body)

        # Convert GitBook template tags to Mintlify components
        body = self._convert_hints(body)
        body = self._convert_tabs(body)
        body = self._convert_expandable(body)
        body = self._convert_code_blocks(body)
        body = self._convert_content_refs(body)
        body = self._convert_embeds(body)
        body = self._convert_swagger(body)
        body = self._convert_file_refs(body)
        body = self._convert_stepper(body)

        # Convert GitBook card tables to Mintlify CardGroup/Card
        body = self._convert_card_tables(body)

        # Clean up any remaining template tags
        body = self._cleanup_template_tags(body)

        # Convert <pre><code> HTML blocks to fenced code blocks
        body = self._convert_pre_code_blocks(body)

        # Convert image references
        body = self._convert_images(body)

        # Convert internal links (.md → relative paths)
        body = self._convert_links(body)

        # Remove the first H1 if it matches the title (Mintlify shows title from frontmatter)
        body = self._remove_duplicate_title(body, title)

        # Build Mintlify frontmatter
        frontmatter = self._build_frontmatter(title, description, icon, sidebar_title, hidden, noindex)

        # Clean up
        result = frontmatter + body
        result = self._clean_output(result)

        return result

    def _split_frontmatter(self, content: str) -> tuple[dict, str]:
        """Split YAML frontmatter from body content."""
        if not content.startswith('---'):
            return {}, content

        parts = content.split('---', 2)
        if len(parts) < 3:
            return {}, content

        fm_text = parts[1].strip()
        body = parts[2]

        # Simple YAML parsing for key: value pairs (handles block scalars)
        fm = {}
        lines = fm_text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            if ':' in line and not line.startswith(' '):
                key, _, value = line.partition(':')
                value = value.strip().strip('"').strip("'")
                # Handle YAML block scalar indicators (>-, |-, >, |)
                if value in ('>', '|-', '>-', '|'):
                    # Collect indented continuation lines
                    block_lines = []
                    i += 1
                    while i < len(lines) and (lines[i].startswith('  ') or lines[i].strip() == ''):
                        block_lines.append(lines[i].strip())
                        i += 1
                    fm[key.strip()] = ' '.join(bl for bl in block_lines if bl)
                    continue
                if value:
                    fm[key.strip()] = value
            i += 1

        return fm, body

    def _extract_h1(self, content: str) -> str:
        """Extract the first H1 heading from content."""
        match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        return match.group(1).strip() if match else ''

    def _remove_duplicate_title(self, body: str, title: str) -> str:
        """Remove the first H1 if it duplicates the frontmatter title."""
        if not title:
            return body
        pattern = rf'^\s*#\s+{re.escape(title)}\s*$'
        body = re.sub(pattern, '', body, count=1, flags=re.MULTILINE)
        return body

    def _build_frontmatter(self, title: str, description: str, icon: str = '',
                           sidebar_title: str = '', hidden: bool = False,
                           noindex: bool = False) -> str:
        """Generate Mintlify MDX frontmatter."""
        lines = ['---']
        if title:
            lines.append(f'title: "{self._escape_yaml(title)}"')
        if sidebar_title and sidebar_title != title:
            lines.append(f'sidebarTitle: "{self._escape_yaml(sidebar_title)}"')
        if description:
            lines.append(f'description: "{self._escape_yaml(description)}"')
        if icon:
            lines.append(f'icon: "{icon}"')
        if hidden:
            lines.append('hidden: true')
        if noindex:
            lines.append('noIndex: true')
        lines.append('---')
        lines.append('')
        return '\n'.join(lines)

    def _validate_icon(self, icon: str) -> str:
        """Validate icon against Mintlify's supported set.

        Returns the icon if valid, empty string if not (with a QA warning).
        """
        if not icon:
            return ''
        valid = validate_icon(icon)
        if valid:
            return valid
        self.qa_issues.append(
            f'Stripped unsupported icon "{icon}" — not in FontAwesome free set'
        )
        return ''

    def _escape_yaml(self, text: str) -> str:
        """Escape special characters for YAML."""
        return text.replace('"', '\\"').replace('\n', ' ').strip()

    # ---- GitBook Template Tag Converters ----

    def _convert_hints(self, content: str) -> str:
        """Convert {% hint style="..." %} to Mintlify callouts."""
        def replace_hint(match):
            attrs = match.group(1)
            inner = match.group(2).strip()

            # Extract style
            style_match = re.search(r'style="(\w+)"', attrs)
            style = style_match.group(1) if style_match else 'info'
            component = HINT_MAP.get(style, 'Note')

            return f'\n<{component}>\n\n{inner}\n\n</{component}>\n'

        # Match {% hint ... %} with any combination of attributes
        pattern = r'\{%\s*hint\s+([^%]*?)%\}(.*?)\{%\s*endhint\s*%\}'
        return re.sub(pattern, replace_hint, content, flags=re.DOTALL)

    def _convert_tabs(self, content: str) -> str:
        """Convert {% tabs %}/{% tab %} to Mintlify Tabs."""
        def replace_tabs(match):
            tabs_content = match.group(1)

            # Extract individual tabs
            tab_pattern = r'\{%\s*tab\s+title="([^"]+)"\s*%\}(.*?)\{%\s*endtab\s*%\}'
            tabs = re.findall(tab_pattern, tabs_content, flags=re.DOTALL)

            if not tabs:
                return tabs_content

            parts = ['\n<Tabs>\n']
            for title, body in tabs:
                body = body.strip()
                parts.append(f'\n<Tab title="{title}">\n\n{body}\n\n</Tab>\n')
            parts.append('\n</Tabs>\n')
            return ''.join(parts)

        pattern = r'\{%\s*tabs\s*%\}(.*?)\{%\s*endtabs\s*%\}'
        return re.sub(pattern, replace_tabs, content, flags=re.DOTALL)

    def _convert_expandable(self, content: str) -> str:
        """Convert {% expand %} / <details> to Mintlify Accordion."""
        # GitBook expand syntax
        def replace_expand(match):
            title = match.group(1)
            inner = match.group(2).strip()
            return f'\n<Accordion title="{self._escape_yaml(title)}">\n\n{inner}\n\n</Accordion>\n'

        # {% expand title="..." %} syntax
        pattern = r'\{%\s*expand\s+title="([^"]+)"\s*%\}(.*?)\{%\s*endexpand\s*%\}'
        content = re.sub(pattern, replace_expand, content, flags=re.DOTALL)

        # Also handle HTML <details>/<summary> in markdown
        def replace_details(match):
            summary = match.group(1)
            inner = match.group(2).strip()
            return f'\n<Accordion title="{self._escape_yaml(summary)}">\n\n{inner}\n\n</Accordion>\n'

        pattern = r'<details>\s*<summary>([^<]+)</summary>(.*?)</details>'
        content = re.sub(pattern, replace_details, content, flags=re.DOTALL)

        return content

    def _convert_code_blocks(self, content: str) -> str:
        """Convert GitBook {% code %} blocks to fenced code blocks."""
        def replace_code(match):
            attrs = match.group(1)
            inner = match.group(2).strip()

            title = ''
            title_match = re.search(r'title="([^"]+)"', attrs)
            if title_match:
                title = title_match.group(1)

            lang = ''
            lang_match = re.search(r'lang(?:uage)?="([^"]+)"', attrs)
            if lang_match:
                lang = lang_match.group(1)

            # If inner already has a fenced code block, just add the title
            if inner.startswith('```'):
                if title and '\n' in inner:
                    # Insert title after the opening ```lang
                    first_line_end = inner.index('\n')
                    first_line = inner[:first_line_end]
                    rest = inner[first_line_end:]
                    return f'{first_line} {title}{rest}'
                return inner

            return f'```{lang} {title}\n{inner}\n```'

        pattern = r'\{%\s*code([^%]*?)%\}(.*?)\{%\s*endcode\s*%\}'
        return re.sub(pattern, replace_code, content, flags=re.DOTALL)

    def _convert_content_refs(self, content: str) -> str:
        """Convert {% content-ref %} to Mintlify card links."""
        def replace_ref(match):
            url = match.group(1)
            inner = match.group(2).strip()

            # Convert .md path to Mintlify path
            clean_url = self._convert_md_path(url)

            # Extract link text from inner content
            link_match = re.search(r'\[([^\]]+)\]', inner)
            title = link_match.group(1) if link_match else clean_url

            return f'\n<Card title="{self._escape_yaml(title)}" href="/{clean_url}">\n\n</Card>\n'

        pattern = r'\{%\s*content-ref\s+url="([^"]+)"\s*%\}(.*?)\{%\s*endcontent-ref\s*%\}'
        return re.sub(pattern, replace_ref, content, flags=re.DOTALL)

    def _convert_embeds(self, content: str) -> str:
        """Convert {% embed %} to links or frames."""
        def replace_embed(match):
            url = match.group(1)
            self.qa_issues.append(f'Embedded content: {url} — verify rendering')

            # YouTube/Vimeo → iframe
            if 'youtube.com' in url or 'youtu.be' in url or 'vimeo.com' in url:
                return f'\n<Frame>\n  <iframe src="{url}" />\n</Frame>\n'

            return f'\n[Embedded: {url}]({url})\n'

        pattern = r'\{%\s*embed\s+url="([^"]+)"[^%]*%\}'
        return re.sub(pattern, replace_embed, content)

    def _convert_swagger(self, content: str) -> str:
        """Flag {% swagger %} / {% api-method %} blocks for manual review."""
        def replace_swagger(match):
            inner = match.group(1)

            # Try to extract method and path
            method_match = re.search(r'method="(\w+)"', inner)
            path_match = re.search(r'path="([^"]+)"', inner)

            method = method_match.group(1).upper() if method_match else ''
            path = path_match.group(1) if path_match else ''

            self.qa_issues.append(f'API reference: {method} {path} — convert to OpenAPI spec')

            return f'\n{{/* API Reference: {method} {path} — flagged for manual review. Use Mintlify OpenAPI integration instead. */}}\n'

        # Handle both {% swagger %} and {% api-method %} syntaxes
        for tag in ['swagger', 'api-method']:
            pattern = rf'\{{% \s*{tag}[^%]*%\}}(.*?)\{{% \s*end{tag}\s*%\}}'
            content = re.sub(pattern, replace_swagger, content, flags=re.DOTALL)

        return content

    def _convert_file_refs(self, content: str) -> str:
        """Convert {% file src="..." %} to download links."""
        def replace_file(match):
            src = match.group(1)
            caption = ''
            caption_match = re.search(r'caption="([^"]+)"', match.group(0))
            if caption_match:
                caption = caption_match.group(1)
            label = caption or src.split('/')[-1]
            return f'[{label}]({src})'

        pattern = r'\{%\s*file\s+src="([^"]+)"[^%]*%\}'
        return re.sub(pattern, replace_file, content)

    def _convert_stepper(self, content: str) -> str:
        """Convert {% stepper %}/{% step %} to Mintlify Steps."""
        def replace_stepper(match):
            stepper_content = match.group(1)

            # Extract individual steps
            step_pattern = r'\{%\s*step\s*%\}(.*?)(?=\{%\s*(?:step|endstepper)\s*%\})'
            steps = re.findall(step_pattern, stepper_content, flags=re.DOTALL)

            if not steps:
                return stepper_content

            parts = ['\n<Steps>\n']
            for i, step_body in enumerate(steps):
                step_body = step_body.strip()
                # Try to extract a title from the first heading or bold text
                title_match = re.match(r'^#{1,6}\s+(.+)$', step_body, re.MULTILINE)
                if title_match:
                    title = title_match.group(1)
                    step_body = step_body[title_match.end():].strip()
                else:
                    title = f'Step {i + 1}'

                parts.append(f'\n<Step title="{self._escape_yaml(title)}">\n\n{step_body}\n\n</Step>\n')
            parts.append('\n</Steps>\n')
            return ''.join(parts)

        pattern = r'\{%\s*stepper\s*%\}(.*?)\{%\s*endstepper\s*%\}'
        return re.sub(pattern, replace_stepper, content, flags=re.DOTALL)

    def _resolve_includes(self, content: str) -> str:
        """Resolve {% include %} tags by inlining the referenced file content."""
        import os

        def replace_include(match):
            include_path = match.group(1)
            # Resolve the .gitbook/includes/ path
            gitbook_match = re.search(r'\.gitbook/includes/(.+)', include_path)
            if gitbook_match and self.base_path:
                full_path = os.path.join(self.base_path, '.gitbook', 'includes', gitbook_match.group(1))
                if os.path.isfile(full_path):
                    with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                        included = f.read()
                    # Strip frontmatter from included file
                    _, included_body = self._split_frontmatter(included)
                    return included_body.strip()
            self.qa_issues.append(f'Could not resolve include: {include_path}')
            return ''

        pattern = r'\{%\s*include\s+"([^"]+)"\s*%\}'
        return re.sub(pattern, replace_include, content)

    def _convert_pre_code_blocks(self, content: str) -> str:
        """Convert <pre><code> HTML blocks to fenced code blocks."""
        def replace_pre_code(match):
            pre_attrs = match.group(1) or ''
            code_attrs = match.group(2) or ''
            inner = match.group(3)

            # Extract language from class
            lang = ''
            lang_match = re.search(r'(?:class="lang(?:uage)?-(\w+)"|data-lang="(\w+)")', pre_attrs + ' ' + code_attrs)
            if lang_match:
                lang = lang_match.group(1) or lang_match.group(2) or ''

            # Extract title from data-title
            title = ''
            title_match = re.search(r'data-title="([^"]+)"', pre_attrs)
            if title_match:
                title = title_match.group(1)

            # Strip <strong> tags (GitBook line highlighting)
            inner = re.sub(r'</?strong>', '', inner)
            # Strip any other inline HTML
            inner = re.sub(r'</?(?:em|mark|span)[^>]*>', '', inner)

            header = f'```{lang}'
            if title:
                header += f' {title}'
            return f'\n{header}\n{inner}\n```\n'

        pattern = r'<pre[^>]*?((?:class|data-)[^>]*)?>\s*<code[^>]*?((?:class|data-)[^>]*)?>(.*?)</code>\s*</pre>'
        return re.sub(pattern, replace_pre_code, content, flags=re.DOTALL)

    def _convert_card_tables(self, content: str) -> str:
        """Convert GitBook <table data-view="cards"> to Mintlify CardGroup/Card."""
        def replace_card_table(match):
            table_html = match.group(0)

            # Parse column types from <thead> <th> attributes
            col_types = []  # 'title', 'description', 'target', 'cover', 'hidden'
            for th_match in re.finditer(r'<th\b([^>]*)>', table_html):
                attrs = th_match.group(1)
                if 'data-card-target' in attrs:
                    col_types.append('target')
                elif 'data-card-cover' in attrs and 'data-card-cover-dark' not in attrs:
                    col_types.append('cover')
                elif 'data-hidden' in attrs:
                    col_types.append('hidden')
                elif not col_types:
                    col_types.append('title')
                else:
                    col_types.append('description')

            # Parse body rows
            tbody_match = re.search(r'<tbody>(.*?)</tbody>', table_html, re.DOTALL)
            if not tbody_match:
                return table_html

            cards = []
            for row_match in re.finditer(r'<tr>(.*?)</tr>', tbody_match.group(1), re.DOTALL):
                row = row_match.group(1)
                cells = [m.group(1).strip() for m in re.finditer(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)]

                title = ''
                description = ''
                href = ''
                img = ''

                for i, cell in enumerate(cells):
                    col_type = col_types[i] if i < len(col_types) else 'hidden'

                    if col_type == 'title':
                        title = re.sub(r'<[^>]+>', '', cell).strip()
                    elif col_type == 'description':
                        description = cell.strip()
                    elif col_type == 'target':
                        link_match = re.search(r'<a\s+href="([^"]+)"', cell)
                        if link_match:
                            raw_href = link_match.group(1)
                            if not raw_href.startswith(('/broken/', 'http')):
                                href = self._convert_md_path(raw_href)
                                if not href.startswith(('http', '#', '/')):
                                    href = '/' + href
                    elif col_type == 'cover':
                        link_match = re.search(r'<a\s+href="([^"]+)"', cell)
                        if link_match:
                            img_src = link_match.group(1)
                            gitbook_match = re.search(r'\.gitbook/assets/(.+)', img_src)
                            if gitbook_match:
                                filename = re.sub(r'[^\w.\-]', '-', gitbook_match.group(1))
                                img = f'/images/{filename}'
                            else:
                                img = img_src

                if title:
                    card_attrs = f'title="{self._escape_yaml(title)}"'
                    if href:
                        card_attrs += f' href="{href}"'
                    if img:
                        card_attrs += f' img="{img}"'
                    card = f'<Card {card_attrs}>\n{description}\n</Card>'
                    cards.append(card)

            if not cards:
                return table_html

            # Check for data-card-size="large" → 2 columns, otherwise 3
            is_large = 'data-card-size="large"' in table_html
            max_cols = 2 if is_large else 3
            cols = min(len(cards), max_cols)
            result = f'\n<CardGroup cols="{cols}">\n\n'
            result += '\n\n'.join(cards)
            result += '\n\n</CardGroup>\n'
            return result

        pattern = r'<table[^>]*data-view="cards"[^>]*>.*?</table>'
        return re.sub(pattern, replace_card_table, content, flags=re.DOTALL)

    def _cleanup_template_tags(self, content: str) -> str:
        """Remove any remaining {% %} template tags that weren't caught by specific converters."""
        # Remove standalone end tags
        content = re.sub(r'\{%\s*end\w+\s*%\}', '', content)
        # Flag and REMOVE any remaining opening tags
        remaining = re.findall(r'\{%\s*(\w+)[^%]*%\}', content)
        for tag in remaining:
            if tag not in ('raw', 'endraw'):
                self.qa_issues.append(f'Unconverted template tag: {{% {tag} %}}')
        # Remove all remaining template tags
        content = re.sub(r'\{%[^%]*%\}', '', content)
        return content

    # ---- Standard Markdown Adjustments ----

    def _convert_images(self, content: str) -> str:
        """Rewrite image paths from .gitbook/assets/ to /images/."""
        def replace_image(match):
            alt = match.group(1)
            src = match.group(2)

            # Rewrite .gitbook/assets paths (handle relative paths)
            gitbook_match = re.search(r'\.gitbook/assets/(.+)', src)
            if gitbook_match:
                filename = gitbook_match.group(1)
                # Clean filename
                filename = re.sub(r'[^\w.\-]', '-', filename)
                src = f'/images/{filename}'

            return f'![{alt}]({src})'

        pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        content = re.sub(pattern, replace_image, content)

        # Also handle <img> tags with .gitbook/assets paths
        def replace_img_tag(match):
            full_tag = match.group(0)
            src_match = re.search(r'src="([^"]*\.gitbook/assets/[^"]*)"', full_tag)
            if src_match:
                old_src = src_match.group(1)
                filename = old_src.split('/')[-1]
                filename = re.sub(r'[^\w.\-]', '-', filename)
                new_src = f'/images/{filename}'
                full_tag = full_tag.replace(src_match.group(0), f'src="{new_src}"')
            return full_tag

        content = re.sub(r'<img\s[^>]*>', replace_img_tag, content)

        return content

    def _convert_links(self, content: str) -> str:
        """Convert .md file links to Mintlify-style paths."""
        def replace_link(match):
            text = match.group(1)
            href = match.group(2)

            # Strip markdown link title attributes (e.g., 'path.md "mention"')
            title_match = re.match(r'^(.+?)\s+"[^"]*"\s*$', href)
            if title_match:
                href = title_match.group(1)

            # Skip external links, anchors, and images
            if href.startswith(('http://', 'https://', '#', 'mailto:')):
                return match.group(0)

            # Convert .md path to Mintlify path
            href = self._convert_md_path(href)

            return f'[{text}](/{href})'

        # Match markdown links but not image links
        pattern = r'(?<!!)\[([^\]]+)\]\(([^)]+)\)'
        return re.sub(pattern, replace_link, content)

    def _convert_md_path(self, path: str) -> str:
        """Convert a GitBook .md file path to a Mintlify page path."""
        import posixpath

        # Preserve fragment
        fragment = ''
        if '#' in path:
            path, fragment = path.split('#', 1)
            fragment = '#' + fragment

        # Remove .md extension
        path = re.sub(r'\.md$', '', path)

        # Handle README files (GitBook uses README.md as group index)
        path = re.sub(r'/README$', '', path)
        if path == 'README':
            path = 'index'

        # Remove leading ./
        path = re.sub(r'^\./', '', path)

        # Resolve relative paths against current page directory
        if not path.startswith('/') and self._current_page_dir:
            # Path is relative to the current file's directory
            path = posixpath.normpath(posixpath.join(self._current_page_dir, path))

        # Clean up
        path = path.strip('/')

        return path + fragment

    def _clean_output(self, text: str) -> str:
        """Clean up the final MDX output."""
        # Strip HTML anchor tags GitBook adds to headings
        text = re.sub(r'\s*<a\s+href="#[^"]*"\s+id="[^"]*"\s*>\s*</a>', '', text)
        text = re.sub(r'\s*<a\s+id="[^"]*"\s+href="#[^"]*"\s*>\s*</a>', '', text)

        # Strip anchor-only links from markdown headings
        # GitBook adds invisible anchors like [](#some-id) or [](# "some-id")
        text = re.sub(r'(^#{1,6}\s+.*?)\s*\[(?:[^\]]*)\]\(#[^)]*\)\s*$', r'\1', text, flags=re.MULTILINE)

        # Remove empty HTML paragraphs and empty inline tags
        text = re.sub(r'<p>\s*</p>', '', text)
        text = re.sub(r'<strong>\s*</strong>', '', text)
        text = re.sub(r'<em>\s*</em>', '', text)

        # Convert <figure>/<picture> wrappers to clean markdown images
        text = re.sub(r'<figure>.*?</figure>', lambda m: _figure_to_md(m.group(0)), text, flags=re.DOTALL)
        text = re.sub(r'<picture>.*?</picture>', lambda m: _picture_to_img(m.group(0)), text, flags=re.DOTALL)

        # Make void HTML elements self-closing for MDX compatibility
        # Use a function to avoid double self-closing (/ />)
        def make_self_closing(tag_name):
            def replacer(match):
                attrs = match.group(1).rstrip().rstrip('/')
                return f'<{tag_name} {attrs.strip()} />'
            return replacer
        text = re.sub(r'<img\s([^>]*?)>', make_self_closing('img'), text)
        text = re.sub(r'<br\s*/?>', '<br />', text)
        text = re.sub(r'<hr\s*/?>', '<hr />', text)

        # Fix any double self-closing patterns (e.g., / />)
        text = re.sub(r'/\s+/>', '/>', text)

        # Strip <mark> tags (GitBook colored text) — keep inner text
        text = re.sub(r'<mark[^>]*>(.*?)</mark>', r'\1', text, flags=re.DOTALL)

        # Rejoin inline images that GitBook split across lines.
        # GitBook exports inline icons as block-level <picture> elements with
        # &#x20; connectors: text&#x20;\n\n    <img .../>\n\n    &#x20;text
        # Both sides have &#x20;
        text = re.sub(
            r'&#x20;\s*\n\s*\n\s*(<img\s[^>]*/\s*>)\s*\n\s*\n\s*&#x20;',
            r' \1 ',
            text,
        )
        # Only &#x20; before the image
        text = re.sub(
            r'&#x20;\s*\n\s*\n\s*(<img\s[^>]*/\s*>)\s*\n',
            r' \1\n',
            text,
        )
        # Only &#x20; after the image
        text = re.sub(
            r'\n\s*(<img\s[^>]*/\s*>)\s*\n\s*\n\s*&#x20;',
            r' \1 ',
            text,
        )

        # Rejoin indented <img> tags that are mid-sentence within list items.
        # Pattern: text\n\n    <img .../>\n\n    lowercase-continuation
        text = re.sub(
            r'(\S[^\n]*)\n\n(\s+)(<img\s[^>]*/\s*>)\n\n\2([a-z][^\n]*)',
            r'\1 \3 \4',
            text,
        )

        # Clean up remaining &#x20; entities (just trailing spaces from GitBook)
        text = text.replace('&#x20;', ' ')

        # Add inline display style to <img> tags that appear inline with text.
        # Mintlify wraps markdown images in a block-level zoom component, so we
        # keep inline icons as <img> with explicit inline styling instead.
        # Uses a sentinel to survive the JSX brace escaping pass.
        text = _style_inline_imgs(text)

        # Escape curly braces in text that MDX would interpret as JSX expressions
        text = _escape_jsx_braces(text)

        # Replace sentinel with actual JSX style attribute (after brace escaping)
        text = text.replace('__INLINE_IMG_STYLE__', 'style={{display:"inline",height:"1em",verticalAlign:"middle"}}')

        # Wrap 2+ adjacent Accordion blocks in AccordionGroup
        text = _wrap_accordion_groups(text)

        # Wrap 2+ adjacent code blocks with different languages in CodeGroup
        text = _wrap_code_groups(text)

        # Remove lines that are only whitespace
        text = re.sub(r'^[^\S\n]+$', '', text, flags=re.MULTILINE)
        # Collapse 3+ consecutive blank lines to 2
        text = re.sub(r'\n{4,}', '\n\n\n', text)
        # Remove trailing whitespace
        text = '\n'.join(line.rstrip() for line in text.split('\n'))
        # Ensure single trailing newline
        text = text.strip() + '\n'
        return text


def _escape_jsx_braces(text: str) -> str:
    """Escape { and } outside of fenced code blocks so MDX doesn't treat them as JSX."""
    lines = text.split('\n')
    result = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Track fenced code blocks
        if stripped.startswith('```'):
            in_code_block = not in_code_block
            result.append(line)
            continue

        if in_code_block:
            result.append(line)
            continue

        # Outside code blocks, escape { and } that aren't part of:
        # - JSX comments: {/* ... */}
        # - Inline code: `...{...}...`

        # Split by inline code spans to protect them
        parts = re.split(r'(`[^`]*`)', line)
        new_parts = []
        for part in parts:
            if part.startswith('`') and part.endswith('`'):
                new_parts.append(part)
            else:
                # Protect JSX comments {/* ... */} with sentinel
                protected = []
                part = re.sub(r'\{/\*.*?\*/\}', lambda m: (protected.append(m.group(0)), f'\x00JSXC{len(protected)-1}\x00')[1], part)
                # Escape all remaining braces
                part = part.replace('{', '\\{').replace('}', '\\}')
                # Restore protected JSX comments
                for i, p in enumerate(protected):
                    part = part.replace(f'\x00JSXC{i}\x00', p)
                new_parts.append(part)
        result.append(''.join(new_parts))

    return '\n'.join(result)


def _figure_to_md(figure_html: str) -> str:
    """Convert a <figure> block to a markdown image."""
    img_match = re.search(r'<img\s[^>]*src="([^"]*)"[^>]*>', figure_html)
    if not img_match:
        return ''
    src = img_match.group(1)
    # Rewrite .gitbook/assets paths
    gitbook_match = re.search(r'\.gitbook/assets/(.+)', src)
    if gitbook_match:
        filename = gitbook_match.group(1)
        filename = re.sub(r'[^\w.\-]', '-', filename)
        src = f'/images/{filename}'
    alt_match = re.search(r'alt="([^"]*)"', figure_html)
    alt = alt_match.group(1) if alt_match else ''
    caption_match = re.search(r'<figcaption>(.*?)</figcaption>', figure_html, re.DOTALL)
    result = f'![{alt}]({src})'
    if caption_match:
        result += f'\n*{caption_match.group(1).strip()}*'
    return result


def _picture_to_img(picture_html: str) -> str:
    """Extract a simple image from a <picture> element."""
    img_match = re.search(r'<img\s([^>]*)>', picture_html)
    if img_match:
        attrs = img_match.group(1)
        # Rewrite .gitbook/assets in src
        def rewrite_src(m):
            src = m.group(1)
            gitbook_match = re.search(r'\.gitbook/assets/(.+)', src)
            if gitbook_match:
                filename = gitbook_match.group(1)
                filename = re.sub(r'[^\w.\-]', '-', filename)
                return f'src="/images/{filename}"'
            return m.group(0)
        attrs = re.sub(r'src="([^"]*)"', rewrite_src, attrs)
        return f'<img {attrs} />'
    return ''


def _wrap_accordion_groups(text: str) -> str:
    """Wrap 2+ adjacent <Accordion>...</Accordion> blocks in <AccordionGroup>."""
    # Match runs of 2+ Accordion blocks separated only by whitespace
    pattern = r'((?:<Accordion\b[^>]*>.*?</Accordion>\s*){2,})'
    def replacer(match):
        block = match.group(1).strip()
        return f'\n<AccordionGroup>\n\n{block}\n\n</AccordionGroup>\n'
    return re.sub(pattern, replacer, text, flags=re.DOTALL)


def _wrap_code_groups(text: str) -> str:
    """Wrap 2+ adjacent fenced code blocks with different languages in <CodeGroup>."""
    lines = text.split('\n')
    result = []
    i = 0

    while i < len(lines):
        # Look for a fenced code block start
        if lines[i].strip().startswith('```') and lines[i].strip() != '```':
            # Collect consecutive code blocks (separated by blank lines)
            blocks = []
            block_start = i
            while i < len(lines):
                opening = lines[i].strip()
                if not opening.startswith('```') or opening == '```':
                    break
                # Extract language from opening fence
                lang = opening.lstrip('`').split()[0] if opening.lstrip('`').split() else ''
                # Find the closing ```
                j = i + 1
                while j < len(lines) and lines[j].strip() != '```':
                    j += 1
                if j >= len(lines):
                    break  # unclosed block, bail
                blocks.append({
                    'lang': lang,
                    'start': i,
                    'end': j,  # line with closing ```
                })
                # Skip past closing ``` and any blank lines
                k = j + 1
                while k < len(lines) and lines[k].strip() == '':
                    k += 1
                # Check if next non-blank line is another code block
                if k < len(lines) and lines[k].strip().startswith('```') and lines[k].strip() != '```':
                    i = k
                else:
                    break

            if len(blocks) >= 2:
                # Check if languages differ
                langs = {b['lang'] for b in blocks}
                if len(langs) > 1:
                    # Wrap in CodeGroup
                    first = blocks[0]['start']
                    last = blocks[-1]['end']
                    # Output everything before the first block that we haven't output yet
                    code_lines = lines[first:last + 1]
                    result.append('<CodeGroup>')
                    result.append('')
                    result.extend(code_lines)
                    result.append('')
                    result.append('</CodeGroup>')
                    i = last + 1
                    continue
                else:
                    # Same language — don't wrap, just output normally
                    last = blocks[-1]['end']
                    result.extend(lines[block_start:last + 1])
                    i = last + 1
                    continue
            else:
                # Single block, output normally
                last = blocks[-1]['end'] if blocks else i
                result.extend(lines[block_start:last + 1])
                i = last + 1
                continue

        result.append(lines[i])
        i += 1

    return '\n'.join(result)


def _style_inline_imgs(text: str) -> str:
    """Add inline display styling to <img> tags that appear inline with text.

    Mintlify wraps markdown images ![alt](src) in a block-level zoom component.
    Raw <img> tags with explicit inline styling bypass this and render inline.
    Uses __INLINE_IMG_STYLE__ sentinel which gets replaced after brace escaping.
    Standalone <img> tags (on their own line) are left as-is.
    """
    lines = text.split('\n')
    result = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith('```'):
            in_code_block = not in_code_block

        if in_code_block or '<img ' not in line:
            result.append(line)
            continue

        # Check if the line has content besides <img> tags and whitespace
        without_imgs = re.sub(r'<img\s[^>]*/\s*>', '', line).strip()
        if not without_imgs:
            # Standalone image on its own line — keep as-is
            result.append(line)
            continue

        # Inline image — add inline style sentinel
        def add_inline_style(m):
            tag = m.group(0)
            # Don't double-add if already has a style
            if '__INLINE_IMG_STYLE__' in tag or 'style=' in tag:
                return tag
            # Insert sentinel before the closing />
            return tag.replace(' />', ' __INLINE_IMG_STYLE__ />')

        result.append(re.sub(r'<img\s[^>]*/\s*>', add_inline_style, line))

    return '\n'.join(result)
