from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

MAX_PAGE_BYTES = 1_000_000
MAX_PAGE_TEXT = 30_000
MAX_REDIRECTS = 4


async def fetch_vacancy_page_text(url: str) -> str | None:
    """Fetch readable text from a public HTTPS vacancy page, when possible."""
    if not _is_public_https_url(url):
        return None
    timeout = httpx.Timeout(12.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "job-assistant/1.0"}) as client:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            if not _is_public_https_url(current_url):
                return None
            request = client.build_request("GET", current_url)
            response = await client.send(request, stream=True, follow_redirects=False)
            if response.is_redirect:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    return None
                current_url = urljoin(current_url, location)
                continue
            if response.status_code != 200 or "text/html" not in response.headers.get("content-type", "").lower():
                await response.aclose()
                return None
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_PAGE_BYTES:
                    await response.aclose()
                    return None
                chunks.append(chunk)
            await response.aclose()
            soup = BeautifulSoup(b"".join(chunks), "html.parser")
            for element in soup(["script", "style", "noscript", "svg"]):
                element.decompose()
            text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
            return text[:MAX_PAGE_TEXT] or None
    return None


def _is_public_https_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".test", ".invalid")):
            return False
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            resolved = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(item[4][0]) for item in resolved]
        return bool(addresses) and all(address.is_global for address in addresses)
    except (OSError, ValueError):
        return False
