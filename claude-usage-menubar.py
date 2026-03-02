#!/usr/bin/env python3
"""Claude Code Usage Monitor — macOS Menubar Widget.

Reads usage-stats.json (written by the daemon) and displays
usage in the macOS menubar with a dropdown showing details.

Requires: pip3 install rumps
"""

import json
import os
import subprocess
import time
from pathlib import Path

import rumps

CACHE_PATH = Path.home() / ".claude" / "usage-monitor" / "cache" / "usage-stats.json"
REFRESH_INTERVAL = 30  # seconds


def status_emoji(pct):
    """Return colored circle emoji based on usage percentage."""
    if pct > 80:
        return "\U0001f534"  # red
    elif pct > 50:
        return "\U0001f7e1"  # yellow
    return "\U0001f7e2"  # green


def progress_bar(pct, width=20):
    """Render a text progress bar."""
    filled = int(round(pct / 100 * width))
    filled = min(filled, width)
    return "[" + "=" * filled + "-" * (width - filled) + "]"


def format_cost(usd):
    return f"${usd:.2f}"


class ClaudeUsageApp(rumps.App):
    def __init__(self):
        super().__init__("Claude Usage", quit_button=None)
        self.title = "Claude: --"
        self._notified_80 = set()
        self.menu = [
            rumps.MenuItem("Loading...", callback=None),
            None,  # separator
            rumps.MenuItem("Refresh Now", callback=self.manual_refresh),
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        # Start timer for periodic refresh
        self.timer = rumps.Timer(self.refresh, REFRESH_INTERVAL)
        self.timer.start()
        # Initial refresh
        self.refresh(None)

    def read_stats(self):
        """Read usage-stats.json, return dict or None."""
        try:
            if not CACHE_PATH.exists():
                return None
            # Check freshness (< 5 min)
            age = time.time() - CACHE_PATH.stat().st_mtime
            if age > 300:
                return None
            with open(CACHE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def refresh(self, sender):
        stats = self.read_stats()
        if not stats:
            self.title = "Claude: no data"
            return

        windows = stats.get("windows", {})
        w5h = windows.get("5h", {})
        w7d = windows.get("7d", {})
        wmo = windows.get("monthly", {})

        p5h = w5h.get("usage_pct", 0)
        p7d = w7d.get("usage_pct", 0)
        mo_cost = wmo.get("cost_usd", 0)

        # Menubar title
        self.title = f"{status_emoji(p5h)}{p5h:.0f}% {status_emoji(p7d)}{p7d:.0f}% {format_cost(mo_cost)}"

        # Build dropdown menu
        items = []

        # 5-Hour Window
        items.append(rumps.MenuItem("--- 5-Hour Window ---", callback=None))
        items.append(rumps.MenuItem(
            f"  {format_cost(w5h.get('cost_usd', 0))} / {format_cost(w5h.get('limit_usd', 0))} ({p5h:.0f}%)",
            callback=None
        ))
        items.append(rumps.MenuItem(f"  {progress_bar(p5h)}", callback=None))
        items.append(rumps.MenuItem(
            f"  {w5h.get('request_count', 0)} requests | Resets in {w5h.get('reset_human', '?')}",
            callback=None
        ))
        items.append(None)  # separator

        # 7-Day Window
        items.append(rumps.MenuItem("--- 7-Day Window ---", callback=None))
        items.append(rumps.MenuItem(
            f"  {format_cost(w7d.get('cost_usd', 0))} / {format_cost(w7d.get('limit_usd', 0))} ({p7d:.0f}%)",
            callback=None
        ))
        items.append(rumps.MenuItem(f"  {progress_bar(p7d)}", callback=None))
        items.append(rumps.MenuItem(
            f"  {w7d.get('request_count', 0)} requests | Resets in {w7d.get('reset_human', '?')}",
            callback=None
        ))
        items.append(None)

        # Monthly
        pmo = wmo.get("usage_pct", 0)
        items.append(rumps.MenuItem("--- Monthly ---", callback=None))
        items.append(rumps.MenuItem(
            f"  {format_cost(wmo.get('cost_usd', 0))} / {format_cost(wmo.get('limit_usd', 0))} ({pmo:.0f}%)",
            callback=None
        ))
        items.append(rumps.MenuItem(f"  {progress_bar(pmo)}", callback=None))
        items.append(rumps.MenuItem(
            f"  {wmo.get('request_count', 0)} requests | {wmo.get('reset_human', '?')} until reset",
            callback=None
        ))
        items.append(None)

        # Per-model breakdown (from monthly window)
        models = wmo.get("models", {})
        if models:
            items.append(rumps.MenuItem("--- Models ---", callback=None))
            for model, data in sorted(models.items(), key=lambda x: x[1]["cost_usd"], reverse=True):
                items.append(rumps.MenuItem(
                    f"  {model}: {data['request_count']} req, {format_cost(data['cost_usd'])}",
                    callback=None
                ))
            items.append(None)

        items.append(rumps.MenuItem("Refresh Now", callback=self.manual_refresh))
        items.append(rumps.MenuItem("Quit", callback=rumps.quit_application))

        self.menu.clear()
        for item in items:
            if item is None:
                self.menu.add(rumps.separator)
            else:
                self.menu.add(item)

        # Notifications at 80%+
        for name, pct in [("5h", p5h), ("7d", p7d), ("monthly", pmo)]:
            if pct >= 80 and name not in self._notified_80:
                self._notified_80.add(name)
                rumps.notification(
                    "Claude Usage Warning",
                    f"{name} window at {pct:.0f}%",
                    f"You're approaching the rate limit for the {name} window.",
                    sound=True,
                )

        # Reset notification flags when usage drops below 80
        for name, pct in [("5h", p5h), ("7d", p7d), ("monthly", pmo)]:
            if pct < 70 and name in self._notified_80:
                self._notified_80.discard(name)

    def manual_refresh(self, sender):
        """Trigger daemon re-scan then refresh display."""
        monitor_dir = Path.home() / ".claude" / "usage-monitor"
        daemon_path = monitor_dir / "claude-usage-daemon.py"
        venv_python = monitor_dir / ".venv" / "bin" / "python3"
        python_bin = str(venv_python) if venv_python.exists() else "python3"
        if daemon_path.exists():
            try:
                subprocess.run(
                    [python_bin, str(daemon_path), "--once"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.refresh(None)


if __name__ == "__main__":
    ClaudeUsageApp().run()
