#!/usr/bin/env python3
"""Validate dynamic ABI APK output sets and recover per-source changelog markers."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

ABI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def load_config(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        config = json.loads(path.read_text(encoding="utf-8"))
    else:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"Configuration root must be an object/table: {path}")
    return config


def _normalized(value: Any) -> str:
    return str(value).lower().replace(" ", "-")


def analyze_build(
    config: dict[str, Any],
    build_dir: Path,
    manifest_dir: Path,
) -> dict[str, Any]:
    """Build the expected output set from the ABI manifest each profile produced."""
    default_source = config.get("patches-source", "ReVanced/revanced-patches")
    default_brand = config.get("rv-brand", "ReVanced")
    profiles: list[dict[str, Any]] = []

    for table_name, options in config.items():
        if not isinstance(options, dict):
            continue
        enabled = options.get("enabled", True)
        if enabled is False or str(enabled).lower() == "false":
            continue

        app_name = _normalized(options.get("app-name", table_name))
        brand = _normalized(options.get("rv-brand", default_brand))
        source = str(options.get("patches-source", default_source))
        arch = str(options.get("arch", "all"))
        build_mode = str(options.get("build-mode", "apk")).lower()
        manifest = manifest_dir / f"{app_name}-{brand}.abis"
        abis: list[str] = []
        expected: list[str] = []
        manifest_error = ""

        if arch != "all" or build_mode != "apk":
            manifest_error = (
                f"arch={arch}, build-mode={build_mode}; this release workflow requires "
                "build-mode=apk and arch=all for ABI splitting"
            )
        elif not manifest.is_file():
            manifest_error = "dynamic ABI manifest is missing"
        else:
            try:
                raw_abis = [
                    line.strip()
                    for line in manifest.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except (OSError, UnicodeDecodeError) as exc:
                raw_abis = []
                manifest_error = f"cannot read ABI manifest: {exc}"

            if not manifest_error:
                if not raw_abis:
                    manifest_error = "ABI manifest is empty"
                elif any(not ABI_NAME.fullmatch(abi) for abi in raw_abis):
                    manifest_error = "ABI manifest contains an invalid ABI name"
                elif len(raw_abis) != len(set(raw_abis)):
                    manifest_error = "ABI manifest contains duplicate ABI names"
                else:
                    abis = raw_abis
                    expected = [f"{app_name}-{abi}-{brand}.apk" for abi in abis]

        prefix = f"{app_name}-"
        suffix = f"-{brand}.apk"
        actual_profile = {
            path.name
            for path in build_dir.glob("*.apk")
            if path.is_file()
            and path.name.startswith(prefix)
            and path.name.endswith(suffix)
        }
        missing = sorted(name for name in expected if not (build_dir / name).is_file())
        unexpected = sorted(actual_profile - set(expected))
        complete = bool(expected) and not missing and not unexpected and not manifest_error

        if complete:
            print(
                f"{table_name}: {len(expected)} ABI APK(s) present and match the manifest "
                f"({', '.join(abis)})."
            )
        else:
            details = []
            if manifest_error:
                details.append(manifest_error)
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unexpected:
                details.append("unexpected: " + ", ".join(unexpected))
            print(f"{table_name}: INCOMPLETE — {'; '.join(details) or 'no expected ABI APKs'}.")
        profiles.append({
            "table": table_name,
            "app_name": app_name,
            "source": source,
            "brand": brand,
            "abis": abis,
            "expected": expected,
            "complete": complete,
            "manifest_error": manifest_error,
            "missing": missing,
            "unexpected": unexpected,
        })

    by_brand: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        by_brand[profile["brand"]].append(profile)
        by_source[profile["source"]].append(profile)

    brand_status: dict[str, dict[str, Any]] = {}
    for brand, items in by_brand.items():
        expected_count = sum(len(item["expected"]) for item in items)
        actual_count = sum(
            1 for path in build_dir.glob("*.apk")
            if path.is_file() and path.name.endswith(f"-{brand}.apk")
        )
        source_groups_complete = all(
            all(profile["complete"] for profile in by_source[item["source"]])
            for item in items
        )
        complete = (
            expected_count > 0
            and all(item["complete"] for item in items)
            and source_groups_complete
            and actual_count == expected_count
        )
        brand_status[brand] = {
            "complete": complete,
            "expected": expected_count,
            "actual": actual_count,
        }
        print(
            f"Release group {brand}: {actual_count}/{expected_count} APK(s); "
            f"complete={complete}."
        )

    return {"brands": brand_status, "profiles": profiles}


def _record_pattern(source: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?m)^Patches: {re.escape(source)}/[^\r\n]*"
        rf"(?:\r?\n\[Changelog\]\([^\r\n]*\))?"
    )


def recover_changelog(current: str, previous: str, profiles: list[dict[str, Any]]) -> str:
    """Keep a new source marker only when every enabled profile using it is complete."""
    def get_record(text: str, source: str) -> str:
        match = _record_pattern(source).search(text)
        return match.group(0).strip() if match else ""

    def sources_in(text: str) -> list[str]:
        return re.findall(r"(?m)^Patches: ([^/\r\n]+/[^/\r\n]+)/", text)

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        by_source[profile["source"]].append(profile)

    source_order: list[str] = []
    for source in (
        [profile["source"] for profile in profiles]
        + sources_in(current)
        + sources_in(previous)
    ):
        if source not in source_order:
            source_order.append(source)

    chosen_records: list[str] = []
    for source in source_order:
        new_record = get_record(current, source)
        old_record = get_record(previous, source)
        items = by_source.get(source, [])

        if not items:
            chosen = old_record or new_record
            print(f"{source}: not part of this build; preserving its previous marker.")
        elif all(item["complete"] for item in items):
            chosen = new_record or old_record
            print(f"{source}: every configured profile is complete; keeping the current marker.")
        else:
            chosen = old_record
            print(f"{source}: at least one configured profile is incomplete; restoring the previous marker.")

        if chosen:
            chosen_records.append(chosen)

    for source in source_order:
        current = _record_pattern(source).sub("", current)

    current = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", current).rstrip()
    if chosen_records:
        current += "\n\n" + "\n\n".join(chosen_records)
    return current.rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--manifest-dir", type=Path, default=Path(".build-abi-manifests"))
    parser.add_argument("--current-changelog", type=Path, default=Path("build.md"))
    parser.add_argument("--previous-changelog", type=Path, default=Path("build.previous.md"))
    parser.add_argument("--status-file", type=Path, default=Path("build-status.json"))
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        current = args.current_changelog.read_text(encoding="utf-8")
        previous = (
            args.previous_changelog.read_text(encoding="utf-8")
            if args.previous_changelog.is_file()
            else ""
        )
        status = analyze_build(config, args.build_dir, args.manifest_dir)
        updated = recover_changelog(current, previous, status["profiles"])
        args.status_file.write_text(
            json.dumps(status, indent=2) + "\n",
            encoding="utf-8",
        )
        args.current_changelog.write_text(updated, encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: could not finalize build status: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
