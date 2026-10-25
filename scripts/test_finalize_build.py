#!/usr/bin/env python3
"""Regression tests for dynamic ABI manifests and release gating."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finalize_build import analyze_build, recover_changelog


class FinalizeBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.build_dir = self.root / "build"
        self.manifest_dir = self.root / ".build-abi-manifests"
        self.build_dir.mkdir()
        self.manifest_dir.mkdir()
        self.config = {
            "patches-source": "example/patches",
            "rv-brand": "brand",
            "Profile One": {
                "app-name": "profile-one",
                "patches-source": "example/patches",
                "rv-brand": "brand",
                "arch": "all",
                "build-mode": "apk",
            },
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_profile(self, app_name: str, abis: list[str], brand: str = "brand") -> None:
        manifest = self.manifest_dir / f"{app_name}-{brand}.abis"
        manifest.write_text("\n".join(abis) + "\n", encoding="utf-8")
        for abi in abis:
            (self.build_dir / f"{app_name}-{abi}-{brand}.apk").write_bytes(b"test")

    def test_accepts_only_the_abis_in_the_manifest(self) -> None:
        self.add_profile("profile-one", ["arm64-v8a", "x86_64"])
        status = analyze_build(self.config, self.build_dir, self.manifest_dir)
        self.assertTrue(status["brands"]["brand"]["complete"])
        profile = status["profiles"][0]
        self.assertEqual(profile["abis"], ["arm64-v8a", "x86_64"])
        self.assertEqual(profile["expected"], [
            "profile-one-arm64-v8a-brand.apk",
            "profile-one-x86_64-brand.apk",
        ])

    def test_missing_abi_output_blocks_release(self) -> None:
        (self.manifest_dir / "profile-one-brand.abis").write_text(
            "arm64-v8a\nx86_64\n", encoding="utf-8"
        )
        (self.build_dir / "profile-one-arm64-v8a-brand.apk").write_bytes(b"test")
        status = analyze_build(self.config, self.build_dir, self.manifest_dir)
        self.assertFalse(status["profiles"][0]["complete"])
        self.assertFalse(status["brands"]["brand"]["complete"])
        self.assertEqual(status["profiles"][0]["missing"], ["profile-one-x86_64-brand.apk"])

    def test_manifest_is_required_even_when_apk_files_exist(self) -> None:
        (self.build_dir / "profile-one-arm64-v8a-brand.apk").write_bytes(b"stale")
        status = analyze_build(self.config, self.build_dir, self.manifest_dir)
        self.assertFalse(status["profiles"][0]["complete"])
        self.assertFalse(status["brands"]["brand"]["complete"])
        self.assertIn("manifest is missing", status["profiles"][0]["manifest_error"])

    def test_extra_stale_abi_output_blocks_release(self) -> None:
        self.add_profile("profile-one", ["arm64-v8a", "x86_64"])
        (self.build_dir / "profile-one-x86-brand.apk").write_bytes(b"stale")
        status = analyze_build(self.config, self.build_dir, self.manifest_dir)
        self.assertFalse(status["profiles"][0]["complete"])
        self.assertFalse(status["brands"]["brand"]["complete"])
        self.assertEqual(status["profiles"][0]["unexpected"], ["profile-one-x86-brand.apk"])

    def test_invalid_abi_manifest_blocks_release(self) -> None:
        (self.manifest_dir / "profile-one-brand.abis").write_text(
            "arm64-v8a\n../x86\n", encoding="utf-8"
        )
        (self.build_dir / "profile-one-arm64-v8a-brand.apk").write_bytes(b"test")
        status = analyze_build(self.config, self.build_dir, self.manifest_dir)
        self.assertFalse(status["profiles"][0]["complete"])
        self.assertFalse(status["brands"]["brand"]["complete"])
        self.assertIn("invalid ABI name", status["profiles"][0]["manifest_error"])

    def test_failed_profile_restores_previous_source_marker(self) -> None:
        self.add_profile("profile-one", ["arm64-v8a"])
        failed = dict(self.config["Profile One"])
        failed["app-name"] = "profile-two"
        self.config["Profile Two"] = failed
        status = analyze_build(self.config, self.build_dir, self.manifest_dir)
        current = (
            "YouTube: 21.0\n"
            "Patches: example/patches/patches-new.mpp\n"
            "[Changelog](https://example/new)\n"
        )
        previous = (
            "YouTube: 20.0\n"
            "Patches: example/patches/patches-old.mpp\n"
            "[Changelog](https://example/old)\n"
        )
        recovered = recover_changelog(current, previous, status["profiles"])
        self.assertIn("patches-old.mpp", recovered)
        self.assertNotIn("patches-new.mpp", recovered)

    def test_source_not_in_this_build_keeps_previous_marker(self) -> None:
        current = "Patches: other/source/patches-new.mpp\n"
        previous = "Patches: other/source/patches-old.mpp\n"
        recovered = recover_changelog(current, previous, [])
        self.assertIn("patches-old.mpp", recovered)
        self.assertNotIn("patches-new.mpp", recovered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
