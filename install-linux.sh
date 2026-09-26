#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/talktoai-code"
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
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/applications" "$HOME/.local/bin"
cat > "$HOME/.local/bin/talktoai-code" <<EOF
#!/usr/bin/env bash
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/studio.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/talktoai-code"
cat > "${XDG_DATA_HOME:-$HOME/.local/share}/applications/talktoai-code.desktop" <<EOF
[Desktop Entry]
Name=TalkToAi Code
Comment=Local-first coding and game workspace
Exec=$HOME/.local/bin/talktoai-code
Terminal=false
Type=Application
Categories=Development;IDE;
EOF
echo "Installed TalkToAi Code. Launch from your applications menu or run $HOME/.local/bin/talktoai-code"
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${HOME}/Library/Application Support/TalkToAiCode"
mkdir -p "$APP_DIR"
cp -R . "$APP_DIR/"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements-runtime.txt"
mkdir -p "$HOME/bin"
cat > "$HOME/bin/talktoai-code" <<EOF
#!/usr/bin/env bash
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/studio.py" "\$@"
EOF
chmod +x "$HOME/bin/talktoai-code"
echo "Installed TalkToAi Code. Add $HOME/bin to PATH, then run: talktoai-code"
