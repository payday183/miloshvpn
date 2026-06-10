from __future__ import annotations

import csv
import ipaddress
from dataclasses import dataclass
from io import StringIO
from urllib.parse import urlsplit


FALLBACK_REALITY_DOMAINS: tuple[str, ...] = (
    "www.kernel.org",
    "www.gov.uk",
    "www.bbc.com",
    "www.mozilla.org",
    "www.python.org",
    "www.nginx.org",
    "www.postgresql.org",
    "www.wikipedia.org",
    "www.google.com",
)
FALLBACK_DOMAIN_RANK = {domain: index for index, domain in enumerate(FALLBACK_REALITY_DOMAINS)}

BLOCKED_DOMAIN_PARTS: tuple[str, ...] = (
    "adult",
    "adservice",
    "adsystem",
    "analytics",
    "amazonaws",
    "api",
    "appspot",
    "appsflyer",
    "azure",
    "bank",
    "billing",
    "casino",
    "cdn",
    "click",
    "cloudfront",
    "cloudflare",
    "doubleclick",
    "download",
    "dzen",
    "facebook",
    "fbcdn",
    "googlevideo",
    "gstatic",
    "googletagmanager",
    "googleusercontent",
    "instagram",
    "login",
    "linkedin",
    "mail",
    "metrics",
    "oauth",
    "payment",
    "paypal",
    "porn",
    "s3",
    "sex",
    "static",
    "storage",
    "telemetry",
    "track",
    "twitter",
    "wallet",
    "whatsapp",
    "youtube",
)


@dataclass(frozen=True)
class DomainCandidate:
    domain: str
    source: str


def normalize_domain(raw: str) -> str | None:
    value = raw.strip().lower()
    if not value:
        return None

    if "://" in value:
        parsed = urlsplit(value)
        value = parsed.hostname or ""
    else:
        value = value.split("/", 1)[0].split(":", 1)[0]

    value = value.strip(".")
    if value.startswith("*."):
        value = value[2:]
    if not value or " " in value or "_" in value:
        return None

    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError:
        return None

    if len(value) > 253 or "." not in value:
        return None
    if any(not label or len(label) > 63 for label in value.split(".")):
        return None
    if any(label.startswith("-") or label.endswith("-") for label in value.split(".")):
        return None
    try:
        ipaddress.ip_address(value)
        return None
    except ValueError:
        return value


def is_domain_allowed(domain: str) -> bool:
    normalized = normalize_domain(domain)
    if normalized is None:
        return False
    labels = normalized.split(".")
    joined = ".".join(labels)
    if any(part in joined for part in BLOCKED_DOMAIN_PARTS):
        return False
    if labels[-1] in {"local", "internal", "lan", "onion", "test"}:
        return False
    return True


def domain_variants(domain: str) -> list[str]:
    normalized = normalize_domain(domain)
    if normalized is None:
        return []
    variants = [normalized]
    if not normalized.startswith("www."):
        variants.append(f"www.{normalized}")
    return [variant for variant in variants if is_domain_allowed(variant)]


def parse_ranked_csv_domains(text: str, *, source: str, limit: int) -> list[DomainCandidate]:
    candidates: list[DomainCandidate] = []
    reader = csv.reader(StringIO(text))
    domain_index: int | None = None
    for row in reader:
        if len(candidates) >= limit:
            break
        if not row:
            continue
        lowered = [column.strip().lower() for column in row]
        if domain_index is None and "domain" in lowered:
            domain_index = lowered.index("domain")
            continue
        if domain_index is not None and len(row) > domain_index:
            domain = row[domain_index]
        elif len(row) > 2 and row[0].strip().isdigit() and row[1].strip().isdigit():
            domain = row[2]
        elif len(row) > 1 and row[0].strip().isdigit():
            domain = row[1]
        else:
            domain = row[0]
        normalized = normalize_domain(domain)
        if normalized and is_domain_allowed(normalized):
            candidates.append(DomainCandidate(normalized, source))
    return candidates


def dedupe_candidates(candidates: list[DomainCandidate], *, limit: int) -> list[DomainCandidate]:
    seen: set[str] = set()
    deduped: list[DomainCandidate] = []
    for candidate in candidates:
        for variant in domain_variants(candidate.domain):
            if variant in seen:
                continue
            seen.add(variant)
            deduped.append(DomainCandidate(variant, candidate.source))
            if len(deduped) >= limit:
                return sorted(deduped, key=lambda item: domain_score(item.domain))
    return sorted(deduped, key=lambda item: domain_score(item.domain))


def domain_score(domain: str) -> tuple[int, str]:
    if domain in FALLBACK_REALITY_DOMAINS:
        return (0, f"{FALLBACK_DOMAIN_RANK[domain]:04d}:{domain}")
    if domain.endswith(".org"):
        return (1, domain)
    if domain.endswith(".gov") or domain.endswith(".gov.uk"):
        return (2, domain)
    if domain.endswith(".net"):
        return (3, domain)
    if domain.endswith(".edu"):
        return (4, domain)
    return (5, domain)
