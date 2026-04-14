#!/usr/bin/env python3
"""
Claude Code Status Widget - Windows Desktop Floating Window
Displays real Claude Code sessions with live status.

Usage: python widget.py
"""
import webview
import os
import sys
import json
import time
import threading
import ctypes
from collections import deque
import winreg


class WidgetAPI:
    """JavaScript <-> Python bridge for window control"""

    def __init__(self):
        self._window = None
        self._drag_start = None
        self._hwnd = None

    def set_window(self, window):
        self._window = window

    def set_hwnd(self, hwnd):
        self._hwnd = hwnd

    def move_window(self, dx, dy):
        if not self._window:
            return
        if self._drag_start is None:
            self._drag_start = {'x': self._window.x, 'y': self._window.y}
        self._window.move(
            self._drag_start['x'] + int(dx),
            self._drag_start['y'] + int(dy),
        )

    def end_drag(self):
        self._drag_start = None

    def resize(self, width, height):
        if self._window:
            w, h = int(width), int(height)
            self._window.resize(w, h)
            self._apply_rounded_corners(w, h)

    def _apply_rounded_corners(self, width, height, radius=14):
        if not self._hwnd:
            return
        try:
            rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
                0, 0, width + 1, height + 1,
                radius * 2, radius * 2
            )
            ctypes.windll.user32.SetWindowRgn(self._hwnd, rgn, True)
        except Exception:
            pass

    def close_window(self):
        if self._window:
            self._window.destroy()

    def set_opacity(self, percent):
        """Set window opacity via Win32 layered window API"""
        if not self._hwnd:
            return
        try:
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x80000
            LWA_ALPHA = 0x02
            ex_style = ctypes.windll.user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE,
                                                 ex_style | WS_EX_LAYERED)
            alpha = int(max(10, min(255, percent * 255 / 100)))
            ctypes.windll.user32.SetLayeredWindowAttributes(
                self._hwnd, 0, alpha, LWA_ALPHA)
        except Exception:
            pass

    def toggle_pin(self):
        if self._window:
            self._window.on_top = not getattr(self._window, '_on_top', True)

    def set_autostart(self, enabled):
        """Add or remove startup registry entry"""
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
        app_name = 'ClaudeWatch'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                 winreg.KEY_SET_VALUE)
            if enabled:
                exe_path = os.path.abspath(sys.argv[0])
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ,
                                  f'"{sys.executable}" "{exe_path}"')
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
            self._window._on_top = self._window.on_top


