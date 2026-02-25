"""Extract branding assets from a GitBook site."""

import os
import re
import colorsys
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .utils import ensure_dir


@dataclass
class BrandAssets:
    """Extracted branding from a GitBook site."""
    site_name: str = ''
    logo_light_url: Optional[str] = None
    logo_dark_url: Optional[str] = None
    favicon_url: Optional[str] = None
    primary_color: Optional[str] = None
    font_family: Optional[str] = None
    # Local paths after download
    logo_light_path: Optional[str] = None
    logo_dark_path: Optional[str] = None
    favicon_path: Optional[str] = None
    # What was auto-detected vs needs user input
    auto_detected: list = field(default_factory=list)
    needs_input: list = field(default_factory=list)


class BrandingExtractor:
    """Extracts branding assets from a GitBook site."""

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip('/')
        self.session = session

    def extract(self, html: Optional[str] = None) -> BrandAssets:
        """Extract all branding assets from the site."""
        assets = BrandAssets()

        if not html:
            try:
                resp = self.session.get(self.base_url, timeout=15)
                resp.raise_for_status()
                html = resp.text
            except requests.RequestException:
                assets.needs_input = ['site_name', 'logo', 'favicon', 'primary_color', 'font']
                return assets

        soup = BeautifulSoup(html, 'lxml')

        # Extract site name
        assets.site_name = self._extract_site_name(soup)
        if assets.site_name:
            assets.auto_detected.append('site_name')
        else:
            assets.needs_input.append('site_name')

        # Extract logos
        logos = self._extract_logos(soup)
        if logos.get('light'):
            assets.logo_light_url = logos['light']
            assets.auto_detected.append('logo_light')
        else:
            assets.needs_input.append('logo_light')
        if logos.get('dark'):
            assets.logo_dark_url = logos['dark']
            assets.auto_detected.append('logo_dark')
        else:
            assets.needs_input.append('logo_dark')

        # Extract favicon
        assets.favicon_url = self._extract_favicon(soup)
        if assets.favicon_url:
            assets.auto_detected.append('favicon')
        else:
            assets.needs_input.append('favicon')

        # Extract primary color
        assets.primary_color = self._extract_primary_color(soup, html)
        if assets.primary_color:
            assets.auto_detected.append('primary_color')
        else:
            assets.needs_input.append('primary_color')

        # Extract font
        assets.font_family = self._extract_font(soup, html)
        if assets.font_family:
            assets.auto_detected.append('font')
        else:
            assets.needs_input.append('font')

        return assets

    def _extract_site_name(self, soup: BeautifulSoup) -> str:
        """Extract the site/company name."""
        # Try Open Graph title
        og_title = soup.find('meta', property='og:site_name')
        if og_title:
            return og_title.get('content', '').strip()

        # Try the header/navbar logo alt text
        header = soup.find(['header', 'nav'])
        if header:
            logo_img = header.find('img')
            if logo_img and logo_img.get('alt'):
                return logo_img['alt'].strip()

        # Try the <title> tag
        if soup.title:
            title = soup.title.get_text(strip=True)
            # Remove common suffixes
            for sep in [' | ', ' - ', ' — ', ' · ']:
                if sep in title:
                    parts = title.split(sep)
                    return parts[-1].strip()  # Usually site name is last
            return title

        return ''

    def _extract_logos(self, soup: BeautifulSoup) -> dict:
        """Extract logo URLs from the page."""
        logos = {'light': None, 'dark': None}

        # Look in header/navbar for logo images
        for container in [soup.find('header'), soup.find('nav'), soup]:
            if not container:
                continue

            # Find images that look like logos
            for img in container.find_all('img'):
                src = img.get('src', '')
                alt = (img.get('alt', '') or '').lower()
                classes = ' '.join(img.get('class', [])).lower()

                if not src:
                    continue

                is_logo = (
                    'logo' in alt or
                    'logo' in classes or
                    'logo' in src.lower() or
                    'brand' in src.lower()
                )

                if is_logo or (container.name in ('header', 'nav') and not logos['light']):
                    full_url = urljoin(self.base_url, src)

                    # Check if it's a dark or light variant
                    if 'dark' in src.lower() or 'dark' in alt:
                        logos['dark'] = full_url
                    elif 'light' in src.lower() or 'light' in alt or 'white' in src.lower():
                        logos['light'] = full_url
                    else:
                        # Default: use as light mode logo (shown on white bg)
                        if not logos['light']:
                            logos['light'] = full_url

            if logos['light']:
                break

        # Also check for SVG logos in the page
        for svg in soup.find_all('svg'):
            parent = svg.parent
            if parent and parent.name == 'a' and parent.find_parent(['header', 'nav']):
                # This is likely a logo SVG — note it for the user
                if not logos['light']:
                    logos['light'] = 'SVG_INLINE'  # Flag for special handling

        return logos

    def _extract_favicon(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract favicon URL."""
        # Try various favicon link tags
        for rel in ['icon', 'shortcut icon', 'apple-touch-icon']:
            link = soup.find('link', rel=lambda r: r and rel in str(r).lower())
            if link and link.get('href'):
                return urljoin(self.base_url, link['href'])
        return None

    def _extract_primary_color(self, soup: BeautifulSoup, html: str) -> Optional[str]:
        """Extract the primary/brand color from CSS."""
        # Strategy 1: Look for CSS custom properties (most reliable)
        color_patterns = [
            r'--primary[^:]*:\s*(#[0-9a-fA-F]{3,8})',
            r'--brand[^:]*:\s*(#[0-9a-fA-F]{3,8})',
            r'--accent[^:]*:\s*(#[0-9a-fA-F]{3,8})',
            r'--color-primary[^:]*:\s*(#[0-9a-fA-F]{3,8})',
            r'--theme-color[^:]*:\s*(#[0-9a-fA-F]{3,8})',
        ]

        for pattern in color_patterns:
            match = re.search(pattern, html)
            if match:
                color = match.group(1)
                if self._is_valid_hex(color):
                    return color

        # Strategy 2: Check meta theme-color
        meta = soup.find('meta', attrs={'name': 'theme-color'})
        if meta and meta.get('content'):
            color = meta['content'].strip()
            if self._is_valid_hex(color):
                return color

        # Strategy 3: Look for prominent colored elements (links, buttons)
        # Extract from inline styles on header/nav elements
        for el in soup.find_all(['a', 'button', 'header'], limit=20):
            style = el.get('style', '')
            bg_match = re.search(r'background(?:-color)?:\s*(#[0-9a-fA-F]{3,8})', style)
            if bg_match:
                color = bg_match.group(1)
                if self._is_valid_hex(color) and not self._is_grayscale(color):
                    return color

        return None

    def _extract_font(self, soup: BeautifulSoup, html: str) -> Optional[str]:
        """Extract the primary font family."""
        # Strategy 1: Google Fonts link
        for link in soup.find_all('link', href=True):
            href = link['href']
            if 'fonts.googleapis.com' in href:
                # Parse font family from URL
                match = re.search(r'family=([^&:]+)', href)
                if match:
                    font = match.group(1).replace('+', ' ')
                    return font.split('|')[0]  # First font if multiple

        # Strategy 2: @import in style tags
        for style in soup.find_all('style'):
            text = style.get_text()
            match = re.search(r'@import\s+url\([\'"]?.*fonts\.googleapis\.com.*family=([^&\'")+]+)', text)
            if match:
                return match.group(1).replace('+', ' ')

        # Strategy 3: CSS font-family on body
        match = re.search(r'body\s*\{[^}]*font-family:\s*[\'"]?([^\'",;}{]+)', html)
        if match:
            font = match.group(1).strip().strip('"\'')
            # Skip generic fonts
            if font.lower() not in ('serif', 'sans-serif', 'monospace', 'system-ui', 'inherit'):
                return font

        return None

    def download_assets(self, assets: BrandAssets, output_dir: str) -> BrandAssets:
        """Download branding assets to the output directory."""
        images_dir = os.path.join(output_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)

        # Download logo (light)
        if assets.logo_light_url and assets.logo_light_url != 'SVG_INLINE':
            local = self._download_file(assets.logo_light_url, images_dir, 'logo-light')
            if local:
                assets.logo_light_path = f'/images/{os.path.basename(local)}'

        # Download logo (dark)
        if assets.logo_dark_url and assets.logo_dark_url != 'SVG_INLINE':
            local = self._download_file(assets.logo_dark_url, images_dir, 'logo-dark')
            if local:
                assets.logo_dark_path = f'/images/{os.path.basename(local)}'

        # Download favicon
        if assets.favicon_url:
            local = self._download_file(assets.favicon_url, images_dir, 'favicon')
            if local:
                assets.favicon_path = f'/images/{os.path.basename(local)}'

        return assets

    def _download_file(self, url: str, output_dir: str, name_prefix: str) -> Optional[str]:
        """Download a file and save with appropriate extension."""
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"    Warning: Failed to download {url}: {e}")
            return None

        # Determine extension from content type or URL
        content_type = resp.headers.get('content-type', '')
        ext = self._ext_from_content_type(content_type) or self._ext_from_url(url) or '.png'

        filepath = os.path.join(output_dir, f'{name_prefix}{ext}')
        with open(filepath, 'wb') as f:
            f.write(resp.content)

        return filepath

    @staticmethod
    def _ext_from_content_type(ct: str) -> Optional[str]:
        """Get file extension from content type."""
        ct = ct.lower().split(';')[0].strip()
        mapping = {
            'image/png': '.png',
            'image/jpeg': '.jpg',
            'image/gif': '.gif',
            'image/svg+xml': '.svg',
            'image/webp': '.webp',
            'image/x-icon': '.ico',
            'image/vnd.microsoft.icon': '.ico',
        }
        return mapping.get(ct)

    @staticmethod
    def _ext_from_url(url: str) -> Optional[str]:
        """Get file extension from URL."""
        path = url.split('?')[0].split('#')[0]
        for ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico']:
            if path.lower().endswith(ext):
                return ext
        return None

    @staticmethod
    def _is_valid_hex(color: str) -> bool:
        """Check if a string is a valid hex color."""
        return bool(re.match(r'^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$', color))

    @staticmethod
    def _is_grayscale(hex_color: str) -> bool:
        """Check if a hex color is grayscale (and thus not a 'brand' color)."""
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 3:
            hex_color = ''.join(c * 2 for c in hex_color)
        if len(hex_color) < 6:
            return False
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return max(r, g, b) - min(r, g, b) < 20


def generate_dark_variant(hex_color: str) -> str:
    """Generate a lighter variant of a color for dark mode."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c * 2 for c in hex_color)
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    # Make it lighter for dark mode
    l = min(1.0, l + 0.15)
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return f'#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}'
