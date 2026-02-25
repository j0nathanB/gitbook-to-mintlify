"""Shared utilities for the GitBook to Mintlify migrator."""

import re
import os
from urllib.parse import urlparse, urljoin


def sanitize_filename(text: str) -> str:
    """Convert a title or URL path into a clean filename."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def url_to_filepath(url: str, base_url: str) -> str:
    """Convert a full URL to a relative file path for Mintlify."""
    parsed = urlparse(url)
    path = parsed.path.strip('/')
    if not path:
        return 'index'
    # Remove trailing slashes and file extensions
    path = re.sub(r'\.(html|htm)$', '', path)
    # Clean each segment
    segments = [sanitize_filename(seg) for seg in path.split('/') if seg]
    return '/'.join(segments)


def resolve_url(href: str, page_url: str, base_url: str) -> str:
    """Resolve a relative URL to absolute."""
    if href.startswith(('http://', 'https://', '//')):
        return href
    return urljoin(page_url, href)


def is_internal_link(href: str, base_url: str) -> bool:
    """Check if a link points to the same GitBook site."""
    if not href or href.startswith(('#', 'mailto:', 'tel:', 'javascript:')):
        return False
    resolved = urljoin(base_url, href)
    return urlparse(resolved).netloc == urlparse(base_url).netloc


def ensure_dir(path: str):
    """Create directory if it doesn't exist."""
    os.makedirs(os.path.dirname(path), exist_ok=True)


def slugify(text: str) -> str:
    """Create a URL-safe slug from text."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    return text.strip('-')
