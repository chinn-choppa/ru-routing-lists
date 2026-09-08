#!/usr/bin/env python3
"""Decide whether a validated candidate may become a public release."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReleasePlanError(RuntimeError):
    """Raised when a candidate violates release policy."""


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ReleasePlanError(f"{path}: expected a JSON object")
    return value


def _parse_timestamp(value: object, source_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ReleasePlanError(f"{source_name}: source_timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleasePlanError(f"{source_name}: invalid source_timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise ReleasePlanError(f"{source_name}: source_timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _index(items: object, key: str, label: str) -> dict[str, dict]:
    if not isinstance(items, list) or not items:
        raise ReleasePlanError(f"manifest must contain a non-empty {label} list")
    result: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ReleasePlanError(f"{label} entries must be objects")
        name = item.get(key)
        if not isinstance(name, str) or not name or name in result:
            raise ReleasePlanError(f"invalid or duplicate {label} key: {name!r}")
        result[name] = item
    return result


def _change_fraction(current: int, previous: int) -> float:
    if previous <= 0:
        return 0.0 if current == previous else float("inf")
    return abs(current - previous) / previous


def create_plan(
    candidate: dict,
    policy: dict,
    previous: dict | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    if candidate.get("schema") != 1:
        raise ReleasePlanError("candidate manifest schema must be 1")
    if policy.get("schema") != 1:
        raise ReleasePlanError("release policy schema must be 1")
    if previous is not None and previous.get("schema") != candidate.get("schema"):
        raise ReleasePlanError("automatic release is blocked across manifest schema changes")

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate_artifacts = _index(candidate.get("artifacts"), "path", "artifacts")
    candidate_sources = _index(candidate.get("sources"), "name", "sources")

    artifact_policy = policy.get("artifacts")
    source_policy = policy.get("sources")
    if not isinstance(artifact_policy, dict) or set(artifact_policy) != set(candidate_artifacts):
        raise ReleasePlanError("release policy must cover exactly every candidate artifact")
    if not isinstance(source_policy, dict) or set(source_policy) != set(candidate_sources):
        raise ReleasePlanError("release policy must cover exactly every candidate source")

    for path, artifact in candidate_artifacts.items():
        rules = artifact_policy[path]
        if not isinstance(rules, dict):
            raise ReleasePlanError(f"{path}: artifact policy must be an object")
        entries = artifact.get("entries")
        size = artifact.get("bytes")
        digest = artifact.get("sha256")
        if not isinstance(entries, int) or entries < 0 or not isinstance(size, int) or size < 0:
            raise ReleasePlanError(f"{path}: invalid entries/bytes metadata")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ReleasePlanError(f"{path}: invalid sha256 metadata")
        min_entries = rules.get("min_entries")
        max_entries = rules.get("max_entries")
        if not isinstance(min_entries, int) or not isinstance(max_entries, int) or min_entries < 0 or max_entries < min_entries:
            raise ReleasePlanError(f"{path}: invalid absolute entry guard")
        if not min_entries <= entries <= max_entries:
            raise ReleasePlanError(
                f"{path}: entry count {entries} is outside approved range {min_entries}..{max_entries}"
            )

    for name, source in candidate_sources.items():
        rules = source_policy[name]
        if not isinstance(rules, dict):
            raise ReleasePlanError(f"{name}: source policy must be an object")
        max_age_hours = rules.get("max_age_hours")
        if max_age_hours is None:
            continue
        if not isinstance(max_age_hours, int) or max_age_hours <= 0:
            raise ReleasePlanError(f"{name}: max_age_hours must be a positive integer or null")
        timestamp = _parse_timestamp(source.get("source_timestamp"), name)
        if timestamp > now + timedelta(hours=1):
            raise ReleasePlanError(f"{name}: source timestamp is unexpectedly in the future")
        if now - timestamp > timedelta(hours=max_age_hours):
            raise ReleasePlanError(
                f"{name}: source snapshot is older than approved {max_age_hours}h freshness window"
            )

    if previous is None:
        changed = sorted(candidate_artifacts)
        reason = "initial_release"
    else:
        previous_artifacts = _index(previous.get("artifacts"), "path", "previous artifacts")
        if set(previous_artifacts) != set(candidate_artifacts):
            raise ReleasePlanError("automatic release is blocked when artifact set changes")

        changed = []
        for path, artifact in candidate_artifacts.items():
            old = previous_artifacts[path]
            if artifact.get("sha256") == old.get("sha256"):
                continue
            changed.append(path)

            rules = artifact_policy[path]
            old_entries = old.get("entries")
            old_bytes = old.get("bytes")
            if not isinstance(old_entries, int) or old_entries <= 0:
                raise ReleasePlanError(f"{path}: previous entries metadata is invalid")
            if not isinstance(old_bytes, int) or old_bytes <= 0:
                raise ReleasePlanError(f"{path}: previous bytes metadata is invalid")

            entry_limit = rules.get("max_entry_change_fraction")
            byte_limit = rules.get("max_byte_change_fraction")
            if not isinstance(entry_limit, (int, float)) or entry_limit < 0:
                raise ReleasePlanError(f"{path}: invalid max_entry_change_fraction")
            if not isinstance(byte_limit, (int, float)) or byte_limit < 0:
                raise ReleasePlanError(f"{path}: invalid max_byte_change_fraction")

            entry_delta = _change_fraction(artifact["entries"], old_entries)
            byte_delta = _change_fraction(artifact["bytes"], old_bytes)
            if entry_delta > float(entry_limit):
                raise ReleasePlanError(
                    f"{path}: entry-count change {entry_delta:.1%} exceeds approved {float(entry_limit):.1%}"
                )
            if byte_delta > float(byte_limit):
                raise ReleasePlanError(
                    f"{path}: byte-size change {byte_delta:.1%} exceeds approved {float(byte_limit):.1%}"
                )

        reason = "artifact_change" if changed else "no_artifact_change"

    return {
        "schema": 1,
        "release_needed": bool(changed),
        "reason": reason,
        "changed_artifacts": changed,
        "artifacts": {
            path: {
                "entries": artifact["entries"],
                "bytes": artifact["bytes"],
                "sha256": artifact["sha256"],
            }
            for path, artifact in sorted(candidate_artifacts.items())
        },
        "sources": {
            name: {
                "revision": source.get("revision"),
                "git_blob_sha": source.get("git_blob_sha"),
                "source_timestamp": source.get("source_timestamp"),
            }
            for name, source in sorted(candidate_sources.items())
        },
        "previous_release_present": previous is not None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path("release-policy.json"))
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidate = _load_json(args.candidate)
    policy = _load_json(args.policy)
    previous = _load_json(args.previous) if args.previous and args.previous.exists() else None
    plan = create_plan(candidate, policy, previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"release_needed={'true' if plan['release_needed'] else 'false'} reason={plan['reason']}")
    for path in plan["changed_artifacts"]:
        artifact = plan["artifacts"][path]
        print(
            f"changed {path}: entries={artifact['entries']} bytes={artifact['bytes']} "
            f"sha256={artifact['sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
