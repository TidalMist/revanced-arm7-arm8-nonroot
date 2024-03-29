> [!IMPORTANT]
> For Huawei with latest [ReVanced MicroG](https://github.com/ReVanced/GmsCore/releases/latest), turn on register device, select profile Emulator arm64-v8a, then log in
<!-- > Latest [ReVanced MicroG](https://github.com/ReVanced/GmsCore/releases/latest) and patches requires any version of original YouTube installed even if there are no google services on device, otherwise yt revanced will crash at startup.
>
> Recover yt somehow, if you delete it with adb
> 
> yt can be disabled if it installed as system app
>
> For Huawei, turn on register device, select profile Emulator arm64-v8a, then log in
-->
> [!NOTE]
> package name or something can changed. Check that repo if obtainium errors installing or cant find apk release. May need reinstall or re-add yt. Save settings before it
### Get updates
>(yt min A8, obtainium A7)
>Notifications, **A12+ background auto update**, shizuku, ~root~. +f-droid and other app sources

**These import links work on devices with [Obtainium](https://github.com/ImranR98/Obtainium) installed**<div align="center"><h1>

[<ins>`Black`](https://apps.obtainium.imranr.dev/redirect.html?r=obtainium://app/%7B%22id%22%3A%22app.rvx.android.youtube%22%2C%22url%22%3A%22https%3A%2F%2Fgithub.com%2FTidalMist%2Frevanced-arm7-arm8-nonroot%22%2C%22author%22%3A%22TidalMist%22%2C%22name%22%3A%22YouTube%22%2C%22additionalSettings%22%3A%22%7B%5C%22versionDetection%5C%22%3Afalse%2C%5C%22apkFilterRegEx%5C%22%3A%5C%22black%5C%22%7D%22%7D) &nbsp; [<ins>`Dark`](https://apps.obtainium.imranr.dev/redirect.html?r=obtainium://app/%7B%22id%22%3A%22app.rvx.android.youtube%22%2C%22url%22%3A%22https%3A%2F%2Fgithub.com%2FTidalMist%2Frevanced-arm7-arm8-nonroot%22%2C%22author%22%3A%22TidalMist%22%2C%22name%22%3A%22YouTube%22%2C%22additionalSettings%22%3A%22%7B%5C%22versionDetection%5C%22%3Afalse%2C%5C%22apkFilterRegEx%5C%22%3A%5C%22dark%5C%22%7D%22%7D)

[<ins>`MaterialYou`](https://apps.obtainium.imranr.dev/redirect.html?r=obtainium://app/%7B%22id%22%3A%22app.rvx.android.youtube%22%2C%22url%22%3A%22https%3A%2F%2Fgithub.com%2FTidalMist%2Frevanced-arm7-arm8-nonroot%22%2C%22author%22%3A%22TidalMist%22%2C%22name%22%3A%22YouTube%22%2C%22additionalSettings%22%3A%22%7B%5C%22versionDetection%5C%22%3Afalse%2C%5C%22apkFilterRegEx%5C%22%3A%5C%22material%5C%22%7D%22%7D)</h1></div>

it will download your arm architecture
> To not see two splash screens, disable `Enable new splash animation` in YouTube settings > ReVanced Extended > Miscellaneous

<details><summary>If links doesn't work</summary>
 
Paste app source url https://github.com/TidalMist/revanced-arm7-arm8-nonroot and in field **Filter apks by regular expression** write `material` or `black` or `dark`

</details>

___

<details><summary>original readme</summary>

#### ⚠️ Do not download modules from 3rd party sources like random websites you found on Google. There are many that uses my modules and impersonates ReVanced.

# ReVanced Magisk Module
[![Telegram](https://img.shields.io/badge/Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/rvc_magisk)
[![CI](https://github.com/j-hc/revanced-magisk-module/actions/workflows/ci.yml/badge.svg?event=schedule)](https://github.com/j-hc/revanced-magisk-module/actions/workflows/ci.yml)

Extensive ReVanced builder  

Get the [latest CI release](https://github.com/j-hc/revanced-magisk-module/releases).

Use [**zygisk-detach**](https://github.com/j-hc/zygisk-detach) to detach YouTube and YT Music from Play Store if you are using magisk modules. 

<details><summary><big>Features</big></summary>
<ul>
 <li>Support all present and future ReVanced and <a href="https://github.com/inotia00/revanced-patches">ReVanced Extended</a> apps</li>
 <li> Can build Magisk modules and non-root APKs</li>
 <li> Updated daily with the latest versions of apps and patches</li>
 <li> Optimize APKs and modules for size</li>
 <li> Modules</li>
    <ul>
     <li> recompile invalidated odex for faster usage</li>
     <li> receive updates from Magisk app</li>
     <li> do not break safetynet or trigger root detections</li>
     <li> handle installation of the correct version of the stock app and all that</li>
     <li> support Magisk and KernelSU</li>
    </ul>
</ul>
Note that the <a href="../../actions/workflows/ci.yml">CI workflow</a> is scheduled to build the modules and APKs everyday using GitHub Actions if there is a change in ReVanced patches. You may want to disable it.
</details>

## To include/exclude patches or patch other apps
[**See the list of patches**](https://j-hc.github.io/rvmm-config-gen/)

 * Star the repo :eyes:
 * Use the repo as a [template](https://github.com/new?template_name=revanced-magisk-module&template_owner=j-hc)
 * Customize [`config.toml`](./config.toml) using [rvmm-config-gen](https://j-hc.github.io/rvmm-config-gen/)
 * Run the build [workflow](../../actions/workflows/build.yml)
 * Grab your modules and APKs from [releases](../../releases)

also see here [`CONFIG.md`](./CONFIG.md)

## Building Locally
### On Termux
```console
bash <(curl -sSf https://raw.githubusercontent.com/j-hc/revanced-magisk-module/main/build-termux.sh)
```

### On Desktop
```console
$ git clone https://github.com/j-hc/revanced-magisk-module
$ cd revanced-magisk-module
$ ./build.sh
```
