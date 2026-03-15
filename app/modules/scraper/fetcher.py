import io
import logging
import re
import ssl
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import pdfplumber

from app.core.config import Settings

logger = logging.getLogger(__name__)

_USER_AGENT = "Prodculator-Scraper/1.0 (+https://prodculator.com)"

# Tags whose entire content block should be removed before stripping
_STRIP_TAGS = re.compile(
    r"<(script|style|nav|footer|header|aside|noscript)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\n{3,}")

# Cache robots.txt results per origin for the lifetime of the process
_robots_cache: dict[str, RobotFileParser | None] = {}


def _is_ssl_error(exc: BaseException) -> bool:
    """Return True if *exc* (or its chain) is an SSL certificate error.

    httpx wraps SSL failures in ``ConnectError`` and may not preserve the
    original ``ssl.SSLError`` in ``__cause__``.  We therefore also check
    the string representation of each exception in the chain.
    """
    cur: BaseException | None = exc
    while cur is not None:
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        if isinstance(cur, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(cur):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(cur):
            return True
        cur = cur.__cause__ if cur.__cause__ is not cur else None
    return False


def _make_client(timeout: int, *, verify: bool = True) -> httpx.Client:
    """Create an httpx Client, optionally disabling SSL verification."""
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        verify=verify,
    )


def _check_robots_txt(url: str, timeout: int = 10) -> bool:
    """Return True if robots.txt allows our user-agent to fetch *url*."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    if origin not in _robots_cache:
        robots_url = f"{origin}/robots.txt"
        rp = RobotFileParser()
        try:
            with _make_client(timeout) as client:
                resp = client.get(robots_url, headers={"User-Agent": _USER_AGENT})
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    # No robots.txt or error → assume allowed
                    rp = None
        except Exception as exc:
            if _is_ssl_error(exc):
                # SSL cert issue — retry without verification so we still
                # respect robots.txt rather than skipping the check entirely.
                try:
                    rp_retry = RobotFileParser()
                    with _make_client(timeout, verify=False) as client:
                        resp = client.get(robots_url, headers={"User-Agent": _USER_AGENT})
                        if resp.status_code == 200:
                            rp_retry.parse(resp.text.splitlines())
                            rp = rp_retry
                        else:
                            rp = None
                except Exception:
                    rp = None
            else:
                rp = None
        _robots_cache[origin] = rp

    rp = _robots_cache[origin]
    if rp is None:
        return True
    return rp.can_fetch(_USER_AGENT, url)


def fetch_and_strip(url: str, settings: Settings) -> str | None:
    """Fetch URL and return cleaned plain text. Returns None on failure."""
    if not _check_robots_txt(url, timeout=settings.SCRAPER_REQUEST_TIMEOUT):
        logger.warning("Blocked by robots.txt: %s", url)
        return None

    try:
        with _make_client(settings.SCRAPER_REQUEST_TIMEOUT) as client:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        if _is_ssl_error(exc):
            logger.info("SSL verification failed for %s, retrying without verify", url)
            try:
                with _make_client(settings.SCRAPER_REQUEST_TIMEOUT, verify=False) as client:
                    resp = client.get(url, headers={"User-Agent": _USER_AGENT})
                    resp.raise_for_status()
                    html = resp.text
            except Exception as retry_exc:
                logger.warning("Fetch failed for %s (SSL retry): %s", url, retry_exc)
                return None
        else:
            logger.warning("Fetch failed for %s: %s", url, exc)
            return None

    # Strip noisy blocks first
    text = _STRIP_TAGS.sub("", html)
    # Remove remaining tags
    text = _TAG_RE.sub(" ", text)
    # Collapse whitespace
    text = _WHITESPACE_RE.sub("\n\n", text).strip()
    # Truncate to stay within AI model context limits
    if len(text) > settings.SCRAPER_MAX_TEXT_CHARS:
        text = text[: settings.SCRAPER_MAX_TEXT_CHARS] + "\n\n[Content truncated]"
    return text


def fetch_pdf_text(url: str, settings: Settings) -> str | None:
    """Fetch a PDF from *url* and extract text from all pages.

    For pages that contain rate-card-style tables, pdfplumber's
    ``extract_tables()`` output is appended as tab-separated rows so that
    the downstream AI extractor can parse structured rate data.

    Returns plain text or None on failure.
    """
    if not _check_robots_txt(url, timeout=settings.SCRAPER_REQUEST_TIMEOUT):
        logger.warning("Blocked by robots.txt: %s", url)
        return None

    try:
        with _make_client(settings.SCRAPER_REQUEST_TIMEOUT) as client:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            pdf_bytes = resp.content
    except Exception as exc:
        if _is_ssl_error(exc):
            try:
                with _make_client(settings.SCRAPER_REQUEST_TIMEOUT, verify=False) as client:
                    resp = client.get(url, headers={"User-Agent": _USER_AGENT})
                    resp.raise_for_status()
                    pdf_bytes = resp.content
            except Exception as retry_exc:
                logger.warning("PDF fetch failed for %s (SSL retry): %s", url, retry_exc)
                return None
        else:
            logger.warning("PDF fetch failed for %s: %s", url, exc)
            return None

    try:
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                # Extract tables first (better for rate cards)
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            cells = [str(c).strip() if c else "" for c in row]
                            parts.append("\t".join(cells))
                    parts.append("")  # blank line between tables
                else:
                    # Fall back to plain text extraction
                    page_text = page.extract_text()
                    if page_text:
                        parts.append(page_text)

        text = "\n".join(parts).strip()
        if not text:
            logger.warning("No text extracted from PDF: %s", url)
            return None

        if len(text) > settings.SCRAPER_MAX_TEXT_CHARS:
            text = text[: settings.SCRAPER_MAX_TEXT_CHARS] + "\n\n[Content truncated]"
        return text
    except Exception as exc:
        logger.warning("PDF text extraction failed for %s: %s", url, exc)
        return None


def fetch_pdf_links(url: str, settings: Settings) -> list[str]:
    """Fetch an HTML page and return all PDF links found on it.

    Useful for index pages like BECTU rate cards or IATSE rates
    that link to individual PDF documents.
    """
    if not _check_robots_txt(url, timeout=settings.SCRAPER_REQUEST_TIMEOUT):
        logger.warning("Blocked by robots.txt: %s", url)
        return []

    try:
        with _make_client(settings.SCRAPER_REQUEST_TIMEOUT) as client:
            resp = client.get(url, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        if _is_ssl_error(exc):
            try:
                with _make_client(settings.SCRAPER_REQUEST_TIMEOUT, verify=False) as client:
                    resp = client.get(url, headers={"User-Agent": _USER_AGENT})
                    resp.raise_for_status()
                    html = resp.text
            except Exception as retry_exc:
                logger.warning("Fetch failed for PDF link page %s (SSL retry): %s", url, retry_exc)
                return []
        else:
            logger.warning("Fetch failed for PDF link page %s: %s", url, exc)
            return []

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    pdf_links: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']*\.pdf[^"\']*)', html, re.IGNORECASE):
        href = match.group(1)
        if href.startswith("//"):
            href = f"{parsed.scheme}:{href}"
        elif href.startswith("/"):
            href = f"{base}{href}"
        elif not href.startswith("http"):
            href = f"{base}/{href}"
        pdf_links.append(href)

    return pdf_links
