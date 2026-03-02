#!/usr/bin/env python3
"""Claude Code Usage Monitor — Background Daemon.

Scans JSONL session files, calculates API costs, writes usage-stats.json.
Runs every N seconds (default 60). Uses mtime/size change detection.
"""

import json
import os
import sys
import time
import glob
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
MONITOR_DIR = CLAUDE_DIR / "usage-monitor"
CONFIG_PATH = MONITOR_DIR / "claude-usage-config.json"
CACHE_PATH = MONITOR_DIR / "cache" / "usage-stats.json"
CLAUDE_JSON = CLAUDE_DIR.parent / ".claude.json"  # ~/.claude.json

# ── Default pricing (Opus 4.6) ──────────────────────────────────────────────
DEFAULT_PRICING = {
    "claude-opus-4-6": {
        "input_per_mtok": 5.00,
        "output_per_mtok": 25.00,
        "cache_read_per_mtok": 0.50,
        "cache_write_1h_per_mtok": 6.25,
    },
    "claude-sonnet-4-6": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "cache_read_per_mtok": 0.30,
        "cache_write_1h_per_mtok": 3.75,
    },
    "claude-haiku-4-5-20251001": {
        "input_per_mtok": 0.80,
        "output_per_mtok": 4.00,
        "cache_read_per_mtok": 0.08,
        "cache_write_1h_per_mtok": 1.00,
    },
}

# Fallback pricing for unknown models (use Sonnet-like pricing)
FALLBACK_PRICING = {
    "input_per_mtok": 3.00,
    "output_per_mtok": 15.00,
    "cache_read_per_mtok": 0.30,
    "cache_write_1h_per_mtok": 3.75,
}


def load_config():
    """Load config, falling back to defaults."""
    config = {
        "plan": "max_5x",
        "limits": {
            "5h_rolling_cost_usd": 5.00,
            "7d_rolling_cost_usd": 300.00,
            "monthly_cost_usd": 500.00,
        },
        "refresh_interval_seconds": 60,
        "pricing": DEFAULT_PRICING,
    }
    try:
        with open(CONFIG_PATH) as f:
            user_config = json.load(f)
        # Merge user config over defaults
        for key in ("plan", "limits", "refresh_interval_seconds", "pricing"):
            if key in user_config:
                if isinstance(user_config[key], dict) and isinstance(config.get(key), dict):
                    config[key].update(user_config[key])
                else:
                    config[key] = user_config[key]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return config


