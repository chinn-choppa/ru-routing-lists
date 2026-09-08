#!/usr/bin/env python3
"""Render concise human-readable notes for a routing-data release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def render(manifest: dict, plan: dict, commit_sha: str) -> str:
    lines = [
        "# Routing data release",
        "",
        "Policy-neutral public routing datasets built from reviewed upstream sources.",
        "",
        f"Builder commit: `{commit_sha}`",
        "",
        "## Artifacts",
        "",
    ]
    for artifact in sorted(manifest.get("artifacts", []), key=lambda item: item["path"]):
        marker = "changed" if artifact["path"] in plan.get("changed_artifacts", []) else "unchanged"
        lines.append(
            f"- `{artifact['path']}` — {artifact['entries']} entries, {artifact['bytes']} bytes, "
            f"SHA-256 `{artifact['sha256']}` ({marker})"
        )

    lines.extend(["", "## Sources", ""])
    for source in sorted(manifest.get("sources", []), key=lambda item: item["name"]):
        lines.append(
            f"- `{source['name']}` — revision `{source['revision']}`, blob `{source['git_blob_sha']}`, "
            f"license `{source['license']['spdx']}`"
        )

    lines.extend(
        [
            "",
            "`ru-ipv4-cidrs.txt` represents delegated RU address resources from the selected upstream; "
            "it is not a guarantee of operational server geolocation.",
            "",
            "Checksums are in `SHA256SUMS`; full provenance is in `manifest.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    notes = render(load_json(args.manifest), load_json(args.plan), args.commit)
    args.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
