#!/usr/bin/env bash

# Fork-specific APK post-processing. The upstream patcher itself remains in
# utils.sh as patch_apk_upstream(), so upstream bug fixes are inherited.

_fork_remove_abis() {
    local apk="$1"
    shift
    local entry abi target
    local entries=()

    while IFS= read -r entry; do
        [[ "$entry" == lib/*/* ]] || continue
        abi="${entry#lib/}"
        abi="${abi%%/*}"
        for target in "$@"; do
            if [[ "$abi" == "$target" ]]; then
                entries+=("$entry")
                break
            fi
        done
    done < <(unzip -Z1 "$apk")

    if ((${#entries[@]})); then
        zip -q -d "$apk" "${entries[@]}" >/dev/null
    fi
}

patch_apk() {
    local stock_input="$1"
    local patched_apk="$2"
    local patcher_args="$3"
    local cli_jar="$4"
    local patches_jar="$5"

    # Only split universal non-root APK builds. Other upstream build modes
    # and explicitly selected architectures keep their normal behavior.
    if [[ "${build_mode:-apk}" != apk || "${arch:-all}" != all ]]; then
        patch_apk_upstream "$stock_input" "$patched_apk" "$patcher_args" "$cli_jar" "$patches_jar"
        return $?
    fi

    # build_rv() strips x86 libraries from the temporary input for arch=all.
    # Use the original downloaded APK instead so all four ABIs remain available.
    local source_apk="$stock_input"
    if [[ "$stock_input" == *.stripped.apk ]]; then
        local original_apk="${stock_input%.stripped.apk}"
        if [[ -f "$original_apk" ]]; then
            source_apk="$original_apk"
        fi
    fi

    # The anddea patch set uses a different YouTube package name.
    if [[ "${rv_brand_f:-}" == rvx-anddea ]]; then
        patcher_args+=" -OpackageNameYouTube=app.rvx.android.youtube"
    fi

    if ! patch_apk_upstream "$source_apk" "$patched_apk" "$patcher_args" "$cli_jar" "$patches_jar"; then
        return 1
    fi

    local sdk_root="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
    if [[ -z "$sdk_root" || ! -d "$sdk_root/build-tools" ]]; then
        epr "Android SDK build-tools not found (ANDROID_HOME/ANDROID_SDK_ROOT is missing)."
        return 1
    fi

    local build_tools_version
    build_tools_version="$(find "$sdk_root/build-tools" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -V | tail -n 1)"
    if [[ -z "$build_tools_version" ]]; then
        epr "No Android SDK build-tools version was found in '$sdk_root/build-tools'."
        return 1
    fi

    local build_tools="$sdk_root/build-tools/$build_tools_version"
    local aapt2="$build_tools/aapt2"
    local zipalign="$build_tools/zipalign"
    local apksigner="$build_tools/apksigner"
    local key_cert="${CWD:-$(pwd)}/ks.jhc.x509.pem"
    local key_pk8="${CWD:-$(pwd)}/ks.jhc.pk8"

    local tool
    for tool in "$aapt2" "$zipalign" "$apksigner"; do
        if [[ ! -x "$tool" ]]; then
            epr "Required Android build tool is missing or not executable: '$tool'."
            return 1
        fi
    done
    if [[ ! -f "$key_cert" || ! -f "$key_pk8" ]]; then
        epr "The fork signing key files ks.jhc.x509.pem / ks.jhc.pk8 are missing."
        return 1
    fi

    mkdir -p "$TEMP_DIR" "$BUILD_DIR"

    local app_name="${app_name_l:?app_name_l is not set by build_rv()}"
    local brand="${rv_brand_f:?rv_brand_f is not set by build_rv()}"
    local arm64_name="${app_name}-arm64-v8a-${brand}.apk"
    local armv7_name="${app_name}-armeabi-v7a-${brand}.apk"
    local x86_64_name="${app_name}-x86_64-${brand}.apk"
    local x86_name="${app_name}-x86-${brand}.apk"

    local arm64_tmp="$TEMP_DIR/$arm64_name"
    local armv7_tmp="$TEMP_DIR/$armv7_name"
    local x86_64_tmp="$TEMP_DIR/$x86_64_name"
    local x86_tmp="$TEMP_DIR/$x86_name"

    rm -f "$arm64_tmp" "$armv7_tmp" "$x86_64_tmp" "$x86_tmp"

    # AAPT2 optimization is applied once, then the optimized universal APK is
    # copied and native libraries for the other ABIs are removed.
    if ! "$aapt2" optimize --target-densities xxhdpi "$patched_apk" -o "$arm64_tmp"; then
        epr "AAPT2 optimization failed for '$patched_apk'."
        rm -f "$arm64_tmp"
        return 1
    fi
    cp -f "$arm64_tmp" "$armv7_tmp"
    cp -f "$arm64_tmp" "$x86_64_tmp"
    cp -f "$arm64_tmp" "$x86_tmp"

    if ! _fork_remove_abis "$arm64_tmp" armeabi-v7a x86_64 x86 ||
       ! _fork_remove_abis "$armv7_tmp" arm64-v8a x86_64 x86 ||
       ! _fork_remove_abis "$x86_64_tmp" arm64-v8a armeabi-v7a x86 ||
       ! _fork_remove_abis "$x86_tmp" arm64-v8a armeabi-v7a x86_64; then
        epr "Could not split native libraries for '$app_name'."
        rm -f "$arm64_tmp" "$armv7_tmp" "$x86_64_tmp" "$x86_tmp"
        return 1
    fi

    local name temp_file output_file
    local names=("$arm64_name" "$armv7_name" "$x86_64_name" "$x86_name")
    local sources=("$arm64_tmp" "$armv7_tmp" "$x86_64_tmp" "$x86_tmp")
    local i
    for i in "${!names[@]}"; do
        name="${names[$i]}"
        temp_file="$TEMP_DIR/${name%.apk}.aligned.apk"
        output_file="$BUILD_DIR/$name"

        if ! "$zipalign" -pf 4 "${sources[$i]}" "$temp_file"; then
            epr "zipalign failed for '$name'."
            rm -f "$temp_file"
            return 1
        fi
        if ! "$apksigner" sign --cert "$key_cert" --key "$key_pk8" "$temp_file"; then
            epr "APK signing failed for '$name'."
            rm -f "$temp_file"
            return 1
        fi
        if ! "$apksigner" verify "$temp_file" >/dev/null; then
            epr "APK signature verification failed for '$name'."
            rm -f "$temp_file"
            return 1
        fi
        mv -f "$temp_file" "$output_file"
        pr "Built optimized APK: '$output_file'"
    done

    rm -f "$arm64_tmp" "$armv7_tmp" "$x86_64_tmp" "$x86_tmp"
    # Keep patched_apk intact: upstream build_rv() moves it to its normal
    # -all.apk intermediate, which the workflow removes before publishing.
    [[ -f "$patched_apk" ]]
}
