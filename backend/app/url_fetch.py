"""Safe URL fetching and text extraction for job posting links."""
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

MAX_URL_LEN = 2048
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 10
MAX_REDIRECTS = 3
MAX_TEXT_CHARS = 20000
MIN_TEXT_CHARS = 200
HEADERS = {"User-Agent": "FakeJobDetector/1.0 (academic project)"}


class UrlFetchError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _check_host(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise UrlFetchError("Could not resolve the website address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise UrlFetchError("This address is not allowed.")


def validate_url(url):
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LEN:
        raise UrlFetchError("Please enter a valid link (up to 2048 characters).")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UrlFetchError("Only http and https links are supported.")
    _check_host(parsed.hostname)
    return url


def fetch_html(url):
    current = validate_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            resp = requests.get(current, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False, stream=True)
        except requests.exceptions.RequestException:
            raise UrlFetchError("Could not fetch that page (timeout or connection error).")
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise UrlFetchError("The website sent an invalid redirect.")
                current = validate_url(urljoin(current, location))
                continue
            if resp.status_code != 200:
                raise UrlFetchError(f"The website returned status {resp.status_code}. Try pasting the text instead.")
            if "text/html" not in resp.headers.get("Content-Type", "").lower():
                raise UrlFetchError("That link is not a web page (HTML).")
            data = bytearray()
            for chunk in resp.iter_content(8192):
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise UrlFetchError("That page is too large to analyse.")
            return bytes(data)
        finally:
            resp.close()
    raise UrlFetchError("Too many redirects.")


def extract_text(html_bytes):
    soup = BeautifulSoup(html_bytes, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    if title:
        text = f"{title}. {text}"
    return text[:MAX_TEXT_CHARS]


def fetch_posting_text(url):
    text = extract_text(fetch_html(url))
    if len(text) < MIN_TEXT_CHARS:
        raise UrlFetchError("Could not read enough text from that page. Many job sites (such as LinkedIn and Naukri) load content with JavaScript or require login. Please paste the posting text instead.")
    return text
