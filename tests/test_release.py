from datetime import datetime, timezone
import unittest

from src import build as builder
from src import refresh_sources
from src import release_plan


class RefreshSourcesTests(unittest.TestCase):
    def make_manifest(self) -> dict:
        revision = "a" * 40
        return {
            "schema": 1,
            "sources": [
                {
                    "name": "domains",
                    "kind": "domain_suffix",
                    "output": "domains.txt",
                    "url": f"https://raw.githubusercontent.com/example/data/{revision}/domains.txt",
                    "revision": revision,
                    "git_blob_sha": "b" * 40,
                    "source_timestamp": "2026-01-01T00:00:00Z",
                    "tracking": {
                        "repository": "example/data",
                        "ref": "main",
                        "path": "domains.txt",
                        "license_path": "LICENSE",
                    },
                    "roles": ["primary"],
                    "license": {
                        "spdx": "MIT",
                        "source_url": f"https://github.com/example/data/blob/{revision}/LICENSE",
                        "approved_git_blob_sha": "c" * 40,
                    },
                    "redistribution": {
                        "allowed": True,
                        "attribution": "example/data",
                    },
                    "provenance": {"depends_on": []},
                }
            ],
        }

    def test_resolve_sources_uses_last_path_commit_and_checks_current_license(self):
        manifest = self.make_manifest()
        source_revision = "d" * 40
        head_revision = "f" * 40
        source_blob = "e" * 40
        responses = {
            "/repos/example/data/commits?sha=main&path=domains.txt&per_page=1": [
                {
                    "sha": source_revision,
                    "commit": {"committer": {"date": "2026-09-07T10:00:00Z"}},
                }
            ],
            "/repos/example/data/commits/main": {
                "sha": head_revision,
                "commit": {"committer": {"date": "2026-09-08T10:00:00Z"}},
            },
            f"/repos/example/data/contents/domains.txt?ref={source_revision}": {"sha": source_blob},
            f"/repos/example/data/contents/LICENSE?ref={source_revision}": {"sha": "c" * 40},
            f"/repos/example/data/contents/LICENSE?ref={head_revision}": {"sha": "c" * 40},
        }

        resolved = refresh_sources.resolve_sources(manifest, responses.__getitem__)
        source = resolved["sources"][0]
        self.assertEqual(source["revision"], source_revision)
        self.assertEqual(source["git_blob_sha"], source_blob)
        self.assertEqual(source["source_timestamp"], "2026-09-07T10:00:00Z")
        self.assertEqual(
            source["url"],
            f"https://raw.githubusercontent.com/example/data/{source_revision}/domains.txt",
        )
        self.assertEqual(source["license"]["checked_revision"], head_revision)
        self.assertIn(head_revision, source["license"]["source_url"])
        builder.validate_sources_manifest(resolved)

    def test_resolve_sources_blocks_license_change_at_source_revision(self):
        manifest = self.make_manifest()
        source_revision = "d" * 40
        responses = {
            "/repos/example/data/commits?sha=main&path=domains.txt&per_page=1": [
                {
                    "sha": source_revision,
                    "commit": {"committer": {"date": "2026-09-08T10:00:00Z"}},
                }
            ],
            "/repos/example/data/commits/main": {
                "sha": source_revision,
                "commit": {"committer": {"date": "2026-09-08T10:00:00Z"}},
            },
            f"/repos/example/data/contents/domains.txt?ref={source_revision}": {"sha": "e" * 40},
            f"/repos/example/data/contents/LICENSE?ref={source_revision}": {"sha": "9" * 40},
        }
        with self.assertRaises(refresh_sources.RefreshError):
            refresh_sources.resolve_sources(manifest, responses.__getitem__)

    def test_resolve_sources_blocks_license_change_on_current_head(self):
        manifest = self.make_manifest()
        source_revision = "d" * 40
        head_revision = "f" * 40
        responses = {
            "/repos/example/data/commits?sha=main&path=domains.txt&per_page=1": [
                {
                    "sha": source_revision,
                    "commit": {"committer": {"date": "2026-09-07T10:00:00Z"}},
                }
            ],
            "/repos/example/data/commits/main": {
                "sha": head_revision,
                "commit": {"committer": {"date": "2026-09-08T10:00:00Z"}},
            },
            f"/repos/example/data/contents/domains.txt?ref={source_revision}": {"sha": "e" * 40},
            f"/repos/example/data/contents/LICENSE?ref={source_revision}": {"sha": "c" * 40},
            f"/repos/example/data/contents/LICENSE?ref={head_revision}": {"sha": "9" * 40},
        }
        with self.assertRaises(refresh_sources.RefreshError):
            refresh_sources.resolve_sources(manifest, responses.__getitem__)


