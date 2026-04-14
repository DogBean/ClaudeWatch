# ClaudeWatch

A lightweight floating desktop widget that monitors your [Claude Code](https://claude.ai/code) sessions in real time on Windows.

![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows)
![License](https://img.shields.io/badge/License-MIT-green)

## Features

- **Live Session Monitoring** - Automatically detects active Claude Code CLI sessions and displays their status (idle, thinking, working)
- **Always on Top** - Floats above all windows as a compact overlay
- **Draggable** - Grab the grip bar to reposition anywhere on screen
- **Pin/Unpin** - Toggle always-on-top behavior
- **Opacity Control** - Adjust window transparency from 20% to 100% via a slider
- **Auto-start on Boot** - Optional startup with Windows via registry entry
- **Rounded Corners** - Native Win32 region clipping for smooth rounded edges
- **Dark Theme** - Cyberpunk-inspired UI with JetBrains Mono font and glow effects

## Preview

The widget displays each Claude Code session as a card with:

| Status    | Color  | Indicator         |
|-----------|--------|--------------------|
| Idle      | Green  | Static play icon   |
| Thinking  | Amber  | Pulsing lightbulb  |
| Working   | Cyan   | Spinning loader    |

A scan-line animation plays across cards in the working state.

## Getting Started

### Prerequisites

- Python 3.8+
- Windows 10/11
- [pywebview](https://pywebview.flowrl.com/) with EdgeChromium backend

### Install

```bash
pip install pywebview
```

### Run

```bash
python widget.py
```

## Settings

Click **SETTINGS** at the bottom to expand the settings panel:

### Auto-start on Boot

Toggle the switch to register/unregister ClaudeWatch in the Windows startup registry:

```
HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
```

### Opacity

Drag the slider to adjust window transparency. Uses Win32 `SetLayeredWindowAttributes` API for smooth, flicker-free transparency without the white-background artifacts common with CSS-only approaches.

Range: **20%** (nearly invisible) to **100%** (fully opaque)

## How It Works

```
widget.py (pywebview host)
  └── StatusMonitor (background thread, polls every 2s)
        └── Reads ~/.claude/projects/**/*.jsonl
              └── Pushes session status to UI via evaluate_js()
                    └── widget.html renders task cards
```

1. **StatusMonitor** scans `~/.claude/projects/` for recently modified `.jsonl` session files
2. Parses the last 60 lines of each session to extract tool usage and text output
3. Determines session status based on file modification time (< 10s = working, < 45s = thinking, else idle)
4. Pushes updates to the HTML frontend via `window.updateTasks()`
5. The UI auto-resizes the window to fit content using `pywebview.api.resize()`

## Project Structure

```
ClaudeWatch/
├── widget.py              # pywebview host + session monitor
├── widget.html             # UI (HTML/CSS/JS in single file)
├── claude_monitor.py       # Alternative monitor implementation
├── claude_status_widget.html  # Standalone demo page
├── ClaudeMonitor.spec      # PyInstaller build spec
└── README.md
```

## Building

```bash
pip install pyinstaller
pyinstaller ClaudeMonitor.spec
```

## License

MIT
