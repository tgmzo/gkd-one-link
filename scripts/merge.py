#!/usr/bin/env python3
"""Build a single GKD subscription without mixing an app's rule namespaces.

JSON is valid JSON5. All downloads, parses and checks complete before dist files
are replaced; a failed upstream therefore leaves the last published version alone.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import gzip
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import tarfile
import time
from typing import Any
import urllib.error
import urllib.request
import urllib.parse

import json5

SUBSCRIPTION_ID = 199427417
SUBSCRIPTION_NAME = "tgmzo 的 GKD 合并订阅"
BASE_URL = "https://raw.githubusercontent.com/tgmzo/gkd-one-link/main/dist/"
MAX_BYTES = 20 * 1024 * 1024
MAX_VERSION = 2**31 - 1
INTEGER_MIN = -(2**31)
PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
SOURCE_KEY = re.compile(r"[A-Za-z0-9_-]+\Z")


class MergeError(ValueError):
    """An input failed validation; no subscription should be published."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _no_nonfinite(value: Any, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise MergeError(f"{label}: NaN and infinity are not valid GKD values")
    if isinstance(value, dict):
        for key, item in value.items():
            _no_nonfinite(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _no_nonfinite(item, f"{label}[{index}]")


def parse_subscription(raw: bytes, label: str) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise MergeError(f"{label}: exceeds {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8-sig", errors="strict")
        def no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result = {}
            for key, item in pairs:
                if key in result:
                    raise MergeError(f"Duplicate JSON object key: {key}")
                result[key] = item
            return result
        try:
            value = json.loads(text, object_pairs_hook=no_duplicate_pairs)
        except json.JSONDecodeError:
            value = json5.loads(text, allow_duplicate_keys=False, consume_trailing=True)
    except (ValueError, UnicodeError) as error:
        raise MergeError(f"{label}: invalid JSON5: {error}") from error
    _no_nonfinite(value, label)
    if not isinstance(value, dict):
        raise MergeError(f"{label}: subscription must be an object")
    return value


def _int(value: Any, label: str, minimum: int = INTEGER_MIN, maximum: int = MAX_VERSION) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MergeError(f"{label}: expected integer in [{minimum}, {maximum}]")
    return value


def _str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MergeError(f"{label}: expected a nonempty string")
    return value


def _list(value: Any, label: str, flexible: bool = False) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if flexible and isinstance(value, (str, dict)):
        return [value]
    raise MergeError(f"{label}: expected an array")


def _unique(values: list[Any], label: str) -> None:
    if len(values) != len(set(values)):
        raise MergeError(f"{label}: duplicate identifiers")


def _ints(value: Any, label: str) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [_int(item, label) for item in values]


def _strings(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    if any(not isinstance(item, str) for item in values):
        raise MergeError(f"{label}: expected string or string array")
    return values


def _rule_objects(group: dict[str, Any], label: str, global_group: bool = False) -> list[dict[str, Any]]:
    result = []
    for index, rule in enumerate(_list(group.get("rules"), f"{label}.rules", flexible=not global_group)):
        if isinstance(rule, dict):
            result.append(rule)
        elif not global_group and isinstance(rule, (str, list)):
            _strings(rule, f"{label}.rules[{index}]")
            result.append({"matches": rule})
        else:
            raise MergeError(f"{label}.rules[{index}]: invalid rule")
    return result


def _check_props(obj: dict[str, Any], label: str) -> None:
    for name in ("enable", "fastQuery", "matchRoot", "matchSystemApp", "matchAnyApp", "matchLauncher", "ignoreGlobalGroupMatch"):
        if obj.get(name) is not None and type(obj[name]) is not bool:
            raise MergeError(f"{label}.{name}: expected boolean")
    for name in ("actionCdKey", "actionMaximumKey", "key", "order", "actionMaximum", "priorityActionMaximum"):
        if obj.get(name) is not None:
            _int(obj[name], f"{label}.{name}")
    for name in ("actionCd", "actionDelay", "matchDelay", "matchTime", "forcedTime", "priorityTime", "cd", "delay"):
        if obj.get(name) is not None:
            _int(obj[name], f"{label}.{name}", 0, 2**63 - 1)
    for name in ("matches", "anyMatches", "excludeMatches", "excludeAllMatches", "activityIds", "excludeActivityIds", "snapshotUrls", "excludeSnapshotUrls", "exampleUrls"):
        if name in obj:
            _strings(obj[name], f"{label}.{name}")
    for name in ("scopeKeys", "preKeys"):
        if name in obj:
            _ints(obj[name], f"{label}.{name}")
    if obj.get("apps") is not None:
        apps = _list(obj["apps"], f"{label}.apps")
        ids = []
        for index, app in enumerate(apps):
            if not isinstance(app, dict):
                raise MergeError(f"{label}.apps[{index}]: expected object")
            ids.append(_package(app.get("id"), f"{label}.apps[{index}].id"))
            _check_props(app, f"{label}.apps[{index}]")
        _unique(ids, f"{label}.apps")


def _package(value: Any, label: str) -> str:
    value = _str(value, label)
    if not PACKAGE.fullmatch(value):
        raise MergeError(f"{label}: invalid Android package identifier")
    return value


def _check_groups(groups: list[Any], label: str, global_groups: bool = False) -> list[str]:
    warnings = []
    keyed = {}
    rules_by_group = {}
    for index, group in enumerate(groups):
        place = f"{label}[{index}]"
        if not isinstance(group, dict):
            raise MergeError(f"{place}: expected group object")
        key = _int(group.get("key"), f"{place}.key")
        _str(group.get("name"), f"{place}.name")
        _check_props(group, place)
        if key in keyed:
            raise MergeError(f"{label}: duplicate group key {key}")
        keyed[key] = group
        rules = _rule_objects(group, place, global_groups)
        if not rules:
            warnings.append(f"{place}: empty group preserved from upstream (no actions)")
        rule_keys = []
        for rule_index, rule in enumerate(rules):
            rule_place = f"{place}.rules[{rule_index}]"
            _check_props(rule, rule_place)
            if rule.get("key") is not None:
                rule_keys.append(_int(rule["key"], f"{rule_place}.key"))
        _unique(rule_keys, f"{place}.rules")
        rules_by_group[key] = rules
    for key, group in keyed.items():
        scope = _ints(group.get("scopeKeys"), f"{label}[key={key}].scopeKeys")
        missing_scope = [target for target in scope if target not in keyed]
        if missing_scope:
            warnings.append(f"{label}[key={key}]: missing scopeKeys {missing_scope}; preserved upstream behavior")
        valid_rule_keys = {
            rule["key"]
            for scoped in [key] + scope
            for rule in rules_by_group.get(scoped, [])
            if rule.get("key") is not None
        }
        for index, rule in enumerate(rules_by_group[key]):
            for field in ("preKeys", "actionCdKey", "actionMaximumKey"):
                # Group defaults participate only when a rule does not override them.
                value = rule.get(field, group.get(field))
                missing = [target for target in _ints(value, field) if target not in valid_rule_keys]
                if missing:
                    warnings.append(f"{label}[key={key}].rules[{index}]: missing {field} {missing}; preserved upstream behavior")
    return warnings


def validate_subscription(data: dict[str, Any], label: str = "subscription") -> list[str]:
    if not isinstance(data, dict):
        raise MergeError(f"{label}: expected object")
    _no_nonfinite(data, label)
    _int(data.get("id"), f"{label}.id", 1, 2**63 - 1)
    _int(data.get("version"), f"{label}.version", 1)
    _str(data.get("name"), f"{label}.name")
    apps = _list(data.get("apps"), f"{label}.apps")
    categories = _list(data.get("categories"), f"{label}.categories")
    globals_ = _list(data.get("globalGroups"), f"{label}.globalGroups")
    category_keys, category_names = [], []
    for index, category in enumerate(categories):
        if not isinstance(category, dict):
            raise MergeError(f"{label}.categories[{index}]: expected object")
        category_keys.append(_int(category.get("key"), f"{label}.categories[{index}].key"))
        category_names.append(_str(category.get("name"), f"{label}.categories[{index}].name"))
        _check_props(category, f"{label}.categories[{index}]")
    _unique(category_keys, f"{label}.categories.keys")
    _unique(category_names, f"{label}.categories.names")
    ids, warnings = [], []
    for index, app in enumerate(apps):
        place = f"{label}.apps[{index}]"
        if not isinstance(app, dict):
            raise MergeError(f"{place}: expected object")
        ids.append(_package(app.get("id"), f"{place}.id"))
        groups = _list(app.get("groups"), f"{place}.groups", flexible=True)
        warnings.extend(_check_groups(groups, f"{label}.apps[{app['id']}].groups"))
    _unique(ids, f"{label}.apps")
    warnings.extend(_check_groups(globals_, f"{label}.globalGroups", global_groups=True))
    if not globals_ and not any(app.get("groups") for app in apps):
        raise MergeError(f"{label}: empty subscription")
    return warnings


def download(url: str, max_bytes: int = MAX_BYTES) -> bytes:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise MergeError("Every upstream URL must use HTTPS")
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "tgmzo-gkd-one-link/1.0", "Accept": "application/json, text/plain, */*"})
            with urllib.request.urlopen(request, timeout=45) as response:
                if not response.geturl().startswith("https://"):
                    raise MergeError("Upstream redirected to insecure HTTP")
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise MergeError(f"Upstream exceeds {max_bytes} bytes")
                return raw
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(attempt + 1)
    raise MergeError(f"Could not download {url} after 3 attempts: {last_error}")


