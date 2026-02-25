"""Scrape individual page content from a GitBook site."""

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag


@dataclass
class PageContent:
    """Scraped content from a single page."""
    url: str
    title: str
    description: str
    html_content: Tag  # The main content element
    raw_html: str


class GitBookScraper:
    """Scrapes content from individual GitBook pages."""

    # Selectors for the main content area (tried in order)
    CONTENT_SELECTORS = [
        '[data-testid="page.contentEditor"]',
        '[class*="page-body"]',
        'main [class*="content"]',
        '[class*="markdown-body"]',
        'main article',
        'article',
        'main',
        '[role="main"]',
        '.page-inner',
        '#page-wrapper .page-inner',
    ]

    # Selectors for the page title
    TITLE_SELECTORS = [
        '[data-testid="page.title"]',
        'main h1:first-of-type',
        'article h1:first-of-type',
        'h1',
    ]

    # Selectors for page description
    DESC_SELECTORS = [
        '[data-testid="page.description"]',
        'meta[name="description"]',
        'meta[property="og:description"]',
    ]

    def __init__(self, session: requests.Session):
        self.session = session

    def scrape_page(self, url: str) -> Optional[PageContent]:
        """Scrape a single page and return its content."""
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"    Warning: Failed to fetch {url}: {e}")
            return None

        soup = BeautifulSoup(resp.text, 'lxml')

        # Extract title
        title = self._extract_title(soup)

        # Extract description
        description = self._extract_description(soup)

        # Extract main content
        content = self._extract_content(soup)

        if not content:
            print(f"    Warning: No content found on {url}")
            return None

        return PageContent(
            url=url,
            title=title,
            description=description,
            html_content=content,
            raw_html=resp.text,
        )

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract the page title."""
        for selector in self.TITLE_SELECTORS:
            el = soup.select_one(selector)
            if el:
                return el.get_text(strip=True)

        # Fallback to <title> tag
        if soup.title:
            title = soup.title.get_text(strip=True)
            # Remove common suffixes like " | Company Docs"
            if ' | ' in title:
                title = title.split(' | ')[0]
            elif ' - ' in title:
                title = title.split(' - ')[0]
            return title

        return 'Untitled'

    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Extract the page description."""
        for selector in self.DESC_SELECTORS:
            el = soup.select_one(selector)
            if el:
                if el.name == 'meta':
                    return el.get('content', '')
                return el.get_text(strip=True)
        return ''

    def _extract_content(self, soup: BeautifulSoup) -> Optional[Tag]:
        """Extract the main content element."""
        for selector in self.CONTENT_SELECTORS:
            el = soup.select_one(selector)
            if el and len(el.get_text(strip=True)) > 50:
                return el

        # Fallback: find the largest text block
        candidates = soup.find_all(['main', 'article', 'div'])
        best = None
        best_len = 0
        for el in candidates:
            text_len = len(el.get_text(strip=True))
            if text_len > best_len:
                best = el
                best_len = text_len

        return best
