#!/usr/bin/env python3
"""Independently validate finished ABI-split APKs before release publication."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from finalize_build import recover_changelog

ABI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PACKAGE_BY_BRAND = {
    "rv": "app.revanced.android.youtube",
    "morphe": "app.morphe.android.youtube",
    "rvx-anddea": "app.rvx.android.youtube",
    "rvx-a6-7": "app.rvx.android.youtube",
}


class ValidationError(RuntimeError):
    """An APK or release group failed a validation gate."""


def normalized(value: Any) -> str:
    return str(value).lower().replace(" ", "-")


def load_config(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError(f"Configuration root is not a table/object: {path}")
    return data


def version_key(path: Path) -> tuple[int, ...]:
    nums = re.findall(r"\d+", path.name)
    return tuple(int(n) for n in nums) or (0,)


def discover_build_tools(explicit: Path | None = None) -> Path:
    if explicit is not None:
        root = explicit
    else:
        sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if not sdk:
            raise ValidationError("ANDROID_HOME/ANDROID_SDK_ROOT is missing; cannot find Android Build Tools.")
        build_tools_root = Path(sdk) / "build-tools"
        candidates = [p for p in build_tools_root.iterdir() if p.is_dir()] if build_tools_root.is_dir() else []
        if not candidates:
            raise ValidationError(f"No Android Build Tools directories found under {build_tools_root}.")
        root = sorted(candidates, key=version_key)[-1]

    for tool in ("aapt2", "zipalign", "apksigner"):
        path = root / tool
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValidationError(f"Required executable is missing: {path}")
    return root


def run_checked(command: list[str], label: str) -> str:
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    except OSError as exc:
        raise ValidationError(f"Could not run {label}: {exc}") from exc
    if result.returncode != 0:
        output = (result.stdout or "").strip()
        if len(output) > 3000:
            output = output[-3000:]
        raise ValidationError(f"{label} failed (exit {result.returncode})" + (f":\n{output}" if output else "."))
    return result.stdout or ""


def certificate_sha256(cert_path: Path) -> str:
    if not cert_path.is_file():
        raise ValidationError(f"Signing certificate is missing: {cert_path}")
    output = run_checked(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-fingerprint", "-sha256"],
        f"OpenSSL certificate fingerprint for {cert_path.name}",
    )
    match = re.search(r"(?i)sha256\s+Fingerprint\s*=\s*([0-9a-f:]+)", output)
    if not match:
        raise ValidationError(f"Could not parse SHA-256 fingerprint from OpenSSL output for {cert_path.name}.")
    digest = re.sub(r"[^0-9a-f]", "", match.group(1).lower())
    if len(digest) != 64:
        raise ValidationError(f"Unexpected SHA-256 fingerprint length for {cert_path.name}.")
    return digest


def get_package_expectation(options: dict[str, Any], brand: str) -> str:
    explicit = options.get("package-name") or options.get("packageNameYouTube")
    if explicit:
        return str(explicit)
    patcher_args = str(options.get("patcher-args", ""))
    match = re.search(r"(?:^|\s)-OpackageNameYouTube=([^\s'\"]+)", patcher_args)
    if match:
        return match.group(1)
    expected = PACKAGE_BY_BRAND.get(brand)
    if expected:
        return expected
    raise ValidationError(
        f"No expected Android package ID is configured for brand {brand!r}; add an explicit package-name mapping."
    )


def expected_version_from_changelog(changelog: str, profile: str) -> str:
    match = re.search(rf"(?m)^{re.escape(profile)}:\s+(\S+)\s*$", changelog)
    if not match:
        raise ValidationError(f"{profile}: current build.md does not contain a successful app-version entry.")
    return match.group(1)


def parse_badging(output: str, apk_name: str) -> tuple[str, str, str]:
    match = re.search(
        r"(?m)^package:\s+name='([^']+)'\s+versionCode='([^']+)'\s+versionName='([^']*)'",
        output,
    )
    if not match:
        raise ValidationError(f"{apk_name}: aapt2 dump badging did not return package, versionCode and versionName.")
    package_name, version_code, version_name = match.groups()
    if not package_name or not version_code or not version_name:
        raise ValidationError(f"{apk_name}: Android package name, versionCode or versionName is empty.")
    return package_name, version_code, version_name


def check_zip_and_abis(path: Path, expected_abi: str) -> set[str]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValidationError(f"{path.name}: APK is missing or empty.")
    try:
        with zipfile.ZipFile(path) as apk:
            names = apk.namelist()
            if len(names) != len(set(names)):
                raise ValidationError(f"{path.name}: ZIP contains duplicate entry names.")
            corrupt = apk.testzip()
            if corrupt is not None:
                raise ValidationError(f"{path.name}: ZIP integrity check failed at entry {corrupt!r}.")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValidationError(f"{path.name}: unreadable or corrupt APK/ZIP: {exc}") from exc

    entries = set(names)
    if "AndroidManifest.xml" not in entries:
        raise ValidationError(f"{path.name}: AndroidManifest.xml is missing.")
    if not any(name == "classes.dex" or re.fullmatch(r"classes\d+\.dex", name) for name in entries):
        raise ValidationError(f"{path.name}: no classes.dex / classesN.dex code entry was found.")

    abis: set[str] = set()
    for name in names:
        if name.startswith("lib/") and name.endswith(".so"):
            parts = name.split("/")
            if len(parts) < 3 or not parts[1]:
                raise ValidationError(f"{path.name}: malformed native library path {name!r}.")
            abis.add(parts[1])
    if abis != {expected_abi}:
        got = ", ".join(sorted(abis)) or "none"
        raise ValidationError(f"{path.name}: expected only ABI {expected_abi!r}, found {got!r}.")
    return entries


def verify_apk(
    path: Path,
    abi: str,
    tools: Path,
    cert_digest: str,
    expected_package: str,
) -> tuple[str, str, str]:
    check_zip_and_abis(path, abi)

    badging = run_checked([str(tools / "aapt2"), "dump", "badging", str(path)], f"aapt2 badging for {path.name}")
    identity = parse_badging(badging, path.name)
    package_name, version_code, version_name = identity
    if package_name != expected_package:
        raise ValidationError(
            f"{path.name}: expected package {expected_package!r}, found {package_name!r}."
        )

    signer_output = run_checked(
        [str(tools / "apksigner"), "verify", "--verbose", "--print-certs", str(path)],
        f"apksigner verification for {path.name}",
    )
    signer_digests = {
        re.sub(r"[^0-9a-f]", "", digest.lower())
        for digest in re.findall(
            r"(?im)^(?:Signer #\d+ certificate|V\d+(?:\.\d+)? Signer: certificate) SHA-256 digest:\s*([0-9a-f:]+)\s*$",
            signer_output,
        )
    }
    if cert_digest not in signer_digests:
        raise ValidationError(
            f"{path.name}: expected signing certificate SHA-256 {cert_digest}; "
            f"apksigner reported {', '.join(sorted(signer_digests)) if signer_digests else 'no recognized certificate digest'}."
        )

    run_checked(
        [str(tools / "zipalign"), "-c", "-P", "16", "4", str(path)],
        f"16 KiB zipalign verification for {path.name}",
    )
    return identity


def validate_build(
    config_path: Path,
    build_dir: Path,
    manifest_dir: Path,
    status_path: Path,
    previous_changelog_path: Path,
    current_changelog_path: Path,
    tools_dir: Path | None = None,
    signing_cert_path: Path = Path("ks.jhc.x509.pem"),
) -> dict[str, Any]:
    config = load_config(config_path)
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Could not read build status {status_path}: {exc}") from exc

    current_changelog = current_changelog_path.read_text(encoding="utf-8") if current_changelog_path.is_file() else ""
    profile_statuses = {item.get("table"): item for item in status.get("profiles", [])}
    config_profiles: list[tuple[str, dict[str, Any], str, str]] = []
    for table, options in config.items():
        if not isinstance(options, dict):
            continue
        enabled = options.get("enabled", True)
        if enabled is False or str(enabled).lower() == "false":
            continue
        app_name = normalized(options.get("app-name", table))
        brand = normalized(options.get("rv-brand", config.get("rv-brand", "ReVanced")))
        config_profiles.append((table, options, app_name, brand))

    if not config_profiles:
        raise ValidationError("No enabled profiles found in the build config.")

    errors_by_brand: dict[str, list[str]] = defaultdict(list)
    expected_names_by_brand: dict[str, set[str]] = defaultdict(set)
    profiles_by_brand: dict[str, list[dict[str, Any]]] = defaultdict(list)
    configured_count_by_brand: dict[str, int] = defaultdict(int)
    for _, _, _, brand in config_profiles:
        configured_count_by_brand[brand] += 1
    tools: Path | None = None
    cert_digest: str | None = None

    for table, options, app_name, brand in config_profiles:
        profile_status = profile_statuses.get(table)
        if profile_status is None:
            errors_by_brand[brand].append(f"{table}: profile is missing from build-status.json.")
            continue
        profile_status.setdefault("validation_error", "")
        profiles_by_brand[brand].append(profile_status)
        if not profile_status.get("complete", False):
            errors_by_brand[brand].append(f"{table}: build output set is already incomplete.")
            continue

        if str(options.get("build-mode", "apk")).lower() != "apk" or str(options.get("arch", "all")) != "all":
            message = f"{table}: this release gate requires build-mode=apk and arch=all."
            profile_status["complete"] = False
            profile_status["validation_error"] = message
            errors_by_brand[brand].append(message)
            continue

        manifest_path = manifest_dir / f"{app_name}-{brand}.abis"
        if not manifest_path.is_file():
            message = f"{table}: ABI manifest is missing ({manifest_path})."
            profile_status["complete"] = False
            profile_status["validation_error"] = message
            errors_by_brand[brand].append(message)
            continue

        abis = [line.strip() for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not abis or len(abis) != len(set(abis)) or any(not ABI_NAME.fullmatch(abi) for abi in abis):
            message = f"{table}: ABI manifest is empty, duplicated or contains an invalid ABI name: {abis!r}."
            profile_status["complete"] = False
            profile_status["validation_error"] = message
            errors_by_brand[brand].append(message)
            continue

        expected_names = {f"{app_name}-{abi}-{brand}.apk" for abi in abis}
        expected_names_by_brand[brand].update(expected_names)
        actual_names = {
            path.name for path in build_dir.glob("*.apk")
            if path.is_file() and path.name.startswith(f"{app_name}-") and path.name.endswith(f"-{brand}.apk")
        }
        if actual_names != expected_names:
            message = (
                f"{table}: APK files do not exactly match the ABI manifest; "
                f"expected={sorted(expected_names)}, actual={sorted(actual_names)}."
            )
            profile_status["complete"] = False
            profile_status["validation_error"] = message
            errors_by_brand[brand].append(message)
            continue

        try:
            if tools is None:
                tools = discover_build_tools(tools_dir)
            if cert_digest is None:
                cert_digest = certificate_sha256(signing_cert_path)
            expected_package = get_package_expectation(options, brand)
            expected_version = expected_version_from_changelog(current_changelog, table)
            identities: set[tuple[str, str, str]] = set()
            for abi in sorted(abis):
                apk_path = build_dir / f"{app_name}-{abi}-{brand}.apk"
                identity = verify_apk(apk_path, abi, tools, cert_digest, expected_package)
                if identity[2] != expected_version:
                    raise ValidationError(
                        f"{apk_path.name}: versionName {identity[2]!r} does not match build.md version {expected_version!r}."
                    )
                identities.add(identity)
                print(
                    f"PASS {apk_path.name}: ZIP, manifest/DEX, ABI={abi}, package={identity[0]}, "
                    f"version={identity[2]}, signature certificate and 16 KiB alignment."
                )
            if len(identities) != 1:
                raise ValidationError(f"{table}: split APKs disagree on package name or version metadata: {sorted(identities)}")
        except ValidationError as exc:
            message = f"{table}: {exc}"
            profile_status["complete"] = False
            profile_status["validation_error"] = message
            errors_by_brand[brand].append(message)
        else:
            profile_status["validated"] = True
            profile_status["validation_error"] = ""

    # Catch unconfigured/stale APKs in each release family, not merely per-profile extras.
    for brand, profile_status_list in profiles_by_brand.items():
        actual_brand_names = {
            path.name for path in build_dir.glob("*.apk")
            if path.is_file() and path.name.endswith(f"-{brand}.apk")
        }
        expected_brand_names = expected_names_by_brand[brand]
        if actual_brand_names != expected_brand_names:
            message = (
                f"{brand}: release-family APK set mismatch; expected={sorted(expected_brand_names)}, "
                f"actual={sorted(actual_brand_names)}."
            )
            errors_by_brand[brand].append(message)
            for profile_status in profile_status_list:
                profile_status["complete"] = False
                prior = profile_status.get("validation_error", "")
                profile_status["validation_error"] = "; ".join(p for p in (prior, message) if p)
                profile_status["validated"] = False

    brand_statuses = status.setdefault("brands", {})
    all_brands = {brand for _, _, _, brand in config_profiles}
    for brand in all_brands:
        profiles = [
            profile_statuses[table]
            for table, _, _, item_brand in config_profiles
            if item_brand == brand and table in profile_statuses
        ]
        expected = sum(len(profile.get("expected", [])) for profile in profiles)
        actual = sum(
            1 for path in build_dir.glob("*.apk")
            if path.is_file() and path.name.endswith(f"-{brand}.apk")
        )
        complete = (
            bool(profiles)
            and len(profiles) == configured_count_by_brand[brand]
            and expected > 0
            and all(profile.get("complete", False) for profile in profiles)
            and actual == expected
        )
        brand_statuses[brand] = {"complete": complete, "expected": expected, "actual": actual}
        if complete:
            print(f"PASS release group {brand}: {actual}/{expected} APK(s) independently validated.")
        else:
            print(f"BLOCK release group {brand}: {actual}/{expected} APK(s); no release will be published for this group.")
            for error in errors_by_brand.get(brand, []):
                print(f"  ERROR: {error}")

    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    current = current_changelog_path.read_text(encoding="utf-8") if current_changelog_path.is_file() else ""
    previous = previous_changelog_path.read_text(encoding="utf-8") if previous_changelog_path.is_file() else ""
    recovered = recover_changelog(current, previous, status.get("profiles", []))
    current_changelog_path.write_text(recovered, encoding="utf-8")

    if not any(item.get("complete", False) for item in brand_statuses.values()):
        raise ValidationError("No release group passed independent APK validation; stopping before release publication and cleanup.")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--manifest-dir", type=Path, default=Path(".build-abi-manifests"))
    parser.add_argument("--status-file", type=Path, default=Path("build-status.json"))
    parser.add_argument("--current-changelog", type=Path, default=Path("build.md"))
    parser.add_argument("--previous-changelog", type=Path, default=Path("build.previous.md"))
    parser.add_argument("--tools-dir", type=Path, default=None)
    parser.add_argument("--signing-cert", type=Path, default=Path("ks.jhc.x509.pem"))
    args = parser.parse_args()
    try:
        validate_build(
            args.config,
            args.build_dir,
            args.manifest_dir,
            args.status_file,
            args.previous_changelog,
            args.current_changelog,
            args.tools_dir,
            args.signing_cert,
        )
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
