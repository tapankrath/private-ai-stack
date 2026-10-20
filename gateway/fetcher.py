"""Isolated web page fetcher for private research (runs on the `research` network only).

The Gateway has no internet route; when a user runs a web search, the Gateway asks this
service for page TEXT. Guards:
  * shared key required (the gateway key)
  * http/https only; every hop (including redirects) must resolve to a PUBLIC address, so the
    fetcher cannot be used to reach the LAN, cloud metadata endpoints or localhost (SSRF)
  * optional domain allowlist (FETCHER_ALLOWED_DOMAINS)
  * size cap, timeout, text/html only; scripts and styles stripped; text returned, never HTML
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request

from .ingest import html_to_text

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 4
UA = "private-ai-fetcher/1.0 (+no tracking)"


def _key() -> str | None:
    p = os.environ.get("FETCHER_KEY_FILE") or os.environ.get("GATEWAY_GATEWAY_KEY_FILE")
    if p and os.path.exists(p):
        return open(p).read().strip()
    return os.environ.get("FETCHER_KEY")


def is_public_host(host: str, resolver=socket.getaddrinfo) -> bool:
    try:
        infos = resolver(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global)):
            return False
    return True


def check_url(url: str, allowed: set[str] | None, resolver=socket.getaddrinfo) -> str:
    u = urlparse(url)
    if u.scheme not in {"http", "https"} or not u.hostname:
        raise ValueError("only http(s) URLs")
    host = u.hostname.lower()
    if allowed and not any(host == d or host.endswith("." + d) for d in allowed):
        raise ValueError("domain not on the allowlist")
    if not is_public_host(host, resolver):
        raise ValueError("refusing private, loopback or unresolvable address")
    return url


def fetch(url: str, allowed: set[str] | None = None, transport: httpx.BaseTransport | None = None,
          resolver=socket.getaddrinfo) -> dict:
    client = httpx.Client(timeout=10, follow_redirects=False, headers={"User-Agent": UA}, transport=transport)
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url, allowed, resolver)
        with client.stream("GET", url) as r:
            if r.status_code in {301, 302, 303, 307, 308} and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            ctype = r.headers.get("content-type", "")
            if r.status_code != 200 or not (ctype.startswith("text/") or "html" in ctype):
                raise ValueError(f"unusable response ({r.status_code}, {ctype or 'no type'})")
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    break
        raw = body[:MAX_BYTES].decode(r.encoding or "utf-8", errors="replace")
        text = html_to_text(raw) if "html" in ctype else raw
        title = ""
        low = raw.lower()
        if "<title>" in low:
            title = raw[low.index("<title>") + 7: low.index("</title>")] if "</title>" in low else ""
        return {"url": url, "title": html_to_text(title)[:200], "text": text[:20000]}
    raise ValueError("too many redirects")


app = FastAPI(docs_url=None, redoc_url=None)


@app.post("/fetch")
async def fetch_endpoint(request: Request):
    key = _key()
    given = (request.headers.get("authorization") or "")[7:]
    if not key or not hmac.compare_digest(given.encode(), key.encode()):
        raise HTTPException(401, "unauthorized")
    body = await request.json()
    allowed = {d.strip().lower() for d in os.environ.get("FETCHER_ALLOWED_DOMAINS", "").split(",") if d.strip()} or None
    try:
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(fetch, str(body.get("url", "")), allowed)
    except (ValueError, httpx.HTTPError) as e:
        raise HTTPException(422, str(e))