class StatusMonitor:
    """Scan Claude Code session files and push real status to the widget"""

    MAX_AGE_SECONDS = 1800   # 30 minutes
    POLL_INTERVAL = 2        # seconds
    MAX_SESSIONS = 6

    def __init__(self, window):
        self.window = window
        home = os.path.expanduser('~')
        self.projects_dir = os.path.join(home, '.claude', 'projects')
        self.running = True
        self._prev_json = ''

    # ---- data collection ----

    def _tail(self, filepath, n=60):
        """Read last n lines efficiently"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return list(deque(f, maxlen=n))
        except Exception:
            return []

    def _parse_session(self, filepath, project_dir_name):
        """Extract status info from a session JSONL file"""
        lines = self._tail(filepath, 60)
        if not lines:
            return None

        cwd = None
        last_tool = None
        last_text = None
        version = None

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            if not cwd and obj.get('cwd'):
                cwd = obj['cwd']
            if not version and obj.get('version'):
                version = obj['version']

            t = obj.get('type', '')
            if t == 'assistant':
                msg = obj.get('message', {})
                content = msg.get('content', '')
                if isinstance(content, list):
                    for c in content:
                        if c.get('type') == 'tool_use' and not last_tool:
                            name = c.get('name', '')
                            inp = c.get('input', {})
                            # Build short description
                            if name == 'Read':
                                last_tool = 'Read ' + os.path.basename(str(inp.get('file_path', '')))
                            elif name == 'Edit' or name == 'Write':
                                last_tool = name + ' ' + os.path.basename(str(inp.get('file_path', '')))
                            elif name == 'Bash':
                                cmd = str(inp.get('command', ''))[:40]
                                last_tool = f'Bash: {cmd}'
                            else:
                                last_tool = name
                        elif c.get('type') == 'text' and not last_text:
                            text = c.get('text', '').strip()[:60]
                            if text:
                                last_text = text
                elif isinstance(content, str) and not last_text:
                    text = content.strip()[:60]
                    if text:
                        last_text = text

                if last_tool or last_text:
                    break  # Got the latest activity, stop parsing

            elif t in ('user', 'human'):
                # If the latest entry is from user, Claude might be thinking
                pass

        # Determine status from file modification time
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            return None
        age = time.time() - mtime

        if age < 10:
            status = 'working'
        elif age < 45:
            status = 'thinking'
        else:
            status = 'idle'

        # Build display info
        if cwd:
            project_name = os.path.basename(cwd.replace('\\', '/'))
        else:
            project_name = project_dir_name

        if last_tool:
            output = last_tool[:50]
        elif last_text:
            output = last_text[:50]
        else:
            output = '等待输入 >'

        return {
            'id': os.path.basename(filepath).replace('.jsonl', '')[:8],
            'name': project_name,
            'path': (cwd or project_dir_name).replace('\\', '/'),
            'status': status,
            'output': output,
            'mtime': mtime,
        }

    def get_active_sessions(self):
        """Find all recently active Claude Code sessions"""
        now = time.time()
        sessions = []

        if not os.path.exists(self.projects_dir):
            return sessions

        try:
            dirs = os.listdir(self.projects_dir)
        except OSError:
            return sessions

        for project_dir_name in dirs:
            project_path = os.path.join(self.projects_dir, project_dir_name)
            if not os.path.isdir(project_path):
                continue

            try:
                files = os.listdir(project_path)
            except OSError:
                continue

            for fname in files:
                if not fname.endswith('.jsonl'):
                    continue
                filepath = os.path.join(project_path, fname)
                try:
                    mtime = os.path.getmtime(filepath)
                except OSError:
                    continue
                if now - mtime > self.MAX_AGE_SECONDS:
                    continue

                info = self._parse_session(filepath, project_dir_name)
                if info:
                    sessions.append(info)

        # Sort: working first, then thinking, then idle, then by recency
        status_order = {'working': 0, 'thinking': 1, 'idle': 2}
        sessions.sort(key=lambda s: (status_order.get(s['status'], 9), -s.get('mtime', 0)))
        return sessions[:self.MAX_SESSIONS]

    # ---- push to UI ----

    def _push(self, sessions):
        payload = json.dumps(sessions, ensure_ascii=False)
        if payload == self._prev_json:
            return  # No change, skip
        self._prev_json = payload
        try:
            self.window.evaluate_js(f'window.updateTasks({payload})')
        except Exception:
            pass

    # ---- background loop ----

    def start(self):
        def loop():
            while self.running:
                sessions = self.get_active_sessions()
                self._push(sessions)
                time.sleep(self.POLL_INTERVAL)

        t = threading.Thread(target=loop, daemon=True)
        t.start()

    def stop(self):
        self.running = False


def main():
    api = WidgetAPI()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(script_dir, 'widget.html')

    if not os.path.exists(html_path):
        print(f"Error: {html_path} not found")
        sys.exit(1)

    window = webview.create_window(
        'ClaudeWatch',
        html_path,
        width=320,
        height=100,
        x=50,
        y=50,
        frameless=True,
        transparent=True,
        background_color='#000000',
        on_top=True,
        js_api=api,
        min_size=(280, 80),
    )
    window._on_top = True

    api.set_window(window)

    monitor = StatusMonitor(window)

    # Start monitor after webview is ready
    def on_loaded():
        monitor.start()
        # Get native window handle and apply rounded corners
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "ClaudeWatch")
            if hwnd:
                api.set_hwnd(hwnd)
                api._apply_rounded_corners(320, 300)
        except Exception:
            pass

    window.events.loaded += on_loaded

    try:
        webview.start(debug=False, gui='edgechromium')
    finally:
        monitor.stop()


if __name__ == '__main__':
    main()
