#!/usr/bin/env python3
"""Deterministic builder for public routing datasets."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable, Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_KINDS = {"domain_suffix", "cidr"}


class BuildError(RuntimeError):
    """Raised when an input violates the public data contract."""


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_sources(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    validate_sources_manifest(manifest)
    return manifest


def validate_sources_manifest(manifest: dict) -> None:
    if manifest.get("schema") != 1:
        raise BuildError("sources.json schema must be 1")

    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise BuildError("sources.json must contain a non-empty sources list")

    names: set[str] = set()
    outputs: set[str] = set()

    for source in sources:
        if not isinstance(source, dict):
            raise BuildError("each source must be an object")

        name = source.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise BuildError(f"invalid or duplicate source name: {name!r}")
        names.add(name)

        revision = source.get("revision")
        if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
            raise BuildError(f"{name}: revision must be a lowercase 40-hex commit SHA")

        blob_sha = source.get("git_blob_sha")
        if not isinstance(blob_sha, str) or not REVISION_RE.fullmatch(blob_sha):
            raise BuildError(f"{name}: git_blob_sha must be a lowercase 40-hex Git blob SHA")

        url = source.get("url")
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != "https" or not parsed.netloc:
            raise BuildError(f"{name}: source URL must use https")
        if revision not in parsed.path:
            raise BuildError(f"{name}: source URL must contain the immutable revision")

        kind = source.get("kind")
        if kind not in ALLOWED_KINDS:
            raise BuildError(f"{name}: unsupported kind {kind!r}")

        license_info = source.get("license")
        if not isinstance(license_info, dict) or not license_info.get("spdx") or not license_info.get("source_url"):
            raise BuildError(f"{name}: license.spdx and license.source_url are required")

        redistribution = source.get("redistribution")
        publish = source.get("publish", True)
        if not isinstance(redistribution, dict):
            raise BuildError(f"{name}: redistribution metadata is required")
        if publish and redistribution.get("allowed") is not True:
            raise BuildError(f"{name}: publishable source requires redistribution.allowed=true")

        if publish:
            output = source.get("output")
            if not isinstance(output, str) or not output or Path(output).name != output:
                raise BuildError(f"{name}: output must be a simple file name")
            if output in outputs:
                raise BuildError(f"duplicate output: {output}")
            outputs.add(output)


def fetch_url(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "ru-routing-lists/1"})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - manifest restricts to HTTPS
        return response.read()


def _strip_line(raw: str) -> str:
    return raw.split("#", 1)[0].strip()


def normalize_domain_suffixes(text: str) -> list[str]:
    values: set[str] = set()
    for line_number, raw in enumerate(text.splitlines(), start=1):
        value = _strip_line(raw)
        if not value:
            continue
        if any(ch.isspace() for ch in value) or ":" in value or "/" in value:
            raise BuildError(f"invalid domain suffix at line {line_number}: {value!r}")

        value = value.rstrip(".").lower()
        try:
            ascii_value = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise BuildError(f"invalid IDNA domain suffix at line {line_number}: {value!r}") from exc

        if len(ascii_value) > 253:
            raise BuildError(f"domain suffix too long at line {line_number}")
        labels = ascii_value.split(".")
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
            raise BuildError(f"invalid domain label at line {line_number}: {value!r}")
        if any(not re.fullmatch(r"[a-z0-9-]+", label) for label in labels):
            raise BuildError(f"invalid domain characters at line {line_number}: {value!r}")

        values.add(ascii_value)

    if not values:
        raise BuildError("domain source produced an empty dataset")
    return sorted(values)


def normalize_cidrs(text: str) -> list[str]:
    networks: dict[tuple[int, int, int], str] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        value = _strip_line(raw)
        if not value:
            continue
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise BuildError(f"invalid CIDR at line {line_number}: {value!r}") from exc
        key = (network.version, int(network.network_address), network.prefixlen)
        networks[key] = str(network)

    if not networks:
        raise BuildError("CIDR source produced an empty dataset")
    return [networks[key] for key in sorted(networks)]


def normalize(kind: str, text: str) -> list[str]:
    if kind == "domain_suffix":
        return normalize_domain_suffixes(text)
    if kind == "cidr":
        return normalize_cidrs(text)
    raise BuildError(f"unsupported kind: {kind}")


def encoded_lines(values: Iterable[str]) -> bytes:
    return ("\n".join(values) + "\n").encode("utf-8")


def build(
    sources_manifest: dict,
    output_dir: Path,
    fetcher: Callable[[str], bytes] = fetch_url,
) -> dict:
    validate_sources_manifest(sources_manifest)
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="ru-routing-build-", dir=output_dir.parent))

    try:
        artifacts: list[dict] = []
        rendered_sources: list[dict] = []

        for source in sources_manifest["sources"]:
            publish = source.get("publish", True)
            rendered_sources.append(
                {
                    "name": source["name"],
                    "kind": source["kind"],
                    "revision": source["revision"],
                    "git_blob_sha": source["git_blob_sha"],
                    "source_timestamp": source.get("source_timestamp"),
                    "roles": source.get("roles", []),
                    "license": source["license"],
                    "redistribution": source["redistribution"],
                    "provenance": source.get("provenance", {"depends_on": []}),
                    "publish": publish,
                }
            )
            if not publish:
                continue

            raw = fetcher(source["url"])
            actual_blob_sha = git_blob_sha(raw)
            if actual_blob_sha != source["git_blob_sha"]:
                raise BuildError(
                    f"{source['name']}: Git blob SHA mismatch: expected "
                    f"{source['git_blob_sha']}, got {actual_blob_sha}"
                )
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BuildError(f"{source['name']}: input is not UTF-8") from exc

            values = normalize(source["kind"], text)
            payload = encoded_lines(values)
            artifact_path = temp_dir / source["output"]
            artifact_path.write_bytes(payload)
            artifacts.append(
                {
                    "path": source["output"],
                    "kind": source["kind"],
                    "source": source["name"],
                    "entries": len(values),
                    "bytes": len(payload),
                    "sha256": sha256_bytes(payload),
                    "license": source["license"]["spdx"],
                }
            )

        artifacts.sort(key=lambda item: item["path"])
        rendered_sources.sort(key=lambda item: item["name"])
        result_manifest = {
            "schema": 1,
            "builder": "src/build.py",
            "sources": rendered_sources,
            "artifacts": artifacts,
        }
        manifest_bytes = (json.dumps(result_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (temp_dir / "manifest.json").write_bytes(manifest_bytes)

        checksum_entries = [(artifact["path"], artifact["sha256"]) for artifact in artifacts]
        checksum_entries.append(("manifest.json", sha256_bytes(manifest_bytes)))
        checksum_entries.sort(key=lambda item: item[0])
        checksum_text = "".join(f"{digest}  {path}\n" for path, digest in checksum_entries)
        (temp_dir / "SHA256SUMS").write_text(checksum_text, encoding="utf-8")

        if output_dir.exists():
            shutil.rmtree(output_dir)
        os.replace(temp_dir, output_dir)
        return result_manifest
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path("sources.json"))
    parser.add_argument("--output", type=Path, default=Path("dist"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_sources(args.sources)
    result = build(manifest, args.output)
    for artifact in result["artifacts"]:
        print(
            f"built {artifact['path']}: entries={artifact['entries']} "
            f"bytes={artifact['bytes']} sha256={artifact['sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
