#!/usr/bin/env python3
"""Regression tests for the independent pre-release APK validation gate."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from validate_built_apks import ValidationError, get_package_expectation, validate_build

CERT_SHA = "aa" * 32


class ValidateBuiltApksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.build = self.root / "build"
        self.manifests = self.root / ".build-abi-manifests"
        self.tools = self.root / "build-tools"
        self.build.mkdir()
        self.manifests.mkdir()
        self.tools.mkdir()
        for name in ("aapt2", "zipalign", "apksigner"):
            tool = self.tools / name
            tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(0o755)
        self.cert = self.root / "ks.jhc.x509.pem"
        self.cert.write_text("test certificate placeholder", encoding="utf-8")
        self.config_path = self.root / "config.toml"
        self.status_path = self.root / "build-status.json"
        self.current_log = self.root / "build.md"
        self.previous_log = self.root / "build.previous.md"
        self.current_log.write_text(
            "Darker-MaterialYou-morphe: 21.20.405  \n\nPatches: MorpheApp/morphe-patches/patches-new.mpp\n",
            encoding="utf-8",
        )
        self.previous_log.write_text(
            "YouTube: 21.20.400\n\nPatches: MorpheApp/morphe-patches/patches-old.mpp\n",
            encoding="utf-8",
        )
        self.package_by_file: dict[str, str] = {}
        self.version_by_file: dict[str, str] = {}
        self.signer_rc = 0
        self.signer_digest = CERT_SHA
        self.signer_output_format = "legacy"
        self.zipalign_rc = 0
        self._setup_single_profile(["arm64-v8a", "x86_64"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _setup_single_profile(self, abis: list[str]) -> None:
        self.config_path.write_text(
            'enable-module-update = false\n\n'
            '[Darker-MaterialYou-morphe]\n'
            'app-name = "d-arker-materiiiaalyou"\n'
            'patches-source = "MorpheApp/morphe-patches"\n'
            'build-mode = "apk"\n'
            'rv-brand = "morphe"\n',
            encoding="utf-8",
        )
        app, brand, table = "d-arker-materiiiaalyou", "morphe", "Darker-MaterialYou-morphe"
        (self.manifests / f"{app}-{brand}.abis").write_text("\n".join(abis) + "\n", encoding="utf-8")
        expected = []
        for abi in abis:
            name = f"{app}-{abi}-{brand}.apk"
            expected.append(name)
            self._write_apk(self.build / name, [abi])
            self.package_by_file[name] = "app.morphe.android.youtube"
        self.status_path.write_text(json.dumps({
            "brands": {brand: {"complete": True, "expected": len(expected), "actual": len(expected)}},
            "profiles": [{
                "table": table, "app_name": app, "source": "MorpheApp/morphe-patches",
                "brand": brand, "abis": abis, "expected": expected, "complete": True,
                "manifest_error": "", "missing": [], "unexpected": [],
            }],
        }), encoding="utf-8")

    def _setup_brand_profile(
        self,
        table: str,
        app: str,
        brand: str,
        source: str,
        package: str,
        *,
        as_json: bool = False,
        abis: list[str] | None = None,
    ) -> None:
        abis = abis or ["arm64-v8a", "x86_64"]
        for path in self.build.glob("*.apk"):
            path.unlink()
        for path in self.manifests.glob("*.abis"):
            path.unlink()

        if as_json:
            self.config_path = self.root / "config.json"
            config = {
                "enable-module-update": False,
                table: {
                    "app-name": app,
                    "patches-source": source,
                    "build-mode": "apk",
                    "rv-brand": brand,
                },
            }
            self.config_path.write_text(json.dumps(config), encoding="utf-8")
        else:
            self.config_path = self.root / "config.toml"
            self.config_path.write_text(
                'enable-module-update = false\n\n'
                f'[{table}]\n'
                f'app-name = "{app}"\n'
                f'patches-source = "{source}"\n'
                'build-mode = "apk"\n'
                f'rv-brand = "{brand}"\n',
                encoding="utf-8",
            )

        extension = "rvp" if brand in {"rv", "rvx-a6-7"} else "mpp"
        self.current_log.write_text(
            f"{table}: 21.20.405  \n\nPatches: {source}/patches-new.{extension}\n",
            encoding="utf-8",
        )
        self.previous_log.write_text(
            f"Patches: {source}/patches-old.{extension}\n",
            encoding="utf-8",
        )

        manifest = self.manifests / f"{app}-{brand}.abis"
        manifest.write_text("\n".join(abis) + "\n", encoding="utf-8")
        expected = []
        self.package_by_file.clear()
        self.version_by_file.clear()
        for abi in abis:
            name = f"{app}-{abi}-{brand}.apk"
            expected.append(name)
            self._write_apk(self.build / name, [abi])
            self.package_by_file[name] = package

        self.status_path.write_text(json.dumps({
            "brands": {brand: {"complete": True, "expected": len(expected), "actual": len(expected)}},
            "profiles": [{
                "table": table, "app_name": app, "source": source,
                "brand": brand, "abis": abis, "expected": expected, "complete": True,
                "manifest_error": "", "missing": [], "unexpected": [],
            }],
        }), encoding="utf-8")

    @staticmethod
    def _write_apk(path: Path, abis: list[str]) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as apk:
            apk.writestr("AndroidManifest.xml", b"fake binary manifest")
            apk.writestr("classes.dex", b"fake dex")
            apk.writestr("resources.arsc", b"fake resources")
            for abi in abis:
                apk.writestr(f"lib/{abi}/libsample.so", f"native-{abi}".encode())

    def _mock_run(self, command, **kwargs):
        cmd = [str(value) for value in command]
        if cmd[0] == "openssl":
            return subprocess.CompletedProcess(cmd, 0, stdout="sha256 Fingerprint=" + ":".join(["AA"] * 32), stderr="")
        if cmd[0].endswith("aapt2"):
            path = Path(cmd[-1])
            package = self.package_by_file.get(path.name, "app.morphe.android.youtube")
            version = self.version_by_file.get(path.name, "21.20.405")
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=f"package: name='{package}' versionCode='214200405' versionName='{version}'\n",
                stderr="",
            )
        if cmd[0].endswith("apksigner"):
            if self.signer_rc:
                return subprocess.CompletedProcess(cmd, self.signer_rc, stdout="ERROR: bad signature", stderr="")
            if self.signer_output_format == "build-tools-37":
                signer_line = f"V2 Signer: certificate SHA-256 digest: {self.signer_digest}"
            elif self.signer_output_format == "build-tools-37-v3":
                signer_line = f"V3.0 Signer: certificate SHA-256 digest: {self.signer_digest}"
            else:
                signer_line = f"Signer #1 certificate SHA-256 digest: {self.signer_digest}"
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"Verifies\n{signer_line}\n", stderr=""
            )
        if cmd[0].endswith("zipalign"):
            output = "alignment check failed" if self.zipalign_rc else ""
            return subprocess.CompletedProcess(cmd, self.zipalign_rc, stdout=output, stderr="")
        self.fail(f"unexpected command: {cmd}")

    def _validate(self):
        with patch("validate_built_apks.subprocess.run", side_effect=self._mock_run):
            return validate_build(
                self.config_path, self.build, self.manifests, self.status_path,
                self.previous_log, self.current_log, self.tools, self.cert,
            )

    def test_accepts_two_abi_apks_with_expected_package_signature_and_alignment(self) -> None:
        status = self._validate()
        self.assertTrue(status["brands"]["morphe"]["complete"])
        self.assertTrue(status["profiles"][0]["validated"])

    def test_public_revanced_profile_uses_its_real_package_id(self) -> None:
        self._setup_brand_profile(
            "Darker-MaterialYou-rv",
            "test-revanced",
            "rv",
            "ReVanced/revanced-patches",
            "app.revanced.android.youtube",
        )
        status = self._validate()
        self.assertTrue(status["brands"]["rv"]["complete"])
        self.assertTrue(status["profiles"][0]["validated"])

    def test_rvx_a6_7_package_id_is_configured_for_future_enablement(self) -> None:
        self.assertEqual(
            get_package_expectation({}, "rvx-a6-7"),
            "app.rvx.android.youtube",
        )

    def test_disabled_rvx_a6_7_profile_does_not_block_other_brands(self) -> None:
        config = self.config_path.read_text(encoding="utf-8")
        self.config_path.write_text(
            config
            + '\n[BlackA6-7]\n'
            + 'enabled = false\n'
            + 'app-name = "bllack"\n'
            + 'build-mode = "apk"\n'
            + 'rv-brand = "rvx-a6-7"\n',
            encoding="utf-8",
        )
        status = self._validate()
        self.assertTrue(status["brands"]["morphe"]["complete"])
        self.assertNotIn("rvx-a6-7", status["brands"])

    def test_partial_update_config_ignores_profiles_not_selected_for_this_run(self) -> None:
        table = "Darker-MaterialYou-rvx-anddea"
        source = "anddea/revanced-patches"
        self._setup_brand_profile(
            table,
            "test-anddea",
            "rvx-anddea",
            source,
            "app.rvx.android.youtube",
            as_json=True,
        )
        self.previous_log.write_text(
            "Patches: ReVanced/revanced-patches/patches-unchanged.rvp\n",
            encoding="utf-8",
        )

        status = self._validate()
        self.assertTrue(status["brands"]["rvx-anddea"]["complete"])
        self.assertNotIn("rv", status["brands"])
        self.assertIn(
            "ReVanced/revanced-patches/patches-unchanged.rvp",
            self.current_log.read_text(encoding="utf-8"),
        )

    def test_accepts_android_build_tools_37_signature_digest_label(self) -> None:
        self.signer_output_format = "build-tools-37"
        status = self._validate()
        self.assertTrue(status["brands"]["morphe"]["complete"])

    def test_accepts_v3_android_build_tools_37_signature_digest_label(self) -> None:
        self.signer_output_format = "build-tools-37-v3"
        status = self._validate()
        self.assertTrue(status["brands"]["morphe"]["complete"])

    def test_wrong_package_blocks_release_group(self) -> None:
        self.package_by_file["d-arker-materiiiaalyou-x86_64-morphe.apk"] = "com.example.wrong"
        with self.assertRaises(ValidationError):
            self._validate()
        status = json.loads(self.status_path.read_text(encoding="utf-8"))
        self.assertFalse(status["brands"]["morphe"]["complete"])
        self.assertIn("expected package", status["profiles"][0]["validation_error"])

    def test_invalid_signature_blocks_release_group(self) -> None:
        self.signer_rc = 1
        with self.assertRaises(ValidationError):
            self._validate()
        self.assertFalse(json.loads(self.status_path.read_text(encoding="utf-8"))["brands"]["morphe"]["complete"])

    def test_signature_from_wrong_certificate_blocks_release_group(self) -> None:
        self.signer_digest = "bb" * 32
        with self.assertRaises(ValidationError):
            self._validate()
        status = json.loads(self.status_path.read_text(encoding="utf-8"))
        self.assertFalse(status["brands"]["morphe"]["complete"])
        self.assertIn("expected signing certificate", status["profiles"][0]["validation_error"])

    def test_bad_16k_alignment_blocks_release_group(self) -> None:
        self.zipalign_rc = 1
        with self.assertRaises(ValidationError):
            self._validate()
        self.assertFalse(json.loads(self.status_path.read_text(encoding="utf-8"))["brands"]["morphe"]["complete"])

    def test_extra_abi_inside_one_split_blocks_release_group(self) -> None:
        self._write_apk(self.build / "d-arker-materiiiaalyou-arm64-v8a-morphe.apk", ["arm64-v8a", "x86_64"])
        with self.assertRaises(ValidationError):
            self._validate()
        status = json.loads(self.status_path.read_text(encoding="utf-8"))
        self.assertFalse(status["brands"]["morphe"]["complete"])
        self.assertIn("expected only ABI", status["profiles"][0]["validation_error"])

    def test_app_version_mismatch_blocks_release_group(self) -> None:
        self.version_by_file["d-arker-materiiiaalyou-x86_64-morphe.apk"] = "21.20.404"
        with self.assertRaises(ValidationError):
            self._validate()
        status = json.loads(self.status_path.read_text(encoding="utf-8"))
        self.assertFalse(status["brands"]["morphe"]["complete"])
        self.assertIn("does not match build.md version", status["profiles"][0]["validation_error"])

    def test_partial_build_can_publish_only_the_valid_brand(self) -> None:
        app, brand, table, abi = "test-anddea", "rvx-anddea", "Darker-MaterialYou-rvx-anddea", "arm64-v8a"
        filename = f"{app}-{abi}-{brand}.apk"
        self._write_apk(self.build / filename, [abi])
        self.package_by_file[filename] = "app.rvx.android.youtube"
        (self.manifests / f"{app}-{brand}.abis").write_text(abi + "\n", encoding="utf-8")
        self.config_path.write_text(
            'enable-module-update = false\n\n'
            '[Darker-MaterialYou-morphe]\napp-name = "d-arker-materiiiaalyou"\n'
            'patches-source = "MorpheApp/morphe-patches"\nbuild-mode = "apk"\nrv-brand = "morphe"\n\n'
            '[Darker-MaterialYou-rvx-anddea]\napp-name = "test-anddea"\n'
            'patches-source = "anddea/revanced-patches"\nbuild-mode = "apk"\nrv-brand = "rvx-anddea"\n',
            encoding="utf-8",
        )
        self.current_log.write_text(
            "Darker-MaterialYou-morphe: 21.20.405  \n"
            "Darker-MaterialYou-rvx-anddea: 21.20.405  \n\n"
            "Patches: MorpheApp/morphe-patches/patches-new.mpp\n"
            "Patches: anddea/revanced-patches/patches-new.mpp\n",
            encoding="utf-8",
        )
        status = json.loads(self.status_path.read_text(encoding="utf-8"))
        status["brands"][brand] = {"complete": True, "expected": 1, "actual": 1}
        status["profiles"].append({
            "table": table, "app_name": app, "source": "anddea/revanced-patches",
            "brand": brand, "abis": [abi], "expected": [filename], "complete": True,
            "manifest_error": "", "missing": [], "unexpected": [],
        })
        self.status_path.write_text(json.dumps(status), encoding="utf-8")
        self.package_by_file["d-arker-materiiiaalyou-x86_64-morphe.apk"] = "com.example.wrong"

        result = self._validate()
        self.assertFalse(result["brands"]["morphe"]["complete"])
        self.assertTrue(result["brands"]["rvx-anddea"]["complete"])

    def test_corrupt_zip_blocks_release_group(self) -> None:
        (self.build / "d-arker-materiiiaalyou-x86_64-morphe.apk").write_bytes(b"not an apk")
        with self.assertRaises(ValidationError):
            self._validate()
        self.assertFalse(json.loads(self.status_path.read_text(encoding="utf-8"))["brands"]["morphe"]["complete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
