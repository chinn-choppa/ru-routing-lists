import json
from pathlib import Path
import tempfile
import unittest

from src import build as builder


class NormalizeTests(unittest.TestCase):
    def test_domain_suffixes_are_normalized_deduplicated_and_sorted(self):
        source = "RU\n# comment\nExample.RU.  # inline\nru\nxn--p1ai\n"
        self.assertEqual(
            builder.normalize_domain_suffixes(source),
            ["example.ru", "ru", "xn--p1ai"],
        )

    def test_domain_suffix_rejects_v2fly_include_directive(self):
        with self.assertRaises(builder.BuildError):
            builder.normalize_domain_suffixes("include:category-ru\n")

    def test_cidrs_are_validated_deduplicated_and_sorted(self):
        source = "2001:db8::/32\n10.0.0.0/8\n10.0.0.0/8 # duplicate\n192.0.2.0/24\n"
        self.assertEqual(
            builder.normalize_cidrs(source),
            ["10.0.0.0/8", "192.0.2.0/24", "2001:db8::/32"],
        )

    def test_cidr_rejects_non_network_address(self):
        with self.assertRaises(builder.BuildError):
            builder.normalize_cidrs("192.0.2.1/24\n")


class BuildTests(unittest.TestCase):
    def make_manifest(self, domain_raw: bytes, cidr_raw: bytes) -> dict:
        revision_a = "a" * 40
        revision_b = "b" * 40
        return {
            "schema": 1,
            "sources": [
                {
                    "name": "domains",
                    "kind": "domain_suffix",
                    "output": "domains.txt",
                    "url": f"https://example.test/{revision_a}/domains.txt",
                    "revision": revision_a,
                    "git_blob_sha": builder.git_blob_sha(domain_raw),
                    "source_timestamp": "2026-01-01T00:00:00Z",
                    "roles": ["primary"],
                    "license": {
                        "spdx": "MIT",
                        "source_url": "https://example.test/LICENSE",
                    },
                    "redistribution": {
                        "allowed": True,
                        "attribution": "example domains",
                    },
                    "provenance": {"depends_on": []},
                },
                {
                    "name": "cidrs",
                    "kind": "cidr",
                    "output": "cidrs.txt",
                    "url": f"https://example.test/{revision_b}/cidrs.txt",
                    "revision": revision_b,
                    "git_blob_sha": builder.git_blob_sha(cidr_raw),
                    "source_timestamp": "2026-01-02T00:00:00Z",
                    "roles": ["primary"],
                    "license": {
                        "spdx": "CC-BY-SA-4.0",
                        "source_url": "https://example.test/LICENSE-CC",
                    },
                    "redistribution": {
                        "allowed": True,
                        "attribution": "example cidrs",
                    },
                    "provenance": {"depends_on": []},
                },
            ],
        }

    def test_build_is_byte_reproducible(self):
        domain_raw = b"ru\nexample.ru\nRU\n"
        cidr_raw = b"192.0.2.0/24\n10.0.0.0/8\n"
        manifest = self.make_manifest(domain_raw, cidr_raw)
        payloads = {
            manifest["sources"][0]["url"]: domain_raw,
            manifest["sources"][1]["url"]: cidr_raw,
        }

        def fetcher(url: str) -> bytes:
            return payloads[url]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_a = root / "a"
            out_b = root / "b"
            builder.build(manifest, out_a, fetcher=fetcher)
            builder.build(manifest, out_b, fetcher=fetcher)

            files_a = {path.name: path.read_bytes() for path in out_a.iterdir()}
            files_b = {path.name: path.read_bytes() for path in out_b.iterdir()}
            self.assertEqual(files_a, files_b)
            self.assertIn("manifest.json", files_a)
            self.assertIn("SHA256SUMS", files_a)

            rendered = json.loads(files_a["manifest.json"])
            self.assertEqual(rendered["schema"], 1)
            self.assertEqual(len(rendered["artifacts"]), 2)

    def test_publish_fails_closed_when_redistribution_is_not_allowed(self):
        manifest = self.make_manifest(b"ru\n", b"192.0.2.0/24\n")
        manifest["sources"][0]["redistribution"]["allowed"] = "unknown"
        with self.assertRaises(builder.BuildError):
            builder.validate_sources_manifest(manifest)

    def test_blob_mismatch_fails_closed_without_replacing_previous_output(self):
        domain_raw = b"ru\n"
        cidr_raw = b"192.0.2.0/24\n"
        manifest = self.make_manifest(domain_raw, cidr_raw)
        manifest["sources"][0]["git_blob_sha"] = "0" * 40

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dist"
            output.mkdir()
            sentinel = output / "known-good.txt"
            sentinel.write_text("keep\n", encoding="utf-8")

            def fetcher(url: str) -> bytes:
                if url == manifest["sources"][0]["url"]:
                    return domain_raw
                return cidr_raw

            with self.assertRaises(builder.BuildError):
                builder.build(manifest, output, fetcher=fetcher)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")


if __name__ == "__main__":
    unittest.main()
