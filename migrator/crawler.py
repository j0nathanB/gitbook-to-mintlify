"""Discover all pages from a GitBook site."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse, urljoin
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .utils import url_to_filepath


@dataclass
class PageInfo:
    """Represents a discovered page."""
    url: str
    title: str
    path: str  # relative file path for output
    parent: Optional[str] = None
    order: int = 0


@dataclass
class NavItem:
    """Represents a navigation item (page or group)."""
    title: str
    path: Optional[str] = None  # None for groups
    url: Optional[str] = None
    children: list = field(default_factory=list)


class GitBookCrawler:
    """Discovers pages and navigation structure from a GitBook site."""

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip('/')
        self.session = session
        self.pages: list[PageInfo] = []
        self.nav_tree: list[NavItem] = []

    def crawl(self) -> tuple[list[PageInfo], list[NavItem]]:
        """Main entry point. Returns (pages, nav_tree)."""
        print(f"  Crawling {self.base_url}...")

        # Try sitemap first for page discovery
        sitemap_urls = self._try_sitemap()

        # Always parse navigation from the homepage for structure
        homepage_html = self._fetch(self.base_url)
        if homepage_html:
            self.nav_tree = self._parse_navigation(homepage_html)

        if sitemap_urls:
            print(f"  Found {len(sitemap_urls)} pages via sitemap")
            self._build_pages_from_sitemap(sitemap_urls)
        elif self.nav_tree:
            print(f"  Building page list from navigation sidebar")
            self._build_pages_from_nav(self.nav_tree)
        else:
            # Last resort: crawl links from homepage
            print(f"  Falling back to link crawling")
            self._crawl_links(homepage_html)

        print(f"  Discovered {len(self.pages)} pages")
        return self.pages, self.nav_tree

    def _fetch(self, url: str) -> Optional[str]:
        """Fetch a URL and return HTML content."""
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"  Warning: Failed to fetch {url}: {e}")
            return None

    def _try_sitemap(self) -> list[str]:
        """Try to fetch and parse sitemap.xml."""
        urls = []
        for sitemap_path in ['/sitemap.xml', '/sitemap-0.xml']:
            sitemap_url = self.base_url + sitemap_path
            content = self._fetch(sitemap_url)
            if content:
                try:
                    root = ET.fromstring(content)
                    ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                    for loc in root.findall('.//ns:loc', ns):
                        if loc.text:
                            urls.append(loc.text.strip())
                except ET.ParseError:
                    continue
                if urls:
                    break
        return urls

    def _parse_navigation(self, html: str) -> list[NavItem]:
        """Extract navigation structure from the sidebar."""
        soup = BeautifulSoup(html, 'lxml')
        nav_tree = []

        # GitBook navigation selectors (try multiple patterns)
        nav_selectors = [
            'nav[aria-label*="Table of contents"]',
            'nav[aria-label*="table of contents"]',
            'aside nav',
            '[data-testid="table-of-contents"]',
            '[class*="sidebar"] nav',
            '[class*="navigation"]',
            'nav',
        ]

        nav_el = None
        for selector in nav_selectors:
            nav_el = soup.select_one(selector)
            if nav_el:
                break

        if not nav_el:
            return nav_tree

        # Parse the nav structure
        # GitBook typically uses nested divs or lists for navigation
        nav_tree = self._parse_nav_element(nav_el)
        return nav_tree

    def _parse_nav_element(self, element) -> list[NavItem]:
        """Recursively parse a navigation element into NavItems."""
        items = []

        # Look for links and group headers
        # GitBook uses various structures — handle the common ones

        # Pattern 1: <ul>/<li> based navigation
        for li in element.find_all('li', recursive=False):
            item = self._parse_nav_li(li)
            if item:
                items.append(item)

        if items:
            return items

        # Pattern 2: <a> tags with optional group dividers
        # GitBook often uses div-based structures
        current_group = None
        for child in element.children:
            if not hasattr(child, 'name') or child.name is None:
                continue

            # Check if it's a group header (non-link text)
            link = child.find('a') if child.name != 'a' else child

            if child.name in ('div', 'section'):
                # Could be a group
                group_title_el = child.find(
                    ['span', 'p', 'div'],
                    string=True,
                    recursive=False
                )
                sub_links = child.find_all('a', href=True)

                if group_title_el and sub_links:
                    group = NavItem(
                        title=group_title_el.get_text(strip=True),
                    )
                    for a in sub_links:
                        href = a.get('href', '')
                        title = a.get_text(strip=True)
                        if title and href:
                            url = urljoin(self.base_url, href)
                            group.children.append(NavItem(
                                title=title,
                                path=url_to_filepath(url, self.base_url),
                                url=url,
                            ))
                    if group.children:
                        items.append(group)
                elif sub_links:
                    # Flat list of links in a div
                    for a in sub_links:
                        href = a.get('href', '')
                        title = a.get_text(strip=True)
                        if title and href:
                            url = urljoin(self.base_url, href)
                            items.append(NavItem(
                                title=title,
                                path=url_to_filepath(url, self.base_url),
                                url=url,
                            ))
                else:
                    # Recurse into div
                    sub_items = self._parse_nav_element(child)
                    items.extend(sub_items)

            elif link and link.name == 'a':
                href = link.get('href', '')
                title = link.get_text(strip=True)
                if title and href:
                    url = urljoin(self.base_url, href)
                    items.append(NavItem(
                        title=title,
                        path=url_to_filepath(url, self.base_url),
                        url=url,
                    ))

        return items

    def _parse_nav_li(self, li) -> Optional[NavItem]:
        """Parse a single <li> navigation element."""
        link = li.find('a', recursive=False)
        if not link:
            link = li.find('a')

        if not link:
            return None

        title = link.get_text(strip=True)
        href = link.get('href', '')
        url = urljoin(self.base_url, href) if href else None
        path = url_to_filepath(url, self.base_url) if url else None

        item = NavItem(title=title, path=path, url=url)

        # Check for nested list (sub-pages)
        sub_list = li.find(['ul', 'ol'], recursive=False)
        if sub_list:
            for sub_li in sub_list.find_all('li', recursive=False):
                child = self._parse_nav_li(sub_li)
                if child:
                    item.children.append(child)

        return item

    def _build_pages_from_sitemap(self, urls: list[str]):
        """Create PageInfo objects from sitemap URLs."""
        base_netloc = urlparse(self.base_url).netloc
        for i, url in enumerate(urls):
            parsed = urlparse(url)
            if parsed.netloc != base_netloc:
                continue
            path = url_to_filepath(url, self.base_url)
            title = path.split('/')[-1].replace('-', ' ').title() if path else 'Home'
            self.pages.append(PageInfo(
                url=url,
                title=title,
                path=path or 'index',
                order=i,
            ))

    def _build_pages_from_nav(self, nav_items: list[NavItem], order_start: int = 0):
        """Create PageInfo objects from navigation tree."""
        order = order_start
        for item in nav_items:
            if item.url and item.path:
                self.pages.append(PageInfo(
                    url=item.url,
                    title=item.title,
                    path=item.path,
                    order=order,
                ))
                order += 1
            for child in item.children:
                if child.url and child.path:
                    self.pages.append(PageInfo(
                        url=child.url,
                        title=child.title,
                        path=child.path,
                        order=order,
                    ))
                    order += 1
                if child.children:
                    self._build_pages_from_nav(child.children, order)

    def _crawl_links(self, html: Optional[str]):
        """Fallback: discover pages by crawling links from homepage."""
        if not html:
            return
        soup = BeautifulSoup(html, 'lxml')
        base_netloc = urlparse(self.base_url).netloc
        seen = set()

        for a in soup.find_all('a', href=True):
            href = a['href']
            url = urljoin(self.base_url, href)
            parsed = urlparse(url)

            if parsed.netloc != base_netloc:
                continue
            if url in seen:
                continue
            if parsed.fragment:
                url = url.split('#')[0]
            if url in seen:
                continue

            seen.add(url)
            path = url_to_filepath(url, self.base_url)
            title = a.get_text(strip=True) or path.split('/')[-1].replace('-', ' ').title()

            if path and title:
                self.pages.append(PageInfo(
                    url=url,
                    title=title,
                    path=path,
                    order=len(self.pages),
                ))
