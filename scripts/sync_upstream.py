#!/usr/bin/env python3
"""Sync upstream j-hc builder scripts while preserving this fork's overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "j-hc/revanced-magisk-module"
API_URL = f"https://api.github.com/repos/{UPSTREAM}/commits/main"
RAW_URL = f"https://raw.githubusercontent.com/{UPSTREAM}"


def fetch(url: str, *, accept: str = "text/plain") -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "revanced-arm7-arm8-nonroot-upstream-sync",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def _require_one(
    pattern: str,
    content: str,
    description: str,
    source: str = "upstream utils.sh",
) -> re.Match[str]:
    matches = list(re.finditer(pattern, content, re.MULTILINE))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {description} in {source}; found {len(matches)}. "
            "Refusing to sync an incompatible change."
        )
    return matches[0]


def validate_upstream_patcher_order(content: str) -> None:
    """Fail closed if upstream no longer adds GmsCore before calling patch_apk().

    In the current upstream layout, build_rv() and this argument assembly live in
    utils.sh (not build.sh). The fork appends its custom package-name option inside
    the patch_apk wrapper, so the GmsCore option must already be in patcher_args
    before build_rv() passes that string to patch_apk().
    """
    args_copy = _require_one(
        r'^\s*patcher_args\s*=\s*\("\$\{p_patcher_args\[@\]\}"\)\s*$',
        content,
        "copy of p_patcher_args into patcher_args",
    )
    apk_guard = _require_one(
        r'^\s*if\s+\[\s*"\$build_mode"\s*=\s*"apk"\s*\]\s*;\s*then\s*$',
        content,
        "APK-mode guard for GmsCore Support",
    )
    gmscore_append = _require_one(
        r'^\s*patcher_args\s*\+=\s*\("-e \\"\$\{microg_patch\}\\""\)\s*$',
        content,
        "GmsCore Support append to patcher_args",
    )
    module_guard = _require_one(
        r'^\s*elif\s+\[\s*"\$build_mode"\s*=\s*"module"\s*\]\s*;\s*then\s*$',
        content,
        "module-mode branch after GmsCore Support",
    )
    patch_call = _require_one(
        r'^\s*if\s+!\s+patch_apk\s+"\$stock_apk_to_patch"\s+"\$patched_apk"\s+'
        r'"\$\{patcher_args\[\*\]\}"\s+"\$\{args\[cli\]\}"\s+'
        r'"\$\{args\[ptjar\]\}";\s*then\s*$',
        content,
        "patch_apk() call using the assembled patcher_args",
    )

    ordered = (
        args_copy.start()
        < apk_guard.start()
        < gmscore_append.start()
        < module_guard.start()
        < patch_call.start()
    )
    if not ordered:
        raise RuntimeError(
            "Upstream utils.sh changed the GmsCore/patcher_args order. Expected "
            "patcher_args copy -> APK-mode GmsCore append -> module branch -> patch_apk() call. "
            "Refusing to sync; review the upstream change before updating the fork."
        )


def validate_fork_wrapper(content: str) -> None:
    """Protect the fork-specific ordering and four-ABI handling from accidental drift."""
    package_option = _require_one(
        r'^\s*patcher_args\+=" -OpackageNameYouTube=app\.rvx\.android\.youtube"\s*$',
        content,
        "anddea package-name option",
        source="fork/patch_apk.sh",
    )
    upstream_call = _require_one(
        r'^\s*if\s+!\s+patch_apk_upstream\s+"\$source_apk"\s+"\$patched_apk"\s+'
        r'"\$patcher_args"\s+"\$cli_jar"\s+"\$patches_jar";\s*then\s*$',
        content,
        "normal patch_apk_upstream() call from the fork wrapper",
        source="fork/patch_apk.sh",
    )
    if package_option.start() > upstream_call.start():
        raise RuntimeError(
            "fork/patch_apk.sh no longer appends the anddea package-name option before "
            "calling patch_apk_upstream(). Refusing to sync."
        )

    required_fragments = {
        "fallback to the original unstripped APK": 'source_apk="$original_apk"',
        "arm64-v8a ABI output": 'local arm64_name="${app_name}-arm64-v8a-${brand}.apk"',
        "armeabi-v7a ABI output": 'local armv7_name="${app_name}-armeabi-v7a-${brand}.apk"',
        "x86_64 ABI output": 'local x86_64_name="${app_name}-x86_64-${brand}.apk"',
        "x86 ABI output": 'local x86_name="${app_name}-x86-${brand}.apk"',
        "xxhdpi AAPT2 optimization": '"$aapt2" optimize --target-densities xxhdpi "$patched_apk" -o "$arm64_tmp"',
        "arm64 split filter": '_fork_remove_abis "$arm64_tmp" armeabi-v7a x86_64 x86',
        "armeabi-v7a split filter": '_fork_remove_abis "$armv7_tmp" arm64-v8a x86_64 x86',
        "x86_64 split filter": '_fork_remove_abis "$x86_64_tmp" arm64-v8a armeabi-v7a x86',
        "x86 split filter": '_fork_remove_abis "$x86_tmp" arm64-v8a armeabi-v7a x86_64',
        "signature verification": '"$apksigner" verify "$temp_file" >/dev/null',
    }
    for description, fragment in required_fragments.items():
        if fragment not in content:
            raise RuntimeError(
                f"fork/patch_apk.sh is missing the {description}; refusing to sync."
            )


def transform_utils(content: str) -> str:
    # Validate the public hook and argument-order invariant but do not rewrite
    # utils.sh: it must remain an exact upstream copy. build.sh aliases its
    # function at runtime instead.
    validate_upstream_patcher_order(content)
    pattern = re.compile(r"(?m)^patch_apk\s*\(\s*\)\s*\{")
    count = len(pattern.findall(content))
    if count != 1:
        raise RuntimeError(
            "Expected exactly one upstream `patch_apk()` definition in utils.sh; "
            "refusing to sync an incompatible change."
        )
    return content


def transform_build(content: str) -> str:
    # Keep upstream build.sh intact except for the fork integration directly
    # after sourcing utils.sh. This copies the original function at runtime,
    # leaving utils.sh byte-for-byte identical to upstream.
    pattern = re.compile(r"(?m)^source utils\.sh\s*$")
    matches = list(pattern.finditer(content))
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one `source utils.sh` in upstream build.sh; "
            "refusing to sync an incompatible change."
        )

    integration = r"""source utils.sh

