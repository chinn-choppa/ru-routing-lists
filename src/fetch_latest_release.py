#!/usr/bin/env python3
"""Fetch manifest.json from the latest published GitHub Release, if any."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

API_BASE = "https://api.github.com"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class LatestReleaseError(RuntimeError):
    """Raised when latest-release metadata cannot be consumed safely."""


def _api_get(path: str, token: str | None) -> object | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ru-routing-lists/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(API_BASE + path, headers=headers)
    try:
        with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed GitHub API origin
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise LatestReleaseError(f"GitHub API request failed: HTTP {exc.code}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LatestReleaseError("GitHub API returned invalid JSON") from exc


def _download_public_asset(url: str, repository: str) -> bytes:
    parsed = urlparse(url)
    expected_prefix = f"/{repository}/releases/download/"
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith(expected_prefix):
        raise LatestReleaseError("latest manifest asset URL is outside the expected public GitHub release path")
    request = Request(url, headers={"User-Agent": "ru-routing-lists/1"})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - validated public GitHub release URL
        return response.read()


def fetch_latest_manifest(repository: str, token: str | None = None) -> tuple[str, bytes] | None:
    if not REPOSITORY_RE.fullmatch(repository):
        raise LatestReleaseError("repository must be owner/name")

    release = _api_get(f"/repos/{repository}/releases/latest", token)
    if release is None:
        return None
    if not isinstance(release, dict) or release.get("draft") is True:
        raise LatestReleaseError("latest release response is invalid")

    tag = release.get("tag_name")
    assets = release.get("assets")
    if not isinstance(tag, str) or not tag or not isinstance(assets, list):
        raise LatestReleaseError("latest release is missing tag/assets metadata")

    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == "manifest.json"]
    if len(matches) != 1:
        raise LatestReleaseError("latest published release must contain exactly one manifest.json asset")
    download_url = matches[0].get("browser_download_url")
    if not isinstance(download_url, str):
        raise LatestReleaseError("latest manifest asset has no browser_download_url")

    payload = _download_public_asset(download_url, repository)
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LatestReleaseError("latest manifest asset is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise LatestReleaseError("latest manifest asset has unsupported schema")
    return tag, payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    result = fetch_latest_manifest(args.repository, token=token)
    if result is None:
        print("latest_release=none")
        return 0
    tag, payload = result
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f"latest_release={tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