class ReleasePlanTests(unittest.TestCase):
    NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

    def make_manifest(self, domain_entries=10, cidr_entries=8000, domain_sha="1", cidr_sha="2") -> dict:
        return {
            "schema": 1,
            "builder": "src/build.py",
            "sources": [
                {
                    "name": "domains",
                    "source_timestamp": "2026-02-17T08:52:16Z",
                    "revision": "a" * 40,
                    "git_blob_sha": "b" * 40,
                },
                {
                    "name": "cidrs",
                    "source_timestamp": "2026-09-08T10:30:00Z",
                    "revision": "c" * 40,
                    "git_blob_sha": "d" * 40,
                },
            ],
            "artifacts": [
                {
                    "path": "domains.txt",
                    "entries": domain_entries,
                    "bytes": domain_entries * 10,
                    "sha256": domain_sha * 64,
                },
                {
                    "path": "cidrs.txt",
                    "entries": cidr_entries,
                    "bytes": cidr_entries * 16,
                    "sha256": cidr_sha * 64,
                },
            ],
        }

    def make_policy(self) -> dict:
        return {
            "schema": 1,
            "artifacts": {
                "domains.txt": {
                    "min_entries": 2,
                    "max_entries": 100,
                    "max_entry_change_fraction": 0.5,
                    "max_byte_change_fraction": 0.5,
                },
                "cidrs.txt": {
                    "min_entries": 7000,
                    "max_entries": 20000,
                    "max_entry_change_fraction": 0.2,
                    "max_byte_change_fraction": 0.25,
                },
            },
            "sources": {
                "domains": {"max_age_hours": None},
                "cidrs": {"max_age_hours": 72},
            },
        }

    def test_initial_release_is_allowed_after_absolute_guards(self):
        plan = release_plan.create_plan(
            self.make_manifest(),
            self.make_policy(),
            now=self.NOW,
        )
        self.assertTrue(plan["release_needed"])
        self.assertEqual(plan["reason"], "initial_release")

    def test_same_artifact_bytes_do_not_create_release(self):
        candidate = self.make_manifest()
        previous = self.make_manifest()
        candidate["sources"][0]["revision"] = "e" * 40
        plan = release_plan.create_plan(
            candidate,
            self.make_policy(),
            previous,
            now=self.NOW,
        )
        self.assertFalse(plan["release_needed"])
        self.assertEqual(plan["reason"], "no_artifact_change")

    def test_small_artifact_change_is_allowed(self):
        previous = self.make_manifest(cidr_entries=8000, cidr_sha="2")
        candidate = self.make_manifest(cidr_entries=8200, cidr_sha="3")
        plan = release_plan.create_plan(
            candidate,
            self.make_policy(),
            previous,
            now=self.NOW,
        )
        self.assertTrue(plan["release_needed"])
        self.assertEqual(plan["changed_artifacts"], ["cidrs.txt"])

    def test_anomalous_artifact_growth_fails_closed(self):
        previous = self.make_manifest(cidr_entries=8000, cidr_sha="2")
        candidate = self.make_manifest(cidr_entries=11000, cidr_sha="3")
        with self.assertRaises(release_plan.ReleasePlanError):
            release_plan.create_plan(
                candidate,
                self.make_policy(),
                previous,
                now=self.NOW,
            )

    def test_stale_dynamic_source_fails_closed(self):
        candidate = self.make_manifest()
        candidate["sources"][1]["source_timestamp"] = "2026-09-01T00:00:00Z"
        with self.assertRaises(release_plan.ReleasePlanError):
            release_plan.create_plan(
                candidate,
                self.make_policy(),
                now=self.NOW,
            )

    def test_stable_source_may_disable_freshness_guard(self):
        candidate = self.make_manifest()
        candidate["sources"][0]["source_timestamp"] = "2020-01-01T00:00:00Z"
        plan = release_plan.create_plan(candidate, self.make_policy(), now=self.NOW)
        self.assertTrue(plan["release_needed"])

    def test_invalid_sha256_metadata_fails_closed(self):
        candidate = self.make_manifest()
        candidate["artifacts"][0]["sha256"] = "z" * 64
        with self.assertRaises(release_plan.ReleasePlanError):
            release_plan.create_plan(candidate, self.make_policy(), now=self.NOW)

    def test_artifact_set_change_fails_closed(self):
        previous = self.make_manifest()
        candidate = self.make_manifest()
        candidate["artifacts"].append(
            {"path": "new.txt", "entries": 5, "bytes": 10, "sha256": "a" * 64}
        )
        policy = self.make_policy()
        policy["artifacts"]["new.txt"] = {
            "min_entries": 1,
            "max_entries": 10,
            "max_entry_change_fraction": 0.5,
            "max_byte_change_fraction": 0.5,
        }
        with self.assertRaises(release_plan.ReleasePlanError):
            release_plan.create_plan(candidate, policy, previous, now=self.NOW)


if __name__ == "__main__":
    unittest.main()
