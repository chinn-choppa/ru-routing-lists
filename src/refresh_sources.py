#!/usr/bin/env python3
"""Resolve tracked upstream refs into an immutable sources manifest."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from src import build as builder

API_BASE = "https://api.github.com"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class RefreshError(RuntimeError):
    """Raised when a tracked source cannot be resolved safely."""


def _safe_relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise RefreshError(f"{field} must be a non-empty relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RefreshError(f"{field} contains an unsafe path component")
    return value


def validate_tracking(source: dict) -> dict:
    tracking = source.get("tracking")
    if not isinstance(tracking, dict):
        raise RefreshError(f"{source.get('name', '<unknown>')}: tracking metadata is required")

    repository = tracking.get("repository")
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise RefreshError(f"{source['name']}: invalid tracking.repository")

    ref = tracking.get("ref")
    if not isinstance(ref, str) or not ref or not REF_RE.fullmatch(ref) or ".." in ref:
        raise RefreshError(f"{source['name']}: invalid tracking.ref")

    path = _safe_relative_path(tracking.get("path"), f"{source['name']}: tracking.path")
    license_path = _safe_relative_path(
        tracking.get("license_path"),
        f"{source['name']}: tracking.license_path",
    )

    approved_license_blob = source.get("license", {}).get("approved_git_blob_sha")
    if not isinstance(approved_license_blob, str) or not builder.REVISION_RE.fullmatch(approved_license_blob):
        raise RefreshError(f"{source['name']}: license.approved_git_blob_sha is required")

    return {
        "repository": repository,
        "ref": ref,
        "path": path,
        "license_path": license_path,
    }


def github_get(path: str, token: str | None = None) -> object:
    if not path.startswith("/"):
        raise RefreshError("GitHub API path must start with /")
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
        raise RefreshError(f"GitHub API request failed for {path}: HTTP {exc.code}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError(f"GitHub API returned invalid JSON for {path}") from exc


def _commit_identity(commit: object, source_name: str, label: str) -> tuple[str, str]:
    if not isinstance(commit, dict):
        raise RefreshError(f"{source_name}: invalid {label} commit response")
    revision = commit.get("sha")
    commit_info = commit.get("commit")
    committer = commit_info.get("committer") if isinstance(commit_info, dict) else None
    timestamp = committer.get("date") if isinstance(committer, dict) else None
    if not isinstance(revision, str) or not builder.REVISION_RE.fullmatch(revision):
        raise RefreshError(f"{source_name}: {label} did not resolve to a commit SHA")
    if not isinstance(timestamp, str) or not timestamp:
        raise RefreshError(f"{source_name}: {label} commit timestamp is missing")
    return revision, timestamp


def _content_blob(getter: Callable[[str], object], repo_api: str, path: str, revision: str, source_name: str) -> str:
    encoded_path = quote(path, safe="/")
    response = getter(f"/repos/{repo_api}/contents/{encoded_path}?ref={revision}")
    if not isinstance(response, dict):
        raise RefreshError(f"{source_name}: invalid contents response for {path}")
    blob_sha = response.get("sha")
    if not isinstance(blob_sha, str) or not builder.REVISION_RE.fullmatch(blob_sha):
        raise RefreshError(f"{source_name}: Git blob SHA is missing for {path}")
    return blob_sha


def resolve_sources(
    manifest: dict,
    getter: Callable[[str], object],
) -> dict:
    builder.validate_sources_manifest(manifest)
    resolved = deepcopy(manifest)

    for source in resolved["sources"]:
        if source.get("publish", True) is False and "tracking" not in source:
            continue

        tracking = validate_tracking(source)
        repository = tracking["repository"]
        ref = tracking["ref"]
        source_path = tracking["path"]
        license_path = tracking["license_path"]

        repo_api = quote(repository, safe="/")
        ref_api = quote(ref, safe="")

        query = urlencode({"sha": ref, "path": source_path, "per_page": 1})
        path_commits = getter(f"/repos/{repo_api}/commits?{query}")
        if not isinstance(path_commits, list) or not path_commits:
            raise RefreshError(f"{source['name']}: no commit found for tracked source path")
        revision, source_timestamp = _commit_identity(path_commits[0], source["name"], "source path")

        head_commit = getter(f"/repos/{repo_api}/commits/{ref_api}")
        head_revision, _ = _commit_identity(head_commit, source["name"], "tracked ref")

        blob_sha = _content_blob(getter, repo_api, source_path, revision, source["name"])
        approved_license_blob = source["license"]["approved_git_blob_sha"]
        source_license_blob = _content_blob(getter, repo_api, license_path, revision, source["name"])
        if source_license_blob != approved_license_blob:
            raise RefreshError(
                f"{source['name']}: license at source revision changed from approved "
                f"{approved_license_blob} to {source_license_blob}; security/license review required"
            )

        head_license_blob = source_license_blob
        if head_revision != revision:
            head_license_blob = _content_blob(getter, repo_api, license_path, head_revision, source["name"])
        if head_license_blob != approved_license_blob:
            raise RefreshError(
                f"{source['name']}: current upstream license changed from approved "
                f"{approved_license_blob} to {head_license_blob}; security/license review required"
            )

        source["revision"] = revision
        source["git_blob_sha"] = blob_sha
        source["source_timestamp"] = source_timestamp
        source["url"] = f"https://raw.githubusercontent.com/{repository}/{revision}/{source_path}"
        source["license"]["source_url"] = f"https://github.com/{repository}/blob/{head_revision}/{license_path}"
        source["license"]["checked_revision"] = head_revision

    builder.validate_sources_manifest(resolved)
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path("sources.json"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = builder.load_sources(args.sources)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    resolved = resolve_sources(manifest, lambda path: github_get(path, token=token))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for source in resolved["sources"]:
        print(f"resolved {source['name']}: {source['revision']} {source['git_blob_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
