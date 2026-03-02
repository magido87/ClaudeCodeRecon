# Claude Code Usage Monitor

> Anthropic silently removed the usage bar from `claude.ai/settings/usage`.
> This is the replacement — runs locally, reads your own data, no extensions needed.

Tracks your Claude Code API spend across three rolling windows in real-time:
- **5-hour** rolling window
- **7-day** rolling window
- **Monthly** billing cycle

macOS menubar widget + background daemon. Zero cloud dependency.

---

## How it works

Claude Code writes every session to JSONL files under `~/.claude/projects/`.
The daemon parses those files, extracts token usage per request, calculates USD cost using the official model pricing, and writes a stats cache every 60s.
The menubar widget reads the cache and renders live usage with color-coded indicators.

```
~/.claude/projects/**/*.jsonl
         ↓  (daemon parses, deduplicates by requestId)
~/.claude/usage-monitor/cache/usage-stats.json
         ↓  (menubar reads every 30s)
macOS menu bar: 🟢14% 🟡52% $3.21
```

---

## Install

**1. Copy files to `~/.claude/usage-monitor/`**

```bash
mkdir -p ~/.claude/usage-monitor
cp claude-usage-daemon.py claude-usage-menubar.py claude-usage-config.json install.sh uninstall.sh ~/.claude/usage-monitor/
```

**2. Install dependencies**

```bash
cd ~/.claude/usage-monitor
python3 -m venv .venv
.venv/bin/pip install rumps
```

**3. Wire up the LaunchAgent (auto-start on login)**

```bash
bash ~/.claude/usage-monitor/install.sh
```

**4. Launch the menubar widget**

```bash
~/.claude/usage-monitor/.venv/bin/python3 ~/.claude/usage-monitor/claude-usage-menubar.py &
```

---

## Usage

| Action | What happens |
|--------|-------------|
| Menubar shows `🟢14% 🟡52% $3.21` | 5h% · 7d% · monthly spend |
| Click menubar icon | Dropdown with per-window breakdown + progress bars |
| Click **Refresh Now** | Triggers daemon re-scan immediately |
| `>80%` on any window | macOS notification fires once per threshold cross |

### Run daemon manually (one-shot, for debugging)

```bash
python3 claude-usage-daemon.py --once
```

Prints current stats to stdout and exits.

---

## Configuration

Edit `claude-usage-config.json` to match your plan:

```json
{
  "plan": "max_5x",
  "limits": {
    "5h_rolling_cost_usd": 33.00,
    "7d_rolling_cost_usd": 300.00,
    "monthly_cost_usd": 500.00
  },
  "refresh_interval_seconds": 60,
  "pricing": {
    "claude-opus-4-6": {
      "input_per_mtok": 5.00,
      "output_per_mtok": 25.00,
      "cache_read_per_mtok": 0.50,
      "cache_write_1h_per_mtok": 6.25
    },
    "claude-sonnet-4-6": {
      "input_per_mtok": 3.00,
      "output_per_mtok": 15.00,
      "cache_read_per_mtok": 0.30,
      "cache_write_1h_per_mtok": 3.75
    },
    "claude-haiku-4-5-20251001": {
      "input_per_mtok": 0.80,
      "output_per_mtok": 4.00,
      "cache_read_per_mtok": 0.08,
      "cache_write_1h_per_mtok": 1.00
    }
  }
}
```

The daemon hot-reloads this config on every scan cycle — no restart needed.

---

## File structure

```
~/.claude/usage-monitor/
├── claude-usage-daemon.py     # background scanner, writes cache
├── claude-usage-menubar.py    # macOS menubar widget (rumps)
├── claude-usage-config.json   # limits + pricing config
├── install.sh                 # registers daemon as LaunchAgent
├── uninstall.sh               # removes LaunchAgent
├── .venv/                     # local venv (rumps)
└── cache/
    └── usage-stats.json       # output — read by menubar
```

---

## Uninstall

```bash
bash ~/.claude/usage-monitor/uninstall.sh
rm -rf ~/.claude/usage-monitor
```

---

## Requirements

- macOS (menubar widget uses `rumps`)
- Python 3.9+
- Claude Code installed (needs `~/.claude/projects/` JSONL files to exist)
- `pip install rumps`

---

## Notes

- Cost estimates are calculated from token counts in local JSONL files. They reflect API pricing — actual Claude Max subscription billing may differ.
- The daemon uses mtime/size change detection so it only re-parses files that changed since the last cycle.
- Billing period start is read from `~/.claude.json` (`oauthAccount.subscriptionCreatedAt`). Falls back to the 1st of the current month if not found.