def validate_source_archive(raw: bytes, label: str) -> None:
    """Check full gzip CRC and tar structure without extracting any source files."""
    try:
        expanded = 0
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            while chunk := stream.read(1024 * 1024):
                expanded += len(chunk)
                if expanded > 256 * 1024 * 1024:
                    raise MergeError(f"{label}: source archive expands beyond 256 MiB")
        members = regular_files = 0
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for member in archive:
                members += 1
                regular_files += int(member.isfile())
                if members > 100_000:
                    raise MergeError(f"{label}: source archive has too many members")
        if not regular_files:
            raise MergeError(f"{label}: source archive contains no regular files")
    except (OSError, EOFError, tarfile.TarError) as error:
        raise MergeError(f"{label}: invalid or incomplete corresponding-source archive: {error}") from error


def load_config(path: Path) -> list[dict[str, Any]]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MergeError(f"Cannot read config {path}: {error}") from error
    sources = config.get("sources") if isinstance(config, dict) else config
    if not isinstance(sources, list) or not sources:
        raise MergeError("sources.json must contain a nonempty sources list")
    keys = []
    for source in sources:
        if not isinstance(source, dict):
            raise MergeError("Every source must be an object")
        key = _str(source.get("key"), "source.key")
        if not SOURCE_KEY.fullmatch(key):
            raise MergeError(f"Invalid source key: {key}")
        keys.append(key)
        _str(source.get("name"), f"{key}.name")
        _str(source.get("url"), f"{key}.url")
        if source.get("redistribution_approved") is not True:
            raise MergeError(f"{key}: public redistribution permission has not been established; no output changed")
    _unique(keys, "source keys")
    return sources


