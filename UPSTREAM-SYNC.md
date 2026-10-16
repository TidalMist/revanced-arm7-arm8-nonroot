# Upstream builder synchronization and patch checks

The `.github/workflows/ci.yml` workflow runs daily at 22:17 Moscow time (19:17 UTC) and can also be started manually from **Actions**.

Each run:
1. Resolves one current commit of `j-hc/revanced-magisk-module`.
2. Synchronizes `utils.sh` and `build.sh`, the complete `bin/` and `module/` trees, plus `CONFIG.md` and `sig.txt`. File bytes, Git blob hashes, deletions, and executable modes are checked and mirrored. The YouTube entry in upstream `sig.txt` is required; if it disappears or becomes ambiguous, synchronization stops.
3. Runs regression tests for the fork integration and snapshot mirroring, Bash syntax checks, Python compilation checks, and whitespace validation.
4. Commits changed upstream builder files and managed assets to `main`, if necessary.
5. Checks whether newer patch releases are available for the profiles in the existing `config.toml`, comparing them with `update/build.md`.
6. Starts `Build Modules` only when a configured patch release has changed, or when no prior build history exists. The build uses the exact commit prepared by that run.

The upstream `utils.sh` is kept byte-for-byte identical to upstream. The sync script checks that upstream still assembles GmsCore Support arguments before calling `patch_apk()`. The integration in `build.sh` preserves the original upstream patcher as `patch_apk_upstream()` and loads `fork/patch_apk.sh`, which applies the fork's AAPT2 optimization, four-ABI APK splitting, zipalign, and signing.

The sync process intentionally does not overwrite this repository's `README.md`, `config.toml`, signing keys, `.github/workflows/ci.yml`, `.github/workflows/build.yml`, `build-termux.sh`, or fork-specific `fork/` integration. The existing target `config.toml` is retained with all its profiles. The original `*-all.apk` intermediate is removed from release uploads after the four ABI-specific APKs have been produced.

New files outside the explicitly managed paths do not block synchronization. The managed paths are mirrored as complete snapshots so newly added dependencies inside `bin/` or `module/` are picked up automatically. If upstream changes the structure or argument order expected by the integration, synchronization fails closed so that the change can be reviewed instead of silently dropping the fork-specific APK handling.
