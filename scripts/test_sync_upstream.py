#!/usr/bin/env python3
"""Regression tests for upstream integration assumptions."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from sync_upstream import (
    ROOT,
    transform_build,
    transform_utils,
    validate_fork_wrapper,
    validate_upstream_patcher_order,
    read_upstream_bin,
    read_upstream_snapshot,
    sync_upstream_snapshot,
    validate_upstream_sig,
    sync_upstream_scripts,
)


class SyncUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream_utils = (ROOT / "utils.sh").read_text(encoding="utf-8")

    def test_current_upstream_gmscore_order_is_accepted(self) -> None:
        validate_upstream_patcher_order(self.upstream_utils)

    def test_missing_gmscore_append_is_rejected(self) -> None:
        marker = 'patcher_args+=("-e \\"${microg_patch}\\"")'
        self.assertEqual(self.upstream_utils.count(marker), 1)
        broken = self.upstream_utils.replace(marker, "", 1)
        with self.assertRaisesRegex(RuntimeError, "GmsCore Support append"):
            validate_upstream_patcher_order(broken)

    def test_gmscore_after_patch_call_is_rejected(self) -> None:
        marker = 'patcher_args+=("-e \\"${microg_patch}\\"")'
        self.assertEqual(self.upstream_utils.count(marker), 1)
        broken = self.upstream_utils.replace(marker, "", 1) + "\n" + marker + "\n"
        with self.assertRaisesRegex(RuntimeError, "GmsCore/patcher_args order"):
            validate_upstream_patcher_order(broken)

    def test_transform_utils_does_not_edit_upstream_content(self) -> None:
        self.assertEqual(transform_utils(self.upstream_utils), self.upstream_utils)

    def test_transform_build_injects_wrapper_once(self) -> None:
        upstream_build = "#!/usr/bin/env bash\nset -euo pipefail\nsource utils.sh\ntrap 'exit' INT\n"
        result = transform_build(upstream_build)
        self.assertIn("source fork/patch_apk.sh", result)
        self.assertEqual(result.count("source fork/patch_apk.sh"), 1)
        self.assertIn("eval \"patch_apk_upstream () $_upstream_patch_body\"", result)

    def test_fork_wrapper_keeps_package_option_before_upstream_call(self) -> None:
        wrapper = (ROOT / "fork/patch_apk.sh").read_text(encoding="utf-8")
        validate_fork_wrapper(wrapper)


    def test_upstream_sig_requires_youtube_entry(self) -> None:
        valid = (
            "a" * 64 + " com.google.android.youtube\n"
            + "b" * 64 + " com.google.android.apps.youtube.music\n"
        ).encode()
        validate_upstream_sig(valid)

        with self.assertRaisesRegex(RuntimeError, "YouTube signature entry"):
            validate_upstream_sig(("b" * 64 + " com.google.android.apps.youtube.music\n").encode())

        duplicate = valid + ("c" * 64 + " com.google.android.youtube\n").encode()
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            validate_upstream_sig(duplicate)

    def test_full_snapshot_reads_module_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            upstream = Path(temp) / "upstream"
            (upstream / "bin/toml").mkdir(parents=True)
            (upstream / "module/META-INF/com/google/android").mkdir(parents=True)
            (upstream / "bin/toml/tq-x86_64").write_bytes(b"parser")
            (upstream / "module/META-INF/com/google/android/update-binary").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )
            (upstream / "CONFIG.md").write_text("config docs\n", encoding="utf-8")
            (upstream / "sig.txt").write_text(
                "a" * 64 + " com.google.android.youtube\n", encoding="utf-8"
            )
            os.chmod(upstream / "module/META-INF/com/google/android/update-binary", 0o755)

            snapshot = read_upstream_snapshot(
                upstream,
                "local-test",
                None,
                prefixes=("bin/", "module/"),
                exact_paths=("CONFIG.md", "sig.txt"),
            )

            self.assertEqual(snapshot["bin/toml/tq-x86_64"], (b"parser", "100644"))
            self.assertEqual(
                snapshot["module/META-INF/com/google/android/update-binary"][1],
                "100755",
            )
            self.assertIn("CONFIG.md", snapshot)
            self.assertIn("sig.txt", snapshot)

    def test_snapshot_mirrors_managed_paths_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "target"
            upstream = Path(temp) / "upstream"
            (root / "bin/old").mkdir(parents=True)
            (root / "module/old").mkdir(parents=True)
            (root / ".github/workflows").mkdir(parents=True)
            (root / "bin/old/obsolete").write_text("old", encoding="utf-8")
            (root / "module/old/obsolete").write_text("old", encoding="utf-8")
            (root / "CONFIG.md").write_text("old docs", encoding="utf-8")
            (root / "sig.txt").write_text("old sig", encoding="utf-8")
            (root / "README.md").write_text("fork readme", encoding="utf-8")
            (root / "config.toml").write_text("fork config", encoding="utf-8")
            (root / ".github/workflows/ci.yml").write_text("fork workflow", encoding="utf-8")

            (upstream / "bin/toml").mkdir(parents=True)
            (upstream / "module/META-INF").mkdir(parents=True)
            (upstream / "bin/toml/tq-x86_64").write_bytes(b"new parser")
            (upstream / "module/META-INF/update-binary").write_text("new module", encoding="utf-8")
            (upstream / "CONFIG.md").write_text("new docs", encoding="utf-8")
            (upstream / "sig.txt").write_text(
                "a" * 64 + " com.google.android.youtube\n", encoding="utf-8"
            )

            snapshot = read_upstream_snapshot(
                upstream,
                "local-test",
                None,
                prefixes=("bin/", "module/"),
                exact_paths=("CONFIG.md", "sig.txt"),
            )
            changed = sync_upstream_snapshot(
                snapshot,
                root=root,
                prefixes=("bin/", "module/"),
                exact_paths=("CONFIG.md", "sig.txt"),
            )

            self.assertFalse((root / "bin/old/obsolete").exists())
            self.assertFalse((root / "module/old/obsolete").exists())
            self.assertEqual((root / "bin/toml/tq-x86_64").read_bytes(), b"new parser")
            self.assertEqual((root / "module/META-INF/update-binary").read_text(), "new module")
            self.assertEqual((root / "CONFIG.md").read_text(), "new docs")
            self.assertIn(" com.google.android.youtube", (root / "sig.txt").read_text())
            self.assertEqual((root / "README.md").read_text(), "fork readme")
            self.assertEqual((root / "config.toml").read_text(), "fork config")
            self.assertEqual((root / ".github/workflows/ci.yml").read_text(), "fork workflow")
            self.assertTrue(any("obsolete (removed)" in item for item in changed))

    def test_local_bin_snapshot_preserves_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            upstream = Path(temp)
            parser = upstream / "bin/toml/tq-x86_64"
            parser.parent.mkdir(parents=True)
            parser.write_bytes(b"test parser")
            os.chmod(parser, 0o755)

            snapshot = read_upstream_bin(upstream, "local-test", None)

            self.assertEqual(snapshot["bin/toml/tq-x86_64"], (b"test parser", "100755"))

    def test_upstream_scripts_are_executable_after_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            utils = root / "utils.sh"
            build = root / "build.sh"
            utils.write_text("old utils", encoding="utf-8")
            build.write_text("old build", encoding="utf-8")
            os.chmod(utils, 0o644)
            os.chmod(build, 0o755)

            changed = sync_upstream_scripts(
                {utils: "new utils", build: "new build"},
                root=root,
            )

            self.assertEqual(stat.S_IMODE(utils.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(build.stat().st_mode), 0o755)
            self.assertEqual(utils.read_text(encoding="utf-8"), "new utils")
            self.assertIn("utils.sh", changed)

            self.assertEqual(
                sync_upstream_scripts({utils: "new utils", build: "new build"}, root=root),
                [],
            )

    def test_fork_wrapper_rejects_missing_x86_split(self) -> None:
        wrapper = (ROOT / "fork/patch_apk.sh").read_text(encoding="utf-8")
        broken = wrapper.replace(
            '_fork_remove_abis "$x86_tmp" arm64-v8a armeabi-v7a x86_64',
            "",
            1,
        )
        with self.assertRaisesRegex(RuntimeError, "x86 split filter"):
            validate_fork_wrapper(broken)

    def test_fork_wrapper_rejects_package_option_after_upstream_call(self) -> None:
        wrapper_path = ROOT / "fork/patch_apk.sh"
        wrapper = wrapper_path.read_text(encoding="utf-8")
        option = '        patcher_args+=" -OpackageNameYouTube=app.rvx.android.youtube"\n'
        call = '    if ! patch_apk_upstream "$source_apk" "$patched_apk" "$patcher_args" "$cli_jar" "$patches_jar"; then\n'
        self.assertIn(option, wrapper)
        self.assertIn(call, wrapper)
        broken = wrapper.replace(option, "", 1)
        broken = broken.replace(call, call + option, 1)
        with self.assertRaisesRegex(RuntimeError, "before calling patch_apk_upstream"):
            validate_fork_wrapper(broken)


if __name__ == "__main__":
    unittest.main(verbosity=2)
