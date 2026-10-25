#!/usr/bin/env python3
"""Integration tests for splitting an APK using the ABI directories it contains."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "fork" / "patch_apk.sh"


class DynamicSplitterTests(unittest.TestCase):
    def setUp(self) -> None:
        for command in ("bash", "zip", "unzip"):
            if shutil.which(command) is None:
                self.skipTest(f"required command not found: {command}")

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sdk = self.root / "sdk"
        self.tools = self.sdk / "build-tools" / "35.0.0"
        self.temp_dir = self.root / "temp"
        self.build_dir = self.root / "build"
        self.source_apk = self.root / "universal.apk"

        self.tools.mkdir(parents=True)
        self.temp_dir.mkdir()
        self.build_dir.mkdir()
        (self.root / "ks.jhc.x509.pem").write_text("test certificate", encoding="utf-8")
        (self.root / "ks.jhc.pk8").write_text("test private key", encoding="utf-8")

        self._write_tool(
            "aapt2",
            r"""#!/usr/bin/env bash
set -euo pipefail
[[ "${1-}" == "optimize" ]] || exit 80
[[ "${2-}" == "--target-densities" && "${3-}" == "xxhdpi" && "${5-}" == "-o" ]] || exit 81
cp -- "$4" "$6"
""",
        )
        self._write_tool(
            "zipalign",
            r"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${1-}" == "-h" ]]; then
    echo "zipalign -P <pagesize>" >&2
    exit 0
fi
if [[ "${1-}" == "-c" ]]; then
    [[ "${2-}" == "-P" && "${3-}" == "16" && "${4-}" == "4" && -f "${5-}" ]] || exit 82
    exit 0
fi
[[ "${1-}" == "-P" && "${2-}" == "16" && "${3-}" == "-f" && "${4-}" == "4" && -f "${5-}" ]] || exit 83
cp -- "$5" "$6"
""",
        )
        self._write_tool(
            "apksigner",
            r"""#!/usr/bin/env bash
set -euo pipefail
case "${1-}" in
    sign)
        [[ "${2-}" == "--cert" && "${4-}" == "--key" && -f "${6-}" ]] || exit 84
        ;;
    verify)
        [[ -f "${2-}" ]] || exit 85
        ;;
    *)
        exit 86
        ;;
esac
""",
        )

    def tearDown(self) -> None:
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def _write_tool(self, name: str, body: str) -> None:
        path = self.tools / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _make_apk(self, abis: list[str]) -> None:
        with zipfile.ZipFile(self.source_apk, "w", compression=zipfile.ZIP_STORED) as apk:
            apk.writestr("classes.dex", b"dummy-dex")
            apk.writestr("resources.arsc", b"dummy-resources")
            apk.writestr("assets/test.txt", b"kept")
            for abi in abis:
                apk.writestr(f"lib/{abi}/libsample.so", f"native-{abi}".encode())

    def _run_split(self, abis: list[str]) -> subprocess.CompletedProcess[str]:
        self._make_apk(abis)
        env = os.environ.copy()
        env.update(
            {
                "ANDROID_HOME": str(self.sdk),
                "CWD": str(self.root),
                "TEMP_DIR": str(self.temp_dir),
                "BUILD_DIR": str(self.build_dir),
                "FAKE_UNIVERSAL_APK": str(self.source_apk),
            }
        )
        script = StringTemplate.SHELL.replace("@@WRAPPER@@", str(WRAPPER))
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )

    def _assert_outputs_match_abis(self, abis: list[str]) -> None:
        outputs = sorted(self.build_dir.glob("*.apk"))
        self.assertEqual(
            sorted(path.name for path in outputs),
            sorted(f"sample-{abi}-morphe.apk" for abi in abis),
        )
        for output in outputs:
            with zipfile.ZipFile(output) as apk:
                self.assertIn("classes.dex", apk.namelist())
                actual_abis = sorted({
                    item.split("/")[1]
                    for item in apk.namelist()
                    if item.startswith("lib/") and item.endswith(".so")
                })
            self.assertEqual(
                actual_abis,
                [output.name.removeprefix("sample-").removesuffix("-morphe.apk")],
            )

        manifest = self.root / ".build-abi-manifests" / "sample-morphe.abis"
        self.assertTrue(manifest.is_file())
        self.assertEqual(manifest.read_text(encoding="utf-8").splitlines(), sorted(abis))

    def test_one_detected_abi_creates_one_apk(self) -> None:
        result = self._run_split(["arm64-v8a"])
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self._assert_outputs_match_abis(["arm64-v8a"])

    def test_two_detected_abis_create_two_apks(self) -> None:
        result = self._run_split(["x86_64", "arm64-v8a"])
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self._assert_outputs_match_abis(["arm64-v8a", "x86_64"])

    def test_no_native_libraries_fails_without_publishing_apks(self) -> None:
        result = self._run_split([])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No native .so libraries were found", result.stderr)
        self.assertEqual(list(self.build_dir.glob("*.apk")), [])
        self.assertFalse((self.root / ".build-abi-manifests" / "sample-morphe.abis").exists())


class StringTemplate:
    SHELL = r"""source "@@WRAPPER@@"
build_mode=apk
arch=all
app_name_l=sample
rv_brand_f=morphe
pr() { :; }
epr() { printf '%s\n' "$*" >&2; }
patch_apk_upstream() {
    cp -- "$FAKE_UNIVERSAL_APK" "$2"
}
patch_apk "$FAKE_UNIVERSAL_APK" "$TEMP_DIR/patched-all.apk" "" "$CWD/cli.jar" "$CWD/patches.mpp"
"""


if __name__ == "__main__":
    unittest.main(verbosity=2)
