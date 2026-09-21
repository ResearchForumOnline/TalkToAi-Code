#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/talktoai-code"
mkdir -p "$APP_DIR"
cp -R . "$APP_DIR/"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements-runtime.txt"
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
echo "Installed TalkToAi Code. Run: talktoai-code"
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
