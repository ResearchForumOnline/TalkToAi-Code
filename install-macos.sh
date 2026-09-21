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
