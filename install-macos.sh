#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HOME}/Library/Application Support/TalkToAiCode"
PYTHON="$(command -v python3.12 || command -v python3)"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'
"$PYTHON" "$SOURCE_DIR/install_source.py" "$APP_DIR"
"$PYTHON" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements-runtime.txt"
if [[ "${TALKTOAI_SKIP_BROWSER_DOWNLOAD:-0}" != 1 ]]; then
  if ! "$APP_DIR/.venv/bin/python" -m playwright install chromium; then
    echo 'Browser download failed; use an installed Chrome or retry the Playwright install.' >&2
  fi
fi
mkdir -p "$HOME/bin"
cat > "$HOME/bin/talktoai-code" <<EOF
#!/usr/bin/env bash
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/studio.py" "\$@"
EOF
chmod +x "$HOME/bin/talktoai-code"
APP_BUNDLE="$HOME/Applications/TalkToAi Code.app"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
cat > "$APP_BUNDLE/Contents/MacOS/TalkToAiCode" <<EOF
#!/usr/bin/env bash
exec "$HOME/bin/talktoai-code" "\$@"
EOF
chmod +x "$APP_BUNDLE/Contents/MacOS/TalkToAiCode"
cat > "$APP_BUNDLE/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>TalkToAi Code</string>
<key>CFBundleIdentifier</key><string>org.talktoai.code</string>
<key>CFBundleExecutable</key><string>TalkToAiCode</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>0.5.0</string>
<key>CFBundleShortVersionString</key><string>0.5.0</string>
</dict></plist>
EOF
echo "Installed TalkToAi Code. Open $APP_BUNDLE or run $HOME/bin/talktoai-code"
