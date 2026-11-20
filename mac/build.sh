#!/bin/bash
set -eu
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:?provide an empty output directory}"
export XREAL_MAC_ARCH="${2:-arm64}"
case "$XREAL_MAC_ARCH" in
    arm64|universal2) ;;
    *) echo "Expected arm64 or universal2" >&2; exit 1 ;;
esac
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
executable="$app/Contents/MacOS/XREAL Updater"
if [[ "$XREAL_MAC_ARCH" == universal2 ]]; then
    for arch in arm64 x86_64; do
        swiftc -O -target "${arch}-apple-macos13.0" "$root"/mac/*.swift -o "$temporary/ui-$arch"
    done
    lipo -create "$temporary/ui-arm64" "$temporary/ui-x86_64" -output "$executable"
    "${PYTHON:-python3}" "$root/mac/check_universal.py" "$app"
else
    swiftc -O -target arm64-apple-macos12.0 "$root"/mac/*.swift -o "$executable"
fi
xattr -cr "$app"
codesign --force --sign - "$app"
codesign --verify --strict --deep "$app"
ditto --noextattr "$app" "$dest/XREAL Updater.app"
