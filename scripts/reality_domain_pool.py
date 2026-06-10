#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile
from io import BytesIO

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR))

from app.services.reality_domains import (  # noqa: E402
    DomainCandidate,
    FALLBACK_REALITY_DOMAINS,
    dedupe_candidates,
    parse_ranked_csv_domains,
)


SOURCE_URLS = {
    "tranco": "https://tranco-list.eu/top-1m.csv.zip",
    "majestic": "https://downloads.majestic.com/majestic_million.csv",
    "cisco": "https://s3-us-west-1.amazonaws.com/umbrella-static/top-1m.csv.zip",
}

CHECKER_CODE = r"""
import json
import socket
import ssl
import sys


def check_domain(domain):
    result = {
        "domain": domain,
        "ok": False,
        "reason": "",
        "addresses": [],
        "tls_version": "",
        "https_status": None,
    }
    try:
        infos = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
        addresses = []
        for info in infos:
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)
        result["addresses"] = addresses[:6]
        if not addresses:
            result["reason"] = "dns_no_addresses"
            return result
    except Exception as exc:
        result["reason"] = f"dns_error:{exc}"
        return result

    context = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["http/1.1"])

    try:
        with socket.create_connection((domain, 443), timeout=7) as raw:
            with context.wrap_socket(raw, server_hostname=domain) as tls:
                result["tls_version"] = tls.version() or ""
                request = (
                    f"HEAD / HTTP/1.1\r\n"
                    f"Host: {domain}\r\n"
                    "User-Agent: miloshvpn-reality-check/1.0\r\n"
                    "Connection: close\r\n\r\n"
                )
                tls.sendall(request.encode("ascii"))
                data = tls.recv(512)
    except ssl.SSLCertVerificationError as exc:
        result["reason"] = f"cert_error:{exc.verify_message}"
        return result
    except Exception as exc:
        result["reason"] = f"connect_or_tls_error:{exc}"
        return result

    first_line = data.splitlines()[0].decode("latin1", "replace") if data else ""
    parts = first_line.split()
    if len(parts) >= 2 and parts[0].startswith("HTTP/"):
        try:
            status = int(parts[1])
        except ValueError:
            status = None
        result["https_status"] = status
        if status is not None and 200 <= status < 500:
            result["ok"] = True
            return result
        result["reason"] = f"http_status:{status}"
        return result

    result["reason"] = "no_http_response"
    return result


payload = json.load(sys.stdin)
for domain in payload["domains"]:
    print(json.dumps(check_domain(domain), sort_keys=True), flush=True)
"""


