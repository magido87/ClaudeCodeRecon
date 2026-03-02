#!/usr/bin/env bash
set -euo pipefail

MONITOR_DIR="$HOME/.claude/usage-monitor"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
DAEMON_PLIST="com.claude-usage-daemon.plist"
MENUBAR_PLIST="com.claude-usage-menubar.plist"

echo "=== Claude Usage Monitor — Uninstall ==="

# ── 1. Stop and unload LaunchAgents ─────────────────────────────────────────
if launchctl bootout "gui/$(id -u)/$DAEMON_PLIST" 2>/dev/null; then
    echo "✓ Daemon stopped"
else
    echo "  Daemon was not running"
fi

if launchctl bootout "gui/$(id -u)/$MENUBAR_PLIST" 2>/dev/null; then
    echo "✓ Menubar stopped"
else
    echo "  Menubar was not running"
fi

# ── 2. Remove plist files ──────────────────────────────────────────────────
rm -f "$LAUNCH_AGENTS/$DAEMON_PLIST"
rm -f "$LAUNCH_AGENTS/$MENUBAR_PLIST"
echo "✓ LaunchAgent plists removed"

# ── 3. Remove monitor directory ────────────────────────────────────────────
read -rp "Remove all monitor files ($MONITOR_DIR)? [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    rm -rf "$MONITOR_DIR"
    echo "✓ Monitor directory removed"
else
    echo "  Kept monitor directory (config + cache preserved)"
fi

echo ""
echo "=== Uninstall complete ==="
