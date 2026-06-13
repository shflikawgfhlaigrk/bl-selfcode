# UTAH — native Command Deck (Flutter)

Native shell for the **live web dashboard** (`live.html`) — full Black Gold deck in a
WebView, same UI as Safari on the Mac. Polls nothing natively; the page talks to the
daemon directly. Real data only.

## Prerequisites

- **Apple Developer** membership (unlocks device install + TestFlight)
- **Flutter** SDK on the Mac (`brew install --cask flutter` or [flutter.dev](https://docs.flutter.dev/get-started/install/macos))
- **Xcode** with your Apple ID signed in (Settings → Accounts)
- iPhone on the same **Tailscale** tailnet as the Mac
- Utah daemon + dashboard running on the Mac (`:8766`), Tailscale serve on `:8765`

## One-time iOS signing setup

1. Register bundle ID **`com.utah.utahApp`** at [developer.apple.com](https://developer.apple.com/account/resources/identifiers/list) (App IDs → +).
2. Copy the signing config:
   ```bash
   cp ios/Signing.xcconfig.example ios/Signing.xcconfig
   ```
3. Edit `ios/Signing.xcconfig` — paste your **Team ID** (10 chars, from Membership or Xcode → Accounts).
4. Open the project once in Xcode to let it create provisioning profiles:
   ```bash
   open ios/Runner.xcworkspace
   ```
   Select **Runner** target → Signing & Capabilities → confirm Team + bundle ID.

`Signing.xcconfig` is gitignored — never commit your Team ID.

## Install on your iPhone (direct)

```bash
cd app
flutter pub get
flutter devices          # plug in iPhone, trust this Mac
flutter run -d <device>  # first run installs the debug build
```

Release build (stays on phone until you delete it):

```bash
flutter build ios --release
# then Xcode → Product → Destination: your iPhone → Run
```

## TestFlight (optional)

```bash
flutter build ipa --release
# Upload the .ipa from build/ios/ipa/ via Xcode → Organizer → Distribute App
```

## Backend proof (no simulator needed)

```bash
dart run bin/proof.dart
```

Exits non-zero if the daemon is unreachable — confirms Tailscale + tailserve before you bother with Xcode.

## Override backend URL

Default dashboard: `http://michaels-macbook-pro.tailb44439.ts.net:8765/` (tailnet HTTP via Tailscale serve).

```bash
flutter run --dart-define=UTAH_BASE=http://127.0.0.1:8766
```

The app loads `{UTAH_BASE}/` — the same `live.html` command deck as the browser.