def fetch_text(url: str, *, timeout: int) -> str:
    request = Request(url, headers={"User-Agent": "miloshvpn-reality-domain-pool/1.0"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read()
    if zipfile.is_zipfile(BytesIO(data)):
        with zipfile.ZipFile(BytesIO(data)) as archive:
            name = next(item for item in archive.namelist() if not item.endswith("/"))
            return archive.read(name).decode("utf-8", "replace")
    return data.decode("utf-8", "replace")


def load_source(source: str, *, limit: int, timeout: int) -> list[DomainCandidate]:
    if source == "fallback":
        return [DomainCandidate(domain, source) for domain in FALLBACK_REALITY_DOMAINS[:limit]]

    if source == "cloudflare":
        url = os.environ.get("REALITY_CLOUDFLARE_RADAR_URL", "").strip()
        if not url:
            raise RuntimeError("REALITY_CLOUDFLARE_RADAR_URL is not set")
    else:
        url = SOURCE_URLS[source]

    text = fetch_text(url, timeout=timeout)
    return parse_ranked_csv_domains(text, source=source, limit=limit)


def collect_candidates(sources: list[str], *, source_limit: int, max_check: int, timeout: int) -> tuple[list[DomainCandidate], list[str]]:
    candidates: list[DomainCandidate] = []
    errors: list[str] = []

    for source in sources:
        try:
            loaded = load_source(source, limit=source_limit, timeout=timeout)
        except (RuntimeError, URLError, TimeoutError, OSError, StopIteration, zipfile.BadZipFile) as exc:
            errors.append(f"{source}: {exc}")
            continue
        candidates.extend(loaded)

    if not any(candidate.source == "fallback" for candidate in candidates):
        candidates.extend(DomainCandidate(domain, "fallback") for domain in FALLBACK_REALITY_DOMAINS)

    return dedupe_candidates(candidates, limit=max_check), errors


def run_x3ui_checker(domains: list[str], *, compose: list[str], service: str) -> list[dict[str, object]]:
    command = [*compose, "exec", "-T", service, "python3", "-c", CHECKER_CODE]
    process = subprocess.run(
        command,
        cwd=REPO_DIR,
        input=json.dumps({"domains": domains}),
        text=True,
        capture_output=True,
        check=False,
        timeout=max(30, len(domains) * 10),
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip() or f"checker exited {process.returncode}")

    results: list[dict[str, object]] = []
    for line in process.stdout.splitlines():
        if not line.strip():
            continue
        results.append(json.loads(line))
    return results


def write_outputs(output_dir: Path, candidates: list[DomainCandidate], results: list[dict[str, object]], errors: list[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_by_domain = {candidate.domain: candidate.source for candidate in candidates}
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    verified = [result for result in results if result.get("ok") is True]
    bad = [result for result in results if result.get("ok") is not True]

    (output_dir / "verified.txt").write_text(
        "".join(f"{item['domain']}\t{source_by_domain.get(str(item['domain']), 'unknown')}\n" for item in verified),
        encoding="utf-8",
    )
    (output_dir / "bad.txt").write_text(
        "".join(f"{item['domain']}\t{item.get('reason', 'unknown')}\n" for item in bad),
        encoding="utf-8",
    )
    (output_dir / "source-errors.txt").write_text("\n".join(errors) + ("\n" if errors else ""), encoding="utf-8")

    active = {
        "checked_at": checked_at,
        "domain": verified[0]["domain"] if verified else None,
        "source": source_by_domain.get(str(verified[0]["domain"]), "unknown") if verified else None,
        "verified_count": len(verified),
        "bad_count": len(bad),
    }
    (output_dir / "active.json").write_text(json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and verify admin Reality domain pool from the x3-ui container.")
    parser.add_argument(
        "--sources",
        default="tranco,majestic,cloudflare,cisco,fallback",
        help="Comma-separated sources: tranco, majestic, cloudflare, cisco, fallback.",
    )
    parser.add_argument("--source-limit", type=int, default=1000, help="Max raw domains to read per source.")
    parser.add_argument("--max-check", type=int, default=100, help="Max filtered domains to check from x3-ui.")
    parser.add_argument("--download-timeout", type=int, default=25)
    parser.add_argument("--output-dir", default="var/reality-domains")
    parser.add_argument("--x3ui-service", default="x3ui")
    parser.add_argument("--compose", default="docker compose", help="Compose command, for example: 'docker compose'.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sources = [source.strip() for source in args.sources.split(",") if source.strip()]
    unknown = sorted(set(sources) - {"tranco", "majestic", "cloudflare", "cisco", "fallback"})
    if unknown:
        raise SystemExit(f"Unknown sources: {', '.join(unknown)}")

    candidates, errors = collect_candidates(
        sources,
        source_limit=max(1, args.source_limit),
        max_check=max(1, args.max_check),
        timeout=max(1, args.download_timeout),
    )
    results = run_x3ui_checker(
        [candidate.domain for candidate in candidates],
        compose=args.compose.split(),
        service=args.x3ui_service,
    )
    output_dir = REPO_DIR / args.output_dir
    write_outputs(output_dir, candidates, results, errors)

    active = json.loads((output_dir / "active.json").read_text(encoding="utf-8"))
    print(json.dumps(active, ensure_ascii=False, indent=2))
    if errors:
        print("Source warnings:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    return 0 if active.get("domain") else 2


if __name__ == "__main__":
    raise SystemExit(main())
