# Atlas Flutter Android

Flutter Android client for the Atlas / UN job application helper product. The Swift app in
`apps/apple/` is the product reference; this app targets Android parity for Search, filters, saved
jobs/searches, updates, sources, settings, local cache, and job detail.

## Local Server

Run or connect to `services/job-api` before refreshing Search data.

Default physical-device URL used in this parity loop:

```text
http://10.253.1.43:8765
```

Emulator URL when the server runs on the host machine:

```text
http://10.0.2.2:8765
```

Android release builds allow cleartext HTTP only for the known local development hosts in
`android/app/src/main/res/xml/network_security_config.xml`. Do not broaden this for production
networks without moving the API to HTTPS.

## Run

```sh
cd apps/atlas_flutter
flutter pub get
flutter run -d <android-device-id>
```

In the app, open Settings, set the API base URL, then use `Save and Reload` to refresh Search data
and persist the local cache.

## Test

```sh
cd apps/atlas_flutter
dart format --set-exit-if-changed .
dart analyze
flutter test --coverage
flutter test integration_test -d emulator-5554
```

The integration test expects an Android emulator or device with the Atlas app dependencies available.
For the parity loop, the emulator profile is `Pixel_8_Pro_API_17`.

## Build

```sh
cd apps/atlas_flutter
flutter build apk --debug
flutter build apk --release
flutter build appbundle --release
```

Primary release APK path:

```text
apps/atlas_flutter/build/app/outputs/flutter-apk/app-release.apk
```

Release AAB path:

```text
apps/atlas_flutter/build/app/outputs/bundle/release/app-release.aab
```

Install a release APK on a connected Pixel:

```sh
adb -s <device-id> install -r build/app/outputs/flutter-apk/app-release.apk
adb -s <device-id> shell am start -n com.yutsukioka.jobagg.atlas/.MainActivity
```

## Local Cache Verification

Expected behavior:

- A successful server refresh writes a persistent local cache.
- App startup loads cached Search rows before network refresh.
- Offline startup shows cached rows, result count, and local-save state instead of an empty Search
  screen.
- Settings shows cache timestamp, freshness, cached count, search total, health open count, refresh,
  and clear-cache controls.

Manual offline flow:

1. Start with the local server reachable.
2. Open Settings and tap `Save and Reload`.
3. Force-stop the app.
4. Make the server unreachable from the Android device.
5. Relaunch Atlas.
6. Verify Search rows are visible from cache immediately.

Desktop builds store this cache in the operating system's persistent Application
Support/application-data location under an `Atlas` directory. They do not use the
system temporary directory. See
[`docs/architecture/atlas_storage_retention_policy.md`](../../docs/architecture/atlas_storage_retention_policy.md)
for historical-detail retention, legacy cache migration, archive retention, and
future cache-deduplication decisions.

## Screenshots And Review

Loop evidence is stored under:

```text
apps/atlas_flutter/docs/loop/
```

Current review documents:

- `docs/loop/ANDROID_SEARCH_UI_AUDIT.md`
- `docs/loop/IOS_ANDROID_VISUAL_REVIEW.md`
- `docs/loop/PHYSICAL_PIXEL_VERIFICATION.md`
- `docs/loop/STATUS.jsonl`

Physical Pixel verification is not complete yet. The 2026-07-03 pass captured physical in-app
screenshots for Search, scrolled Search, Filter sheet, Sort menu, Saved, Updates, Sources, and
Settings after the ANR/cache fixes, but the corrected physical Job Detail screenshot and physical
offline-restart check are still pending because the device was detached before recapture. PR #10
still needs the human approval comment for the physical-device gate.
