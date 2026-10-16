#!/usr/bin/env python3
"""Regression tests for upstream integration assumptions."""

from __future__ import annotations

import os
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

    def test_local_bin_snapshot_preserves_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            upstream = Path(temp)
            parser = upstream / "bin/toml/tq-x86_64"
            parser.parent.mkdir(parents=True)
            parser.write_bytes(b"test parser")
            os.chmod(parser, 0o755)

            snapshot = read_upstream_bin(upstream, "local-test", None)

            self.assertEqual(snapshot["bin/toml/tq-x86_64"], (b"test parser", "100755"))

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