# Preserve the upstream patcher before the fork-specific wrapper overrides it.
if ! declare -F patch_apk >/dev/null; then
    echo "ERROR: utils.sh did not define patch_apk()." >&2
    exit 1
fi
if declare -F patch_apk_upstream >/dev/null; then
    echo "ERROR: patch_apk_upstream already exists; refusing to override it." >&2
    exit 1
fi
_upstream_patch_definition="$(declare -f patch_apk)"
_upstream_patch_body="${_upstream_patch_definition#*$'\n'}"
if [[ "$_upstream_patch_body" == "$_upstream_patch_definition" ]]; then
    echo "ERROR: Could not copy the upstream patch_apk() definition." >&2
    exit 1
fi
eval "patch_apk_upstream () $_upstream_patch_body"
unset _upstream_patch_definition _upstream_patch_body
if ! declare -F patch_apk_upstream >/dev/null; then
    echo "ERROR: Could not create patch_apk_upstream()." >&2
    exit 1
fi
source fork/patch_apk.sh"""
    return pattern.sub(lambda _: integration, content, count=1)


def read_upstream_file(path: str, source_dir: Path | None, revision: str) -> str:
    if source_dir is not None:
        return (source_dir / path).read_text(encoding="utf-8")
    url = f"{RAW_URL}/{revision}/{path}"
    try:
        return fetch(url).decode("utf-8")
    except (urllib.error.URLError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Could not download upstream {path} from {url}: {exc}") from exc



def _git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}".encode("ascii") + bytes((0,))
    return hashlib.sha1(header + data).hexdigest()


def validate_upstream_sig(data: bytes, source: str = "upstream sig.txt") -> None:
    """Require exactly one well-formed official YouTube signature entry."""
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{source} is not valid UTF-8: {exc}") from exc

    entries = [
        line.strip()
        for line in content.splitlines()
        if re.search(r"\s+com\.google\.android\.youtube$", line.strip())
    ]
    if len(entries) != 1 or not re.fullmatch(
        r"[0-9a-fA-F]{64}\s+com\.google\.android\.youtube", entries[0] if entries else ""
    ):
        raise RuntimeError(
            f"Expected exactly one valid YouTube signature entry in {source}; "
            f"found {len(entries)}. Refusing to sync."
        )


def read_upstream_snapshot(
    source_dir: Path | None,
    revision: str,
    tree_sha: str | None,
    *,
    prefixes: tuple[str, ...],
    exact_paths: tuple[str, ...] = (),
) -> dict[str, tuple[bytes, str]]:
    """Read and validate selected upstream paths, preserving Git file modes."""
    files: dict[str, tuple[bytes, str]] = {}

    if source_dir is not None:
        for prefix in prefixes:
            base = source_dir / prefix.rstrip("/")
            if base.is_symlink() or not base.is_dir():
                raise RuntimeError(
                    f"Local upstream source is missing required directory: {base}"
                )
            for path in sorted(base.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError(f"Symlink in upstream snapshot is not supported: {path}")
                if not path.is_file():
                    continue
                relative = path.relative_to(source_dir).as_posix()
                if path.is_absolute() is False or relative.startswith("../"):
                    raise RuntimeError(f"Unsafe upstream path: {relative}")
                mode_bits = stat.S_IMODE(path.stat().st_mode)
                mode = "100755" if mode_bits & 0o111 else "100644"
                files[relative] = (path.read_bytes(), mode)

        for relative in exact_paths:
            path = source_dir / relative
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(
                    f"Local upstream source is missing required file: {path}"
                )
            mode_bits = stat.S_IMODE(path.stat().st_mode)
            mode = "100755" if mode_bits & 0o111 else "100644"
            files[relative] = (path.read_bytes(), mode)
    else:
        if not tree_sha:
            raise RuntimeError("Upstream commit did not provide a Git tree SHA.")
        tree_url = (
            f"https://api.github.com/repos/{UPSTREAM}/git/trees/{tree_sha}?recursive=1"
        )
        try:
            tree = json.loads(fetch(tree_url, accept="application/vnd.github+json"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read upstream Git tree: {exc}") from exc

        if tree.get("truncated"):
            raise RuntimeError(
                "Upstream Git tree response was truncated; refusing a partial snapshot."
            )

        selected_entries = []
        for entry in tree.get("tree", []):
            relative = entry.get("path", "")
            selected = relative in exact_paths or any(
                relative.startswith(prefix) for prefix in prefixes
            )
            if not selected:
                continue
            if entry.get("type") != "blob":
                continue
            mode = entry.get("mode")
            if mode not in {"100644", "100755"}:
                raise RuntimeError(
                    f"Unsupported upstream Git mode {mode!r} for {relative}; "
                    "refusing to sync."
                )
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise RuntimeError(f"Unsafe path in upstream tree: {relative}")
            selected_entries.append(entry)

        for entry in selected_entries:
            relative = entry["path"]
            url = f"{RAW_URL}/{revision}/{relative}"
            try:
                data = fetch(url, accept="application/octet-stream")
            except urllib.error.URLError as exc:
                raise RuntimeError(
                    f"Could not download upstream asset {relative}: {exc}"
                ) from exc

            if len(data) != entry.get("size"):
                raise RuntimeError(
                    f"Size mismatch for {relative}: expected {entry.get('size')}, got {len(data)}."
                )
            actual_sha = _git_blob_sha(data)
            if actual_sha != entry.get("sha"):
                raise RuntimeError(
                    f"Git blob SHA mismatch for {relative}: expected {entry.get('sha')}, got {actual_sha}."
                )
            files[relative] = (data, entry["mode"])

    for prefix in prefixes:
        if not any(path.startswith(prefix) for path in files):
            raise RuntimeError(f"Upstream snapshot has no files under {prefix}.")
    for relative in exact_paths:
        if relative not in files:
            raise RuntimeError(f"Upstream snapshot is missing required file: {relative}.")

    if "bin/" in prefixes and "bin/toml/tq-x86_64" not in files:
        raise RuntimeError("Upstream bin/ snapshot is missing bin/toml/tq-x86_64.")
    if "module/" in prefixes and not any(
        path.startswith("module/") for path in files
    ):
        raise RuntimeError("Upstream module/ snapshot is empty.")
    if "sig.txt" in exact_paths:
        validate_upstream_sig(files["sig.txt"][0])

    return files


def read_upstream_bin(
    source_dir: Path | None, revision: str, tree_sha: str | None
) -> dict[str, tuple[bytes, str]]:
    """Read the complete upstream bin/ snapshot and validate bytes/modes."""
    return read_upstream_snapshot(
        source_dir, revision, tree_sha, prefixes=("bin/",)
    )


def sync_upstream_snapshot(
    files: dict[str, tuple[bytes, str]],
    root: Path = ROOT,
    *,
    prefixes: tuple[str, ...],
    exact_paths: tuple[str, ...] = (),
) -> list[str]:
    """Mirror selected upstream directories and root files, including deletions."""
    managed = set(exact_paths)
    for prefix in prefixes:
        managed.update(
            relative for relative in files if relative.startswith(prefix)
        )
    for relative in files:
        if relative not in exact_paths and not any(
            relative.startswith(prefix) for prefix in prefixes
        ):
            raise RuntimeError(f"Refusing to sync unmanaged upstream path: {relative}")

    expected = set(files)
    changed: list[str] = []

    for prefix in prefixes:
        base = root / prefix.rstrip("/")
        if base.is_symlink():
            raise RuntimeError(f"Refusing to update symlink inside synced tree: {base}")
        if base.exists():
            for path in sorted(base.rglob("*"), reverse=True):
                if path.is_symlink():
                    raise RuntimeError(f"Refusing to update symlink inside synced tree: {path}")
                relative = path.relative_to(root).as_posix()
                if path.is_file() and relative not in expected:
                    path.unlink()
                    changed.append(f"{relative} (removed)")
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass

    for relative in exact_paths:
        destination = root / relative
        if destination.is_symlink():
            raise RuntimeError(f"Refusing to overwrite symlink: {relative}")
        if destination.is_file() and relative not in expected:
            destination.unlink()
            changed.append(f"{relative} (removed)")

    for relative, (data, mode) in sorted(files.items()):
        destination = root / relative
        if destination.is_symlink():
            raise RuntimeError(f"Refusing to overwrite symlink: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        old_data = destination.read_bytes() if destination.is_file() else None
        desired_mode = 0o755 if mode == "100755" else 0o644
        old_mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else None

        if old_data != data:
            destination.write_bytes(data)
            changed.append(relative)
        if old_mode != desired_mode:
            os.chmod(destination, desired_mode)
            if relative not in changed:
                changed.append(f"{relative} (mode updated)")

    return changed


def sync_bin_snapshot(files: dict[str, tuple[bytes, str]]) -> list[str]:
    """Backward-compatible helper for tests that only mirror bin/."""
    return sync_upstream_snapshot(files, prefixes=("bin/",))

def sync_upstream_scripts(outputs: dict[Path, str], root: Path = ROOT) -> list[str]:
    """Write synced scripts and preserve upstream executable modes.

    Both upstream entry points are executable in Git (100755). Path.write_text()
    preserves the existing local mode, so explicitly restore +x after syncing.
    """
    changed: list[str] = []
    executable_scripts = {"utils.sh", "build.sh"}

    for path, content in outputs.items():
        relative = path.relative_to(root).as_posix()
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
            changed.append(relative)

        desired_mode = 0o755 if path.name in executable_scripts else 0o644
        current_mode = stat.S_IMODE(path.stat().st_mode)
        if current_mode != desired_mode:
            os.chmod(path, desired_mode)
            if relative not in changed:
                changed.append(f"{relative} (mode updated)")

    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Use local upstream files for offline testing instead of downloading them.",
    )
    args = parser.parse_args()

    tree_sha: str | None = None
    if args.source_dir:
        revision = "local-test"
        source_dir = args.source_dir.resolve()
    else:
        try:
            commit = json.loads(fetch(API_URL, accept="application/vnd.github+json"))
            revision = commit["sha"]
            tree_sha = commit["commit"]["tree"]["sha"]
            message = commit.get("commit", {}).get("message", "").splitlines()[0]
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, IndexError) as exc:
            raise RuntimeError(f"Could not resolve upstream main commit: {exc}") from exc
        source_dir = None
        print(f"Upstream: {UPSTREAM}@{revision}")
        if message:
            print(f"Upstream commit: {message}")

    # Fetch and validate everything before writing any working-tree file.
    upstream_utils = read_upstream_file("utils.sh", source_dir, revision)
    upstream_build = read_upstream_file("build.sh", source_dir, revision)
    upstream_snapshot = read_upstream_snapshot(
        source_dir,
        revision,
        tree_sha,
        prefixes=("bin/", "module/"),
        exact_paths=("CONFIG.md", "sig.txt"),
    )
    wrapper_path = ROOT / "fork/patch_apk.sh"
    if not wrapper_path.is_file():
        raise RuntimeError("fork/patch_apk.sh is missing; refusing to sync.")
    validate_fork_wrapper(wrapper_path.read_text(encoding="utf-8"))

    utils = transform_utils(upstream_utils)
    build = transform_build(upstream_build)

    outputs = {ROOT / "utils.sh": utils, ROOT / "build.sh": build}
    changed = sync_upstream_scripts(outputs)

    changed.extend(
        sync_upstream_snapshot(
            upstream_snapshot,
            prefixes=("bin/", "module/"),
            exact_paths=("CONFIG.md", "sig.txt"),
        )
    )

    if changed:
        print("Updated: " + ", ".join(changed))
    else:
        print("No upstream changes in utils.sh, build.sh, bin/, module/, CONFIG.md, or sig.txt.")

    print("GmsCore argument ordering and fork ABI/package-name guards passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
