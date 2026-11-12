"""Regression tests for app ownership, GKD references and safe publication."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "merge.py"
SPEC = importlib.util.spec_from_file_location("gkd_merge", MODULE_PATH)
merge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = merge
SPEC.loader.exec_module(merge)


def subscription(identifier, apps, *, categories=None, globals_=None):
    return {
        "id": identifier,
        "name": f"Source {identifier}",
        "version": 1,
        "categories": categories or [],
        "globalGroups": globals_ or [],
        "apps": apps,
    }


def app(package, name="开屏广告", key=20, text="跳过"):
    return {
        "id": package,
        "name": package,
        "groups": [{
            "key": key,
            "name": name,
            "rules": [{"key": 5, "matches": f'[text="{text}"]'}],
        }],
    }


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.output = self.root / "dist"
        self.config = self.root / "sources.json"

        self.primary_app = app("com.example.shared")
        self.primary_app["groups"][0]["rules"].append({
            "key": 6,
            "preKeys": [5],
            "actionCdKey": 5,
            "actionMaximumKey": 5,
            "matches": '[text="关闭"]',
        })
        self.primary_app["groups"].append({
            "key": 21,
            "name": "功能类-后续动作",
            "scopeKeys": [20],
            "rules": [{"key": 8, "preKeys": [6], "matches": '[text="完成"]'}],
        })
        self.primary_global = {
            "key": 4,
            "name": "开屏广告-全局",
            "disableIfAppGroupMatch": "开屏广告",
            "rules": [{"key": 0, "matches": '[text="跳过"]'}],
        }
        self.documents = {
            "primary": subscription(
                101, [self.primary_app],
                categories=[
                    {"key": 9, "name": "开屏广告", "enable": True},
                    {"key": 7, "name": "功能类", "enable": False},
                ],
                globals_=[self.primary_global],
            ),
            "extra": subscription(
                102,
                [app("com.example.shared", text="错误补充"),
                 app("com.example.other", "权限提示-通知权限")],
                categories=[
                    {"key": 1, "name": "开屏广告", "enable": False},
                    {"key": 9, "name": "权限提示", "enable": False},
                ],
                globals_=[{
                    "key": 4, "name": "开屏广告-补充全局",
                    "rules": [{"matches": '[text="Skip"]'}],
                }],
            ),
        }
        self.sources = [{
            "key": key,
            "name": key,
            "url": f"https://raw.githubusercontent.com/example/{key}/main/dist/gkd.json5",
            "repository": f"example/{key}",
            "ref": "main",
            "license": "MIT",
            "redistribution_approved": True,
        } for key in self.documents]
        self.write_config()
        self.write_inputs()

    def write_config(self):
        self.config.write_text(json.dumps({"sources": self.sources}), encoding="utf-8")

    def write_inputs(self):
        for key, document in self.documents.items():
            (self.inputs / f"{key}.json5").write_text(
                json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def run_merge(self):
        merge.run_merge(self.config, input_dir=self.inputs, output_dir=self.output)
        return json.loads((self.output / "gkd.json5").read_text(encoding="utf-8"))

    def snapshot(self):
        return {
            str(p.relative_to(self.output)): p.read_bytes()
            for p in self.output.rglob("*") if p.is_file()
        }

    def test_app_ownership_preserves_rule_and_group_references(self):
        payload = self.run_merge()
        apps = {item["id"]: item for item in payload["apps"]}
        self.assertEqual(set(apps), {"com.example.shared", "com.example.other"})
        self.assertEqual(apps["com.example.shared"], self.primary_app)
        self.assertEqual(apps["com.example.other"], self.documents["extra"]["apps"][1])
        # Group 20 remains valid in both apps; its identity is scoped to a package.
        self.assertEqual([item["groups"][0]["key"] for item in apps.values()], [20, 20])
        self.assertEqual(payload["globalGroups"], [self.primary_global])

    def test_categories_keep_primary_policy_and_unique_keys(self):
        payload = self.run_merge()
        categories = payload["categories"]
        self.assertEqual(len({item["key"] for item in categories}), len(categories))
        by_name = {item["name"]: item for item in categories}
        self.assertEqual(set(by_name), {"开屏广告", "功能类", "权限提示"})
        self.assertTrue(by_name["开屏广告"]["enable"])
        self.assertFalse(by_name["功能类"]["enable"])
        self.assertFalse(by_name["权限提示"]["enable"])
        for item in payload["apps"]:
            for group in item["groups"]:
                self.assertTrue(any(group["name"].startswith(name) for name in by_name))

    def test_existing_upstream_dangling_reference_is_preserved_and_reported(self):
        # Current upstream rules contain this GKD-tolerated condition. Importing
        # them must retain their semantics while making the issue inspectable.
        self.documents["primary"]["apps"][0]["groups"][0]["rules"][1]["preKeys"] = [999]
        self.write_inputs()
        payload = self.run_merge()
        self.assertEqual(payload["apps"][0], self.documents["primary"]["apps"][0])
        report = json.loads((self.output / "merge-report.json").read_text(encoding="utf-8"))
        self.assertTrue(any("preKeys" in warning and "999" in warning for warning in report["warnings"]))

    def test_update_manifest_and_noop_then_content_change(self):
        initial = self.run_merge()
        subscription_bytes = (self.output / "gkd.json5").read_bytes()
        manifest_bytes = (self.output / "gkd.version.json5").read_bytes()
        second = self.run_merge()
        self.assertEqual(initial["version"], second["version"])
        self.assertEqual((self.output / "gkd.json5").read_bytes(), subscription_bytes)
        self.assertEqual((self.output / "gkd.version.json5").read_bytes(), manifest_bytes)
        manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest["id"], initial["id"])
        self.assertEqual(manifest["version"], initial["version"])
        self.assertEqual(
            initial["checkUpdateUrl"],
            "https://raw.githubusercontent.com/tgmzo/gkd-one-link/main/dist/gkd.version.json5",
        )
        self.documents["extra"]["apps"][1]["groups"][0]["rules"][0]["matches"] = '[text="暂不"]'
        self.write_inputs()
        changed = self.run_merge()
        self.assertEqual(changed["version"], initial["version"] + 1)
        self.assertEqual(json.loads((self.output / "gkd.version.json5").read_text())["version"], changed["version"])

    def test_upstream_version_only_change_does_not_bump(self):
        initial = self.run_merge()
        self.documents["primary"]["version"] += 1
        self.write_inputs()
        self.assertEqual(self.run_merge()["version"], initial["version"])

    def test_bad_json5_does_not_replace_working_outputs(self):
        self.run_merge()
        previous = self.snapshot()
        (self.inputs / "extra.json5").write_text('{id: 102, apps: [', encoding="utf-8")
        with self.assertRaises(merge.MergeError):
            self.run_merge()
        self.assertEqual(self.snapshot(), previous)

    def test_duplicate_json_object_key_is_rejected(self):
        (self.inputs / "extra.json5").write_text(
            '{id:102,name:"bad",version:1,apps:[],apps:[]}', encoding="utf-8")
        with self.assertRaises(merge.MergeError):
            self.run_merge()
        self.assertFalse(self.output.exists())

    def test_duplicate_package_and_group_keys_are_rejected(self):
        for kind in ("package", "group", "rule"):
            with self.subTest(kind=kind):
                original = copy.deepcopy(self.documents["extra"])
                if kind == "package":
                    self.documents["extra"]["apps"].append(copy.deepcopy(original["apps"][0]))
                elif kind == "group":
                    groups = self.documents["extra"]["apps"][0]["groups"]
                    groups.append(copy.deepcopy(groups[0]))
                else:
                    rules = self.documents["extra"]["apps"][0]["groups"][0]["rules"]
                    rules.append(copy.deepcopy(rules[0]))
                self.write_inputs()
                with self.assertRaises(merge.MergeError):
                    self.run_merge()
                self.documents["extra"] = original
                self.assertFalse(self.output.exists())

    def test_missing_redistribution_permission_preserves_outputs(self):
        self.run_merge()
        previous = self.snapshot()
        self.sources[0]["redistribution_approved"] = False
        self.write_config()
        with self.assertRaises(merge.MergeError):
            self.run_merge()
        self.assertEqual(self.snapshot(), previous)

    def test_network_failure_preserves_outputs(self):
        self.run_merge()
        previous = self.snapshot()
        with mock.patch("urllib.request.urlopen", side_effect=URLError("upstream unavailable")), \
                mock.patch("time.sleep", return_value=None):
            with self.assertRaises(merge.MergeError):
                merge.run_merge(self.config, output_dir=self.output)
        self.assertEqual(self.snapshot(), previous)

    def test_existing_version_mismatch_is_not_overwritten(self):
        self.run_merge()
        metadata = self.output / "gkd.version.json5"
        document = json.loads(metadata.read_text(encoding="utf-8"))
        document["version"] += 1
        metadata.write_text(json.dumps(document), encoding="utf-8")
        previous = self.snapshot()
        with self.assertRaises(merge.MergeError):
            self.run_merge()
        self.assertEqual(self.snapshot(), previous)

    def test_production_downloads_pin_rules_and_source_archive_to_same_commit(self):
        self.sources[0]["archive_source"] = True
        self.write_config()
        archive_stream = io.BytesIO()
        with tarfile.open(fileobj=archive_stream, mode="w:gz") as archive:
            content = b'{"name":"fixture","license":"GPL-3.0-only"}'
            member = tarfile.TarInfo("example-primary-commit/package.json")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        valid_archive = archive_stream.getvalue()
        commits = {"primary": "a" * 40, "extra": "b" * 40}
        calls = []

        def downloaded(url, max_bytes=None):
            calls.append(url)
            for key, sha in commits.items():
                if url == f"https://api.github.com/repos/example/{key}/commits/main":
                    return json.dumps({"sha": sha}).encode()
                if url == f"https://raw.githubusercontent.com/example/{key}/{sha}/dist/gkd.json5":
                    return json.dumps(self.documents[key]).encode()
                if key == "primary" and url == f"https://api.github.com/repos/example/{key}/tarball/{sha}":
                    return valid_archive
            self.fail(f"Unexpected or unpinned upstream URL: {url}")

        with mock.patch.object(merge, "download", side_effect=downloaded):
            merge.run_merge(self.config, output_dir=self.output)
        report = json.loads((self.output / "merge-report.json").read_text(encoding="utf-8"))
        for source in report["sources"]:
            self.assertEqual(source["commit_sha"], commits[source["key"]])
            self.assertIn(commits[source["key"]], source["resolved_url"])
        self.assertEqual((self.output / "upstream" / "primary-source.tar.gz").read_bytes(), valid_archive)
        self.assertEqual(len(calls), 5)

    def test_corrupt_source_archive_preserves_subscription_and_source_snapshots(self):
        self.run_merge()
        previous = self.snapshot()
        self.sources[0]["archive_source"] = True
        self.write_config()
        sha = "a" * 40

        def downloaded(url, max_bytes=None):
            if url == "https://api.github.com/repos/example/primary/commits/main":
                return json.dumps({"sha": sha}).encode()
            if url == f"https://raw.githubusercontent.com/example/primary/{sha}/dist/gkd.json5":
                return json.dumps(self.documents["primary"]).encode()
            if url == f"https://api.github.com/repos/example/primary/tarball/{sha}":
                # Gzip signature alone cannot establish a valid source archive.
                return b"\x1f\x8btruncated source archive"
            self.fail(f"Unexpected upstream URL after corrupt archive: {url}")

        with mock.patch.object(merge, "download", side_effect=downloaded):
            with self.assertRaises(merge.MergeError):
                merge.run_merge(self.config, output_dir=self.output)
        self.assertEqual(self.snapshot(), previous)


if __name__ == "__main__":
    unittest.main()
