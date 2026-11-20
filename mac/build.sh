#!/bin/bash
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:?provide an empty output directory}"
mkdir -p "$dest"
dest="$(cd "$dest" && pwd)"
app="$dest/XREAL Updater.app"
if [[ -e "$app" ]]; then echo "Output app already exists" >&2; exit 1; fi
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
app="$temporary/XREAL Updater.app"
"${PYTHON:-python3}" -m PyInstaller --distpath "$temporary/dist" --workpath "$temporary/build" "$root/mac-worker.spec"
mkdir -p "$app/Contents/"{MacOS,Helpers,Resources}
ditto "$temporary/dist/XREAL Updater.app" "$app/Contents/Helpers/Engine.app"
cp "$app/Contents/Helpers/Engine.app/Contents/Info.plist" "$app/Contents/Info.plist"
cp "$app/Contents/Helpers/Engine.app/Contents/Resources/icon-windowed.icns" "$app/Contents/Resources/"
/usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier local.xreal.russian-updater.native' "$app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Set :CFBundleVersion 2' "$app/Contents/Info.plist"
swiftc -O -target arm64-apple-macos12.0 "$root"/mac/*.swift -o "$app/Contents/MacOS/XREAL Updater"
xattr -cr "$app"
codesign --force --sign - "$app"
codesign --verify --strict --deep "$app"
ditto --noextattr "$app" "$dest/XREAL Updater.app"
