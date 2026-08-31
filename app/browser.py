"""Headless-browser research tool.

Fetches one specific URL, renders it (JavaScript included), and returns cleaned visible text. This
exists for research classes a plain HTTP GET or a search snippet cannot serve — JS-rendered listings,
maps, menus, pricing pages — where Tavily's crawl either fails or returns stale/incomplete content.

It is deliberately narrow: fetch one URL, return text, nothing else. It never submits forms, clicks
through a flow, or authenticates. A fresh browser is launched per call rather than kept resident, so a
call is slower (roughly one to two seconds of launch overhead) but no session state or cookies can leak
between unrelated calls.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

MAX_CHARS = 6000
TIMEOUT_MS = 20_000

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata"}


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Reject anything that is not a normal public http(s) host.

    This tool fetches agent-supplied URLs from server-side infrastructure, which is a textbook SSRF
    vector — without this check a prompt could induce a fetch of a cloud metadata endpoint or an
    internal service. Resolve the hostname and reject private/loopback/link-local/reserved ranges,
    not just check the literal string, since a hostname can resolve to an internal address.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False, "Only http/https URLs are allowed."
    host = (parsed.hostname or "").lower()
    if not host or host in _BLOCKED_HOSTNAMES:
        return False, "That host is not allowed."
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"Could not resolve host: {exc}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, "That host resolves to a non-public address and is not allowed."
    return True, ""


def browse_page(url: str, *, wait_for_selector: str | None = None) -> dict:
    """Render `url` headlessly and return its visible text, truncated to MAX_CHARS.

    Returns {"url", "title", "text", "truncated"} on success, or {"url", "error"} on failure. Errors
    (blocked host, timeout, navigation failure) are returned as data rather than raised, so a specialist
    or the chat agent can report "could not reach X" instead of the whole turn failing.
    """
    safe, reason = _is_safe_url(url)
    if not safe:
        return {"url": url, "error": reason}

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page(user_agent="CogenResearchBot/1.0 (+venture due diligence)")
                page.set_default_timeout(TIMEOUT_MS)
                page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                if wait_for_selector:
                    try:
                        page.wait_for_selector(wait_for_selector, timeout=5000)
                    except Exception:
                        pass  # best-effort — fall through to whatever rendered in time
                title = page.title()
                text = page.inner_text("body")
            finally:
                browser.close()
    except Exception as exc:
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"}

    text = " ".join(text.split())
    return {"url": url, "title": title, "text": text[:MAX_CHARS], "truncated": len(text) > MAX_CHARS}
