#!/usr/bin/env bash

# Fork-specific APK post-processing. The upstream patcher itself remains in
# utils.sh as patch_apk_upstream(), so upstream bug fixes are inherited.

_fork_remove_other_abis() {
    local apk="$1"
    local keep_abi="$2"
    local listing entry abi
    local entries=()

    if ! listing="$(unzip -Z1 "$apk")"; then
        return 1
    fi

    while IFS= read -r entry; do
        [[ "$entry" == lib/*/* ]] || continue
        abi="${entry#lib/}"
        abi="${abi%%/*}"
        if [[ "$abi" != "$keep_abi" ]]; then
            entries+=("$entry")
        fi
    done <<< "$listing"

    if ((${#entries[@]})); then
        zip -q -d "$apk" "${entries[@]}" >/dev/null || return 1
    fi
    return 0
}

_fork_list_abis() {
    local apk="$1"
    local listing
    if ! listing="$(unzip -Z1 "$apk")"; then
        return 1
    fi

    awk -F/ '$1 == "lib" && NF >= 3 && $2 != "" && $NF ~ /\.so$/ { print $2 }' <<< "$listing" |
        LC_ALL=C sort -u
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
    # Use the original downloaded APK so its available ABI libraries are retained.
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
    # Build Tools must support 16 KiB ZIP alignment for native libraries.
    local zipalign_help
    zipalign_help="$("$zipalign" -h 2>&1 || true)"
    if ! grep -Eq '(^|[[:space:]])-P([[:space:]]|$)' <<<"$zipalign_help"; then
        epr "zipalign from Android Build Tools '$build_tools_version' does not support '-P 16'; install Build Tools 35.0.0-rc3 or newer."
        return 1
    fi
    if [[ ! -f "$key_cert" || ! -f "$key_pk8" ]]; then
        epr "The fork signing key files ks.jhc.x509.pem / ks.jhc.pk8 are missing."
        return 1
    fi

    local app_name="${app_name_l:?app_name_l is not set by build_rv()}"
    local brand="${rv_brand_f:?rv_brand_f is not set by build_rv()}"
    local manifest_dir="${CWD:-$(pwd)}/.build-abi-manifests"
    local manifest="$manifest_dir/${app_name}-${brand}.abis"

    if ! mkdir -p "$manifest_dir" "$BUILD_DIR" "$TEMP_DIR"; then
        epr "Could not create output directories for '$app_name'."
        return 1
    fi

    # Remove this profile's previous dynamic outputs before starting a new attempt.
    if [[ -f "$manifest" ]]; then
        local old_abi
        while IFS= read -r old_abi; do
            if [[ "$old_abi" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
                if ! rm -f -- "$BUILD_DIR/${app_name}-${old_abi}-${brand}.apk"; then
                    epr "Could not remove previous output for ABI '$old_abi'."
                    return 1
                fi
            fi
        done < "$manifest"
    fi
    if ! rm -f -- "$manifest" \
        "$BUILD_DIR/${app_name}-arm64-v8a-${brand}.apk" \
        "$BUILD_DIR/${app_name}-armeabi-v7a-${brand}.apk" \
        "$BUILD_DIR/${app_name}-x86_64-${brand}.apk" \
        "$BUILD_DIR/${app_name}-x86-${brand}.apk"; then
        epr "Could not clear previous ABI outputs for '$app_name'."
        return 1
    fi

    local split_work_dir
    if ! split_work_dir="$(mktemp -d -p "$TEMP_DIR" "${app_name}-${brand}.split.XXXXXX")"; then
        epr "Could not create a temporary directory for '$app_name'."
        return 1
    fi
    local optimized_tmp="$split_work_dir/optimized.apk"
    local manifest_tmp="$split_work_dir/abis.manifest"

    # Optimize once, then discover the ABI directories actually present in the
    # optimized patched APK rather than assuming four architectures.
    if ! "$aapt2" optimize --target-densities xxhdpi "$patched_apk" -o "$optimized_tmp"; then
        epr "AAPT2 optimization failed for '$patched_apk'."
        rm -rf -- "$split_work_dir"
        return 1
    fi
    if ! unzip -tqq "$optimized_tmp"; then
        epr "AAPT2 produced an invalid ZIP/APK for '$app_name'."
        rm -rf -- "$split_work_dir"
        return 1
    fi

    local abi_list
    if ! abi_list="$(_fork_list_abis "$optimized_tmp")"; then
        epr "Could not inspect native library entries in '$optimized_tmp'."
        rm -rf -- "$split_work_dir"
        return 1
    fi
    if [[ -z "$abi_list" ]]; then
        epr "No native .so libraries were found in the optimized APK; refusing an empty ABI release."
        rm -rf -- "$split_work_dir"
        return 1
    fi

    local -a abis=()
    local -a names=()
    local abi
    while IFS= read -r abi; do
        [[ -n "$abi" ]] || continue
        if [[ ! "$abi" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
            epr "Unsafe or invalid ABI directory name '$abi'; refusing to create output files."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        abis+=("$abi")
        names+=("${app_name}-${abi}-${brand}.apk")
    done <<< "$abi_list"

    if ((${#abis[@]} == 0)); then
        epr "Could not determine any usable ABI from '$optimized_tmp'."
        rm -rf -- "$split_work_dir"
        return 1
    fi

    local name split_tmp temp_file actual_abis i
    for i in "${!abis[@]}"; do
        abi="${abis[$i]}"
        name="${names[$i]}"
        split_tmp="$split_work_dir/${name%.apk}.split.apk"
        temp_file="$split_work_dir/${name%.apk}.aligned.apk"

        if ! cp -f "$optimized_tmp" "$split_tmp"; then
            epr "Could not create the '$abi' split for '$app_name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if ! _fork_remove_other_abis "$split_tmp" "$abi"; then
            epr "Could not remove non-'$abi' native libraries from '$name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if ! unzip -tqq "$split_tmp"; then
            epr "The '$abi' split is not a valid ZIP/APK."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if ! actual_abis="$(_fork_list_abis "$split_tmp")"; then
            epr "Could not verify native libraries in '$name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if [[ "$actual_abis" != "$abi" ]]; then
            epr "ABI split mismatch for '$name': expected only '$abi', found '${actual_abis:-none}'."
            rm -rf -- "$split_work_dir"
            return 1
        fi

        # zipalign's -P 16 aligns uncompressed native libraries for 16 KiB pages.
        if ! "$zipalign" -P 16 -f 4 "$split_tmp" "$temp_file"; then
            epr "zipalign failed for '$name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if ! "$zipalign" -c -P 16 4 "$temp_file"; then
            epr "APK alignment verification failed for '$name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if ! "$apksigner" sign --cert "$key_cert" --key "$key_pk8" "$temp_file"; then
            epr "APK signing failed for '$name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if ! "$apksigner" verify "$temp_file" >/dev/null; then
            epr "APK signature verification failed for '$name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if ! unzip -tqq "$temp_file"; then
            epr "Signed APK ZIP integrity check failed for '$name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
        if ! "$zipalign" -c -P 16 4 "$temp_file"; then
            epr "Signed APK failed its final 16 KiB alignment check: '$name'."
            rm -rf -- "$split_work_dir"
            return 1
        fi
    done

    if [[ ! -f "$patched_apk" ]]; then
        epr "The universal patched APK disappeared before ABI outputs could be published."
        rm -rf -- "$split_work_dir"
        return 1
    fi
    if ! printf '%s\n' "${abis[@]}" > "$manifest_tmp"; then
        epr "Could not write the ABI manifest for '$app_name'."
        rm -rf -- "$split_work_dir"
        return 1
    fi

    # Publish only after every ABI APK has passed ZIP, alignment and signature checks.
    for i in "${!names[@]}"; do
        name="${names[$i]}"
        temp_file="$split_work_dir/${name%.apk}.aligned.apk"
        if ! mv -f -- "$temp_file" "$BUILD_DIR/$name"; then
            epr "Could not publish '$name'; removing this profile's partial outputs."
            local cleanup_name
            for cleanup_name in "${names[@]}"; do
                rm -f -- "$BUILD_DIR/$cleanup_name"
            done
            rm -f -- "$manifest"
            rm -rf -- "$split_work_dir"
            return 1
        fi
    done

    if ! mv -f -- "$manifest_tmp" "$manifest"; then
        epr "Could not publish the ABI manifest for '$app_name'; removing partial outputs."
        local cleanup_name
        for cleanup_name in "${names[@]}"; do
            rm -f -- "$BUILD_DIR/$cleanup_name"
        done
        rm -f -- "$manifest"
        rm -rf -- "$split_work_dir"
        return 1
    fi

    rm -rf -- "$split_work_dir"
    pr "Built ${#abis[@]} ABI-specific APK(s) for '$app_name': ${abis[*]}"
    return 0
}
