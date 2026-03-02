#!/usr/bin/env bash
set -euo pipefail

MONITOR_DIR="$HOME/.claude/usage-monitor"
VENV_DIR="$MONITOR_DIR/.venv"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
DAEMON_PLIST="com.claude-usage-daemon.plist"
MENUBAR_PLIST="com.claude-usage-menubar.plist"
VENV_PYTHON="$VENV_DIR/bin/python3"

echo "=== Claude Usage Monitor — Install ==="

# ── 1. Create dirs ──────────────────────────────────────────────────────────
mkdir -p "$MONITOR_DIR/cache"
echo "✓ Directories ready"

# ── 2. Create venv and install rumps ────────────────────────────────────────
if [ ! -f "$VENV_PYTHON" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

if "$VENV_PYTHON" -c "import rumps" 2>/dev/null; then
    echo "✓ rumps already installed"
else
    echo "  Installing rumps into venv..."
    "$VENV_DIR/bin/pip" install --quiet rumps
    echo "✓ rumps installed"
fi
echo "  venv: $VENV_DIR"

# ── 3. Make scripts executable ──────────────────────────────────────────────
chmod +x "$MONITOR_DIR/claude-usage-daemon.py"
chmod +x "$MONITOR_DIR/claude-usage-menubar.py"
echo "✓ Scripts executable"

# ── 4. Run daemon once to generate initial cache ────────────────────────────
echo "  Running initial scan..."
"$VENV_PYTHON" "$MONITOR_DIR/claude-usage-daemon.py" --once 2>&1 | grep -E '^\s+(5h|Config|Cache|Scanning)' || true
echo "✓ Initial cache generated"

# ── 5. Create LaunchAgent for daemon ────────────────────────────────────────
mkdir -p "$LAUNCH_AGENTS"
cat > "$LAUNCH_AGENTS/$DAEMON_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-usage-daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>${MONITOR_DIR}/claude-usage-daemon.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${MONITOR_DIR}/cache/daemon-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${MONITOR_DIR}/cache/daemon-stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
PLIST
echo "✓ Daemon LaunchAgent created"

# ── 6. Create LaunchAgent for menubar ───────────────────────────────────────
cat > "$LAUNCH_AGENTS/$MENUBAR_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude-usage-menubar</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>${MONITOR_DIR}/claude-usage-menubar.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${MONITOR_DIR}/cache/menubar-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${MONITOR_DIR}/cache/menubar-stderr.log</string>
</dict>
</plist>
PLIST
echo "✓ Menubar LaunchAgent created"

# ── 7. Load agents ──────────────────────────────────────────────────────────
# Unload first if already loaded (ignore errors)
launchctl bootout "gui/$(id -u)/com.claude-usage-daemon" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.claude-usage-menubar" 2>/dev/null || true

launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENTS/$DAEMON_PLIST"
echo "✓ Daemon started"

launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENTS/$MENUBAR_PLIST"
echo "✓ Menubar started"

echo ""
echo "=== Install complete ==="
echo "  Daemon:  running (refreshes every 60s)"
echo "  Menubar: running (check your menubar)"
echo "  Config:  $MONITOR_DIR/claude-usage-config.json"
echo "  Cache:   $MONITOR_DIR/cache/usage-stats.json"
echo ""
echo "  To uninstall: bash $MONITOR_DIR/uninstall.sh"
