"""Email and website parsing, classification, and safe-host checks."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

import tldextract

# Offline public-suffix snapshot: no network calls at runtime. Correctly handles
# multi-part suffixes like .co.uk, .com.au, .github.io, which a naive
# "split on the last dot" regex (e.g. the original blueprint's
# `(?<=@)[^.]+\.[^.]+`) gets wrong.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

PERSONAL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "yahoo.com.ph", "ymail.com",
        "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com", "icloud.com",
        "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me", "gmx.com",
        "gmx.net", "mail.com", "zoho.com", "yandex.com", "yandex.ru", "qq.com", "163.com",
        "126.com", "fastmail.com", "hey.com",
    }
)

DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
        "temp-mail.org", "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com",
        "throwawaymail.com", "dispostable.com", "maildrop.cc",
    }
)

_EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$")
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".corp", ".home", ".intranet")


class EmailClass(str, Enum):
    BUSINESS = "business"
    PERSONAL = "personal"
    DISPOSABLE = "disposable"
    INVALID = "invalid"


@dataclass(frozen=True)
class ParsedEmail:
    raw: str
    local: str
    host: str               # full host after "@", lowercased
    registered_domain: str  # e.g. "acme.co.uk" even for "mail.eu.acme.co.uk"
    email_class: EmailClass


def registered_domain(host: str) -> str:
    """Return the registrable domain for a host ('' if it has no valid public suffix)."""
    ext = _EXTRACT(host)
    if not ext.domain or not ext.suffix:
        return ""
    return f"{ext.domain}.{ext.suffix}"


def parse_email(email: str) -> ParsedEmail:
    raw = (email or "").strip()
    if not _EMAIL_RE.match(raw) or len(raw) > 254:
        return ParsedEmail(raw, "", "", "", EmailClass.INVALID)
    local, host = raw.rsplit("@", 1)
    host = host.lower().rstrip(".")
    reg = registered_domain(host)
    if not reg:
        return ParsedEmail(raw, local, host, "", EmailClass.INVALID)
    if reg in DISPOSABLE_DOMAINS:
        cls = EmailClass.DISPOSABLE
    elif reg in PERSONAL_DOMAINS:
        cls = EmailClass.PERSONAL
    else:
        cls = EmailClass.BUSINESS
    return ParsedEmail(raw, local, host, reg, cls)


def is_safe_public_host(host: str) -> bool:
    """Reject hosts that must never be fetched by an enrichment step (SSRF guard)."""
    host = (host or "").strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return bool(registered_domain(host))  # a normal hostname with a real public suffix
    return ip.is_global  # literal IPs are only acceptable if publicly routable


def normalize_website(value: str) -> str:
    """Return the registrable domain of a user-supplied website, or '' if unusable/unsafe."""
    value = (value or "").strip()
    if not value or len(value) > 253 + 50:
        return ""
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if not is_safe_public_host(host):
        return ""
    return registered_domain(host)


def mask_email(email: str) -> str:
    """'jane.doe@acme.io' -> 'j***@acme.io' for logs and screenshots."""
    if "@" not in email:
        return "***"
    local, host = email.rsplit("@", 1)
    return f"{local[:1]}***@{host}"
