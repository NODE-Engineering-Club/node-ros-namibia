#!/usr/bin/env python3
"""Set up the ASKET telemetry storage account and publish the dashboard.

Needs only the storage account's connection string; no Azure CLI or portal
login. Safe to run again: every step checks before it changes anything.

What it does, in order:

1. Creates the private ``telemetry`` container (no public access).
2. Adds one CORS rule to the Blob service (GET/HEAD/OPTIONS from any origin)
   so the dashboard in a browser can read blobs. Existing rules are kept.
   CORS only lets a browser *ask*; every read still needs the SAS token.
3. Turns on the account's static website and uploads ``web/index.html`` to
   the ``$web`` container.
4. Creates a read + list SAS token for the ``telemetry`` container only,
   valid for ``--sas-days``. It cannot write, delete, or see other containers.
5. Prints the viewer link. The token is in the link's ``#fragment``, which
   browsers never send to any server, so it does not end up in access logs.
   The page remembers it in the browser after the first visit.

Usage (on the Jetson, or any machine with Python 3.9+)::

    pip install azure-storage-blob==12.23.1
    python3 cloud/azure/provision.py --conn-file ~/.config/asket/azure_storage_connection_string

Never commit the connection string: it is the account key, with full
read/write/delete on everything in the account.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.storage.blob import (
    BlobServiceClient,
    ContainerSasPermissions,
    ContentSettings,
    CorsRule,
    StaticWebsite,
    generate_container_sas,
)

HERE = Path(__file__).resolve().parent


def load_conn(path: str) -> str:
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if env:
        return env
    p = Path(os.path.expanduser(path))
    if not p.is_file():
        sys.exit(f"No connection string: set AZURE_STORAGE_CONNECTION_STRING or create {p}")
    if p.stat().st_mode & 0o077:
        print(f"warning: {p} is readable by other users; run: chmod 600 {p}")
    return p.read_text(encoding="utf-8").strip()


def parse_conn(conn: str) -> dict:
    parts = dict(kv.split("=", 1) for kv in conn.split(";") if "=" in kv)
    if "AccountName" not in parts or "AccountKey" not in parts:
        sys.exit("The connection string must contain AccountName and AccountKey "
                 "(an account-key connection string, not a SAS one).")
    return parts


def ensure_container(svc: BlobServiceClient, name: str) -> None:
    try:
        svc.create_container(name)
        print(f"[1] created private container '{name}'")
    except ResourceExistsError:
        print(f"[1] container '{name}' already exists")


def ensure_cors(svc: BlobServiceClient) -> None:
    props = svc.get_service_properties()
    rules = list(props.get("cors") or [])
    for r in rules:
        origins = r.allowed_origins if isinstance(r.allowed_origins, list) else r.allowed_origins.split(",")
        methods = r.allowed_methods if isinstance(r.allowed_methods, list) else r.allowed_methods.split(",")
        if "*" in origins and {"GET", "HEAD"} <= set(methods):
            print("[2] CORS rule for the dashboard already present")
            return
    if len(rules) >= 5:
        sys.exit("[2] the Blob service already has 5 CORS rules (Azure's limit); add GET/HEAD/OPTIONS from * by hand")
    rules.append(CorsRule(["*"], ["GET", "HEAD", "OPTIONS"], allowed_headers=["*"],
                          exposed_headers=["*"], max_age_in_seconds=3600))
    svc.set_service_properties(cors=rules)
    print("[2] added CORS rule: GET/HEAD/OPTIONS from any origin (reads still need the SAS)")


def ensure_website(svc: BlobServiceClient, marker: str) -> bool:
    try:
        svc.set_service_properties(static_website=StaticWebsite(enabled=True, index_document="index.html"))
    except HttpResponseError as exc:
        print(f"[3] could not enable the static website ({exc.reason}); skipping the upload")
        return False
    web = svc.get_container_client("$web")
    html = (HERE / "web" / "index.html").read_bytes()
    web.upload_blob("index.html", html, overwrite=True,
                    content_settings=ContentSettings(content_type="text/html; charset=utf-8",
                                                     cache_control="no-cache"))
    web.upload_blob(".asket-marker", marker.encode(), overwrite=True,
                    content_settings=ContentSettings(content_type="text/plain", cache_control="no-cache"))
    print("[3] static website on; dashboard uploaded to $web/index.html")
    return True


def find_web_endpoint(account: str, marker: str) -> str | None:
    """The static-website host carries a zone number (z1..z5x) that only the
    management API reports. Ask each zone for the marker we just uploaded."""
    for z in range(1, 60):
        url = f"https://{account}.z{z}.web.core.windows.net"
        try:
            with urllib.request.urlopen(f"{url}/.asket-marker", timeout=4) as r:
                if r.read().decode(errors="ignore").strip() == marker:
                    return url
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def make_sas(account: str, key: str, container: str, days: int,
             https_only: bool = True) -> tuple[str, datetime]:
    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    sas = generate_container_sas(
        account_name=account, container_name=container, account_key=key,
        permission=ContainerSasPermissions(read=True, list=True),
        expiry=expiry, start=datetime.now(timezone.utc) - timedelta(minutes=10),
        # Real Azure is always https; plain http only for the local emulator.
        protocol="https" if https_only else "https,http",
    )
    return sas, expiry


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conn-file", default="~/.config/asket/azure_storage_connection_string")
    ap.add_argument("--container", default="telemetry")
    ap.add_argument("--asv-id", default="ASKET_02")
    ap.add_argument("--sas-days", type=int, default=180)
    ap.add_argument("--skip-website", action="store_true", help="only container, CORS and token")
    ap.add_argument("--print-sas-only", action="store_true",
                    help="issue a fresh token and print the link, change nothing else")
    args = ap.parse_args()

    conn = load_conn(args.conn_file)
    parts = parse_conn(conn)
    account, key = parts["AccountName"], parts["AccountKey"]
    svc = BlobServiceClient.from_connection_string(conn)
    blob_url = svc.url.rstrip("/")

    web_url = None
    if not args.print_sas_only:
        ensure_container(svc, args.container)
        ensure_cors(svc)
        if not args.skip_website:
            marker = secrets.token_hex(8)
            if ensure_website(svc, marker):
                web_url = find_web_endpoint(account, marker)
                if not web_url:
                    print("    could not find the website address automatically; it is in the portal "
                          "under Storage account > Data management > Static website > Primary endpoint")

    sas, expiry = make_sas(account, key, args.container, args.sas_days,
                           https_only=blob_url.startswith("https://"))
    frag = (f"#blob={quote(blob_url, safe='')}&container={quote(args.container)}"
            f"&asv={quote(args.asv_id)}&sas={quote(sas, safe='')}")
    print(f"[4] read+list token for '{args.container}' valid until {expiry:%Y-%m-%d %H:%M} UTC")
    print()
    print("Viewer link (share only with the team; anyone with it can read the telemetry until it expires):")
    print(f"  {(web_url or '<static website endpoint>')}/{frag}")
    print()
    print("Same page from a local copy (no website needed):")
    print(f"  file://{HERE / 'web' / 'index.html'}{frag}")


if __name__ == "__main__":
    main()