def _load_previous(output: Path) -> dict[str, Any] | None:
    path = output / "gkd.json5"
    if not path.exists():
        if (output / "gkd.version.json5").exists():
            raise MergeError("Version metadata exists without a subscription; refusing to reset version")
        return None
    data = parse_subscription(path.read_bytes(), "previous output")
    validate_subscription(data, "previous output")
    if data["id"] != SUBSCRIPTION_ID:
        raise MergeError("Existing subscription id differs; refusing to replace it")
    meta_path = output / "gkd.version.json5"
    if meta_path.exists():
        meta = parse_subscription(meta_path.read_bytes(), "previous version metadata")
        if meta.get("id") != data["id"] or meta.get("version") != data["version"]:
            raise MergeError("Existing subscription and version metadata disagree")
    return data


def merge_subscriptions(documents: list[dict[str, Any]], previous: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, int], list[int]]:
    apps, winners, contributions = [], {}, []
    for index, document in enumerate(documents):
        added = 0
        for app in document.get("apps", []):
            if app["id"] not in winners:
                winners[app["id"]] = index
                apps.append(copy.deepcopy(app))
                added += 1
        contributions.append(added)
    # Categories are matched by group-name prefix in GKD, never by category key.
    # Reuse old keys by name to keep GKD's per-category preferences stable.
    old_keys = {c["name"]: c["key"] for c in (previous or {}).get("categories", [])}
    categories, seen_names, used_keys = [], set(), set(old_keys.values())
    next_key = max([0, *used_keys]) + 1
    for document in documents:
        for category in document.get("categories", []):
            name = category["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            result = copy.deepcopy(category)
            if name in old_keys:
                result["key"] = old_keys[name]
            elif result["key"] in used_keys:
                while next_key in used_keys:
                    next_key += 1
                result["key"] = next_key
                next_key += 1
            used_keys.add(result["key"])
            categories.append(result)
    merged = {
        "id": SUBSCRIPTION_ID,
        "name": SUBSCRIPTION_NAME,
        "version": 1,
        "author": "tgmzo；规则作者见仓库致谢",
        "updateUrl": BASE_URL + "gkd.json5",
        "checkUpdateUrl": BASE_URL + "gkd.version.json5",
        "supportUri": "https://github.com/tgmzo/gkd-one-link/issues",
        "categories": categories,
        "globalGroups": copy.deepcopy(documents[0].get("globalGroups", [])),
        "apps": apps,
    }
    return merged, winners, contributions


def _payload(data: dict[str, Any]) -> str:
    return canonical({key: value for key, value in data.items() if key != "version"})


def _publish(output: Path, files: dict[str, bytes]) -> list[str]:
    # Nothing is touched until every file has been encoded and staged successfully.
    output.mkdir(parents=True, exist_ok=True)
    changed = [name for name, raw in files.items() if not (output / name).is_file() or (output / name).read_bytes() != raw]
    with tempfile.TemporaryDirectory(prefix=".gkd-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        for name in changed:
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as stream:
                stream.write(files[name])
                stream.flush()
                os.fsync(stream.fileno())
        for name in changed:
            (output / name).parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / name, output / name)
    return changed


def run_merge(config_path: Path | str, input_dir: Path | str | None = None, output_dir: Path | str | None = None) -> dict[str, Any]:
    config_path = Path(config_path)
    sources = load_config(config_path)  # Permission gate precedes all downloads.
    output = Path(output_dir) if output_dir is not None else config_path.parent / "dist"
    fixtures = Path(input_dir) if input_dir is not None else None
    previous = _load_previous(output)
    documents, provenance, warnings = [], [], []
    source_files: dict[str, bytes] = {}
    for source in sources:
        if fixtures:
            raw = (fixtures / f"{source['key']}.json5").read_bytes()
            commit_sha = "offline-fixture"
            resolved_url = source["url"]
        else:
            repository = _str(source.get("repository"), f"{source['key']}.repository")
            ref = _str(source.get("ref"), f"{source['key']}.ref")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
                raise MergeError(f"{source['key']}: invalid GitHub repository")
            commit_url = f"https://api.github.com/repos/{repository}/commits/{urllib.parse.quote(ref, safe='')}"
            try:
                commit = json.loads(download(commit_url).decode("utf-8"))
                commit_sha = commit["sha"]
            except (ValueError, KeyError, TypeError) as error:
                raise MergeError(f"{source['key']}: cannot resolve GitHub source commit") from error
            if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
                raise MergeError(f"{source['key']}: invalid resolved commit SHA")
            prefix = f"https://raw.githubusercontent.com/{repository}/{ref}/"
            if not source["url"].startswith(prefix):
                raise MergeError(f"{source['key']}: raw URL does not match configured repository and ref")
            resolved_url = f"https://raw.githubusercontent.com/{repository}/{commit_sha}/" + source["url"][len(prefix):]
            raw = download(resolved_url)
        source_files[f"upstream/{source['key']}.json5"] = raw
        archive_name = None
        archive_sha256 = None
        if source.get("archive_source") is True:
            if fixtures:
                archive_name = "offline-fixture (archive download skipped)"
            else:
                archive_name = f"upstream/{source['key']}-source.tar.gz"
                archive = download(f"https://api.github.com/repos/{source['repository']}/tarball/{commit_sha}", max_bytes=30 * 1024 * 1024)
                # GitHub's tarball endpoint must return a gzip archive, not an API error page.
                validate_source_archive(archive, source["key"])
                source_files[archive_name] = archive
                archive_sha256 = hashlib.sha256(archive).hexdigest()
        document = parse_subscription(raw, source["key"])
        warnings.extend(validate_subscription(document, source["key"]))
        documents.append(document)
        provenance.append({
            **{key: source[key] for key in ("key", "name", "url", "repository", "ref", "license", "license_evidence", "authorization_record") if key in source},
            "commit_sha": commit_sha,
            "resolved_url": resolved_url,
            "corresponding_source_archive": archive_name,
            "archive_sha256": archive_sha256,
            "upstream_id": document["id"],
            "upstream_version": document["version"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "app_count": len(document.get("apps", [])),
            "global_group_count": len(document.get("globalGroups", [])),
        })
    merged, winners, contributions = merge_subscriptions(documents, previous)
    changed_payload = previous is None or _payload(merged) != _payload(previous)
    merged["version"] = 1 if previous is None else previous["version"] + int(changed_payload)
    _int(merged["version"], "merged.version", 1)
    warnings.extend(validate_subscription(merged, "merged"))
    for index, source in enumerate(provenance):
        source["contributed_apps"] = contributions[index]
        source["skipped_apps"] = source["app_count"] - contributions[index]
    report = {
        "subscription_id": merged["id"],
        "version": merged["version"],
        "payload_sha256": hashlib.sha256(_payload(merged).encode("utf-8")).hexdigest(),
        "policy": "First source wins each complete app; only the first source supplies global groups; category names are unioned in priority order.",
        "sources": provenance,
        "app_sources": {package: sources[index]["key"] for package, index in sorted(winners.items())},
        "app_count": len(merged["apps"]),
        "app_group_count": sum(len(_list(app.get("groups"), "groups", flexible=True)) for app in merged["apps"]),
        "global_group_count": len(merged["globalGroups"]),
        "warnings": sorted(set(warnings)),
    }
    metadata = {"id": merged["id"], "name": merged["name"], "version": merged["version"]}
    def encoded(value: Any) -> bytes:
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    files = {**source_files, "gkd.json5": encoded(merged), "gkd.version.json5": encoded(metadata), "merge-report.json": encoded(report)}
    # Confirm roundtrip before writing anything to the published directory.
    validate_subscription(parse_subscription(files["gkd.json5"], "serialized result"), "serialized result")
    changed_files = _publish(output, files)
    return {"version": merged["version"], "payload_changed": changed_payload, "changed_files": changed_files, "app_count": report["app_count"], "app_group_count": report["app_group_count"], "global_group_count": report["global_group_count"], "warning_count": len(report["warnings"])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "sources.json")
    parser.add_argument("--input-dir", type=Path, help="Offline fixtures directory containing <source key>.json5")
    parser.add_argument("--output-dir", type=Path, help="Output directory (default: dist beside sources.json)")
    args = parser.parse_args(argv)
    try:
        summary = run_merge(args.config, args.input_dir, args.output_dir)
    except (MergeError, OSError) as error:
        print(f"Merge aborted: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