def get_billing_start():
    """Get subscription billing period start from .claude.json."""
    try:
        with open(CLAUDE_JSON) as f:
            data = json.load(f)
        created = data.get("oauthAccount", {}).get("subscriptionCreatedAt", "")
        if created:
            return datetime.fromisoformat(created.replace("Z", "+00:00"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    return None


def get_monthly_start(billing_start):
    """Calculate the start of the current billing month."""
    now = datetime.now(timezone.utc)
    if not billing_start:
        # Fallback: 1st of current month
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Find the most recent billing cycle day
    bill_day = billing_start.day
    try:
        start = now.replace(day=bill_day, hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        # bill_day > days in current month, use last day
        import calendar
        last_day = calendar.monthrange(now.year, now.month)[1]
        start = now.replace(day=last_day, hour=0, minute=0, second=0, microsecond=0)

    if start > now:
        # Go back one month
        if start.month == 1:
            start = start.replace(year=start.year - 1, month=12)
        else:
            try:
                start = start.replace(month=start.month - 1)
            except ValueError:
                import calendar
                prev_month = start.month - 1
                last_day = calendar.monthrange(start.year, prev_month)[1]
                start = start.replace(month=prev_month, day=min(bill_day, last_day))
    return start


def find_jsonl_files():
    """Find all JSONL session files including subagents."""
    patterns = [
        str(PROJECTS_DIR / "**" / "*.jsonl"),
    ]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern, recursive=True))
    return files


class FileTracker:
    """Track file mtime/size to avoid re-parsing unchanged files."""

    def __init__(self):
        self._cache = {}  # path -> (mtime, size, parsed_requests)

    def needs_reparse(self, path):
        try:
            stat = os.stat(path)
        except OSError:
            return False
        key = (stat.st_mtime, stat.st_size)
        cached = self._cache.get(path)
        if cached and cached[0] == key:
            return False
        return True

    def get_cached(self, path):
        cached = self._cache.get(path)
        if cached:
            return cached[1]
        return None

    def update(self, path, parsed_requests):
        try:
            stat = os.stat(path)
            key = (stat.st_mtime, stat.st_size)
            self._cache[path] = (key, parsed_requests)
        except OSError:
            pass


def parse_jsonl_file(filepath):
    """Parse a JSONL file, extract usage per requestId (last chunk wins)."""
    requests = {}  # requestId -> {model, usage, timestamp}

    try:
        with open(filepath, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "assistant":
                    continue

                msg = entry.get("message", {})
                usage = msg.get("usage")
                request_id = entry.get("requestId")
                timestamp = entry.get("timestamp")
                model = msg.get("model", "unknown")

                if not usage or not request_id:
                    continue

                # Keep the entry with highest output_tokens per requestId
                existing = requests.get(request_id)
                current_output = usage.get("output_tokens", 0)
                if existing is None or current_output >= existing["usage"].get("output_tokens", 0):
                    requests[request_id] = {
                        "model": model,
                        "usage": usage,
                        "timestamp": timestamp,
                        "request_id": request_id,
                    }
    except (OSError, IOError):
        pass

    return requests


def calculate_cost(usage, model, pricing):
    """Calculate USD cost for a single request's usage."""
    prices = pricing.get(model, FALLBACK_PRICING)
    if isinstance(prices, dict) and "input_per_mtok" not in prices:
        prices = FALLBACK_PRICING

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)

    # Cache write: use 1h tokens from cache_creation if available
    cache_creation = usage.get("cache_creation", {})
    cache_write_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
    # Fallback to top-level cache_creation_input_tokens if no breakdown
    if cache_write_1h == 0:
        cache_write_1h = usage.get("cache_creation_input_tokens", 0)

    cost = (
        (input_tokens / 1_000_000) * prices.get("input_per_mtok", 5.0)
        + (output_tokens / 1_000_000) * prices.get("output_per_mtok", 25.0)
        + (cache_read / 1_000_000) * prices.get("cache_read_per_mtok", 0.5)
        + (cache_write_1h / 1_000_000) * prices.get("cache_write_1h_per_mtok", 10.0)
    )
    return cost


def compute_stats(all_requests, config):
    """Compute usage stats for all time windows."""
    now = datetime.now(timezone.utc)
    pricing = config.get("pricing", DEFAULT_PRICING)
    limits = config.get("limits", {})

    windows = {
        "5h": now - timedelta(hours=5),
        "7d": now - timedelta(days=7),
    }

    billing_start = get_billing_start()
    monthly_start = get_monthly_start(billing_start)
    windows["monthly"] = monthly_start

    # Initialize accumulators
    stats = {}
    for window_name in windows:
        stats[window_name] = {
            "cost_usd": 0.0,
            "request_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "models": {},
        }

    # Per-model global stats
    model_totals = {}

    for req in all_requests.values():
        ts_str = req.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        model = req["model"]
        usage = req["usage"]
        cost = calculate_cost(usage, model, pricing)

        for window_name, window_start in windows.items():
            if ts >= window_start:
                s = stats[window_name]
                s["cost_usd"] += cost
                s["request_count"] += 1
                s["input_tokens"] += usage.get("input_tokens", 0)
                s["output_tokens"] += usage.get("output_tokens", 0)
                s["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)
                cache_creation = usage.get("cache_creation", {})
                s["cache_write_tokens"] += cache_creation.get("ephemeral_1h_input_tokens", 0) or usage.get("cache_creation_input_tokens", 0)

                if model not in s["models"]:
                    s["models"][model] = {"request_count": 0, "cost_usd": 0.0}
                s["models"][model]["request_count"] += 1
                s["models"][model]["cost_usd"] += cost

    # Build output
    limit_map = {
        "5h": limits.get("5h_rolling_cost_usd", 5.0),
        "7d": limits.get("7d_rolling_cost_usd", 300.0),
        "monthly": limits.get("monthly_cost_usd", 500.0),
    }

    result = {
        "generated_at": now.isoformat(),
        "plan": config.get("plan", "max_5x"),
        "billing_period_start": monthly_start.isoformat(),
        "windows": {},
    }

    for window_name in ("5h", "7d", "monthly"):
        s = stats[window_name]
        limit = limit_map[window_name]
        pct = (s["cost_usd"] / limit * 100) if limit > 0 else 0

        # Calculate reset time
        if window_name == "5h":
            reset_at = now + timedelta(hours=5)
            # Find oldest request in window to calculate actual reset
            oldest_in_window = None
            for req in all_requests.values():
                ts_str = req.get("timestamp")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
                if ts >= windows["5h"]:
                    if oldest_in_window is None or ts < oldest_in_window:
                        oldest_in_window = ts
            if oldest_in_window:
                reset_at = oldest_in_window + timedelta(hours=5)
            reset_seconds = max(0, (reset_at - now).total_seconds())
        elif window_name == "7d":
            reset_at = now + timedelta(days=7)
            oldest_in_window = None
            for req in all_requests.values():
                ts_str = req.get("timestamp")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
                if ts >= windows["7d"]:
                    if oldest_in_window is None or ts < oldest_in_window:
                        oldest_in_window = ts
            if oldest_in_window:
                reset_at = oldest_in_window + timedelta(days=7)
            reset_seconds = max(0, (reset_at - now).total_seconds())
        else:
            # Monthly: next billing date
            next_month = monthly_start.month + 1
            next_year = monthly_start.year
            if next_month > 12:
                next_month = 1
                next_year += 1
            try:
                next_billing = monthly_start.replace(year=next_year, month=next_month)
            except ValueError:
                import calendar
                last_day = calendar.monthrange(next_year, next_month)[1]
                next_billing = monthly_start.replace(year=next_year, month=next_month, day=last_day)
            reset_seconds = max(0, (next_billing - now).total_seconds())

        result["windows"][window_name] = {
            "cost_usd": round(s["cost_usd"], 4),
            "limit_usd": limit,
            "usage_pct": round(pct, 1),
            "request_count": s["request_count"],
            "input_tokens": s["input_tokens"],
            "output_tokens": s["output_tokens"],
            "cache_read_tokens": s["cache_read_tokens"],
            "cache_write_tokens": s["cache_write_tokens"],
            "reset_seconds": int(reset_seconds),
            "reset_human": format_duration(int(reset_seconds)),
            "models": {
                m: {"request_count": d["request_count"], "cost_usd": round(d["cost_usd"], 4)}
                for m, d in s["models"].items()
            },
        }

    return result


def format_duration(seconds):
    """Format seconds as human-readable duration."""
    if seconds <= 0:
        return "now"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 and days == 0:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "<1m"


def write_stats_atomic(stats):
    """Write stats to cache file atomically using temp file + rename."""
    cache_dir = CACHE_PATH.parent
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(cache_dir), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(stats, f, indent=2)
        os.replace(tmp_path, str(CACHE_PATH))
    except OSError as e:
        print(f"Error writing stats: {e}", file=sys.stderr)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def run_once(tracker):
    """Run a single scan cycle. Returns the stats dict."""
    config = load_config()
    jsonl_files = find_jsonl_files()

    all_requests = {}

    for filepath in jsonl_files:
        if tracker.needs_reparse(filepath):
            requests = parse_jsonl_file(filepath)
            tracker.update(filepath, requests)
        else:
            requests = tracker.get_cached(filepath)
            if requests is None:
                requests = parse_jsonl_file(filepath)
                tracker.update(filepath, requests)

        all_requests.update(requests)

    stats = compute_stats(all_requests, config)
    write_stats_atomic(stats)

    return stats


def main():
    """Main daemon loop."""
    print(f"Claude Usage Daemon starting...")
    print(f"  Config: {CONFIG_PATH}")
    print(f"  Cache:  {CACHE_PATH}")
    print(f"  Scanning: {PROJECTS_DIR}")

    tracker = FileTracker()
    config = load_config()
    interval = config.get("refresh_interval_seconds", 60)

    # Run once immediately
    one_shot = "--once" in sys.argv
    stats = run_once(tracker)
    w5h = stats["windows"].get("5h", {})
    w7d = stats["windows"].get("7d", {})
    wmo = stats["windows"].get("monthly", {})
    print(f"  5h: ${w5h.get('cost_usd', 0):.2f}/{w5h.get('limit_usd', 0):.0f} ({w5h.get('usage_pct', 0):.0f}%) | "
          f"7d: ${w7d.get('cost_usd', 0):.2f}/{w7d.get('limit_usd', 0):.0f} ({w7d.get('usage_pct', 0):.0f}%) | "
          f"mo: ${wmo.get('cost_usd', 0):.2f}/{wmo.get('limit_usd', 0):.0f} ({wmo.get('usage_pct', 0):.0f}%)")

    if one_shot:
        print(json.dumps(stats, indent=2))
        return

    print(f"  Refresh interval: {interval}s")
    print(f"  Running... (Ctrl+C to stop)")

    while True:
        try:
            time.sleep(interval)
            config = load_config()
            interval = config.get("refresh_interval_seconds", 60)
            stats = run_once(tracker)
        except KeyboardInterrupt:
            print("\nDaemon stopped.")
            break
        except Exception as e:
            print(f"Error in scan cycle: {e}", file=sys.stderr)
            time.sleep(interval)


if __name__ == "__main__":
    main()
