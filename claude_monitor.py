#!/usr/bin/env python3
"""
Claude Code CLI 实时状态监控悬浮窗
动漫 HUD 风格 + 小人干活动画 + 精细化状态显示

快捷键: ESC 退出 | 鼠标拖动标题栏移动窗口 | 双击标题栏折叠/展开
         右键标题栏 → 置顶切换 / 退出
"""

import tkinter as tk
import json
import time
import threading
from pathlib import Path

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ── 配置 ──────────────────────────────────────────────────────
CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
REFRESH_MS = 2000
ANIM_MS = 400
WIN_WIDTH = 250

# ── 配色 (对齐 claude_status_widget.html 动漫 HUD) ────────────
C = {
    "bg":       "#060612",
    "surface":  "#0c0c1e",
    "card":     "#0e0e24",
    "border":   "#1a1a3a",
    "text":     "#e0e0f8",
    "dim":      "#3a3a5c",
    "accent":   "#00d4ff",
    "green":    "#00ff88",
    "yellow":   "#ffd700",
    "red":      "#ff4757",
    "pink":     "#ff6b9d",
    "cyan":     "#00d4ff",
    "purple":   "#b06cff",
}

# ── 小人干活动画帧 ──────────────────────────────────────────
WORKER_FRAMES = [
    " o/  \n/|\\  \n/ \\  ",
    " \\o  \n |\\  \n / \\ ",
    "  o  \n /|\\ \n / \\ ",
    " \\o/ \n  |  \n / \\ ",
]

SPIN = ["|", "/", "-", "\\"]

# ── 状态显示配置（对齐 HTML: 思考=pink 工作=green 待機=accent） ──
STATUS_CFG = {
    "thinking":   {"label": "THINKING",   "fg": C["pink"]},
    "generating": {"label": "GENERATING", "fg": C["cyan"]},
    "working":    {"label": "WORKING",    "fg": C["green"]},
    "idle":       {"label": "IDLE",       "fg": C["dim"]},
}


def _darken(hex_color, factor):
    """将十六进制颜色按比例变暗，用于脉动效果"""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


# ═══════════════════════════════════════════════════════════════
#  数据层
# ═══════════════════════════════════════════════════════════════

class Session:
    __slots__ = (
        "pid", "cwd", "project_name", "git_branch",
        "session_file", "status", "action", "action_type", "elapsed",
    )
    def __init__(self):
        self.pid = 0
        self.cwd = ""
        self.project_name = ""
        self.git_branch = ""
        self.session_file = None
        self.status = "idle"
        self.action = ""
        self.action_type = ""
        self.elapsed = ""


def find_claude_processes():
    """返回 [{pid, cwd}]"""
    result = []
    if not HAS_PSUTIL:
        return result
    try:
        for p in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            if p.info["name"] != "node.exe":
                continue
            cl = " ".join(p.info.get("cmdline") or [])
            if "claude-code" not in cl or "cli.js" not in cl:
                continue
            try:
                cwd = p.info.get("cwd", "") or ""
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                cwd = ""
            result.append({"pid": p.pid, "cwd": cwd})
    except Exception:
        pass
    return result


def _find_project_dir(cwd):
    if not cwd:
        return None
    encoded = cwd.replace(":", "-").replace("\\", "-")
    p = PROJECTS_DIR / encoded
    return p if p.is_dir() else None


def _find_session_for_pid(pid, cwd, used_files):
    proj_dir = _find_project_dir(cwd)
    if not proj_dir:
        return None
    try:
        p = psutil.Process(pid)
        for f in p.open_files():
            if f.path.endswith(".jsonl") and "projects" in f.path:
                fp = Path(f.path)
                if fp.exists() and str(fp) not in used_files:
                    return fp
    except Exception:
        pass
    now = time.time()
    candidates = []
    for jf in proj_dir.glob("*.jsonl"):
        if str(jf) in used_files:
            continue
        try:
            mt = jf.stat().st_mtime
            if (now - mt) < 7200:
                candidates.append((jf, mt))
        except OSError:
            pass
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0] if candidates else None


def _get_last_action(jsonl_path):
    """返回 (action_type, action_text)"""
    try:
        size = jsonl_path.stat().st_size
        if size == 0:
            return ("", "")
        tail = min(8000, size)
        with open(jsonl_path, "r", encoding="utf-8", errors="ignore") as f:
            if size > tail:
                f.seek(size - tail)
                f.readline()
            lines = f.readlines()
        if not lines:
            return ("", "")

        last_raw = lines[-1].strip()
        if last_raw and not last_raw.endswith("}"):
            return ("generating", "Generating response...")

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = d.get("type", "")

            if t == "progress":
                return ("tool", "Agent running...")

            if t == "assistant":
                msg = d.get("message", d)
                content = msg.get("content", [])
                if isinstance(content, list):
                    for blk in reversed(content):
                        if isinstance(blk, dict) and blk.get("type") == "tool_use":
                            return ("tool", _tool_label(blk.get("name", ""),
                                                       blk.get("input", {})))
                    for blk in reversed(content):
                        if isinstance(blk, dict) and blk.get("type") == "text":
                            txt = blk.get("text", "").strip()
                            if txt:
                                return ("text", txt.split("\n")[0][:80])
                return ("generating", "Generating response...")

            if t == "user":
                return ("thinking", "Thinking...")

        return ("", "")
    except Exception:
        return ("", "")


def _tool_label(name, inp):
    def _fn(p):
        try:
            return Path(p).name
        except Exception:
            return str(p)[:30]
    labels = {
        "Read":       lambda i: f"Reading {_fn(i.get('file_path',''))}",
        "Write":      lambda i: f"Writing {_fn(i.get('file_path',''))}",
        "Edit":       lambda i: f"Editing {_fn(i.get('file_path',''))}",
        "Bash":       lambda i: f"{i.get('description', i.get('command',''))[:55]}",
        "Grep":       lambda i: f"Searching \"{i.get('pattern','')[:30]}\"",
        "Glob":       lambda i: f"Finding {i.get('pattern','')[:30]}",
        "Agent":      lambda i: f"Agent: {i.get('description','')[:35]}",
        "WebSearch":  lambda i: f"Searching: {i.get('query','')[:35]}",
        "WebFetch":   lambda i: f"Fetching: {i.get('url','')[:35]}",
        "Skill":      lambda i: f"Skill: {i.get('skill','')}",
    }
    fn = labels.get(name)
    if fn:
        try:
            return fn(inp)
        except Exception:
            pass
    return name


def _read_git_branch(jsonl_path):
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="ignore") as f:
            chunk = f.read(3000)
        for line in chunk.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") == "user":
                return d.get("gitBranch", "")
    except Exception:
        pass
    return ""


def _fmt_time(seconds):
    s = int(seconds)
    if s < 0:
        return ""
    if s < 5:
        return "now"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h {(s % 3600) // 60}m"


# ═══════════════════════════════════════════════════════════════
#  UI 层
# ═══════════════════════════════════════════════════════════════

class Monitor:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.configure(bg=C["bg"])
        self.root.geometry(f"{WIN_WIDTH}x140+100+100")
        self.root.minsize(240, 60)

        self._drag = {"x": 0, "y": 0}
        self._file_sizes = {}
        self._frame = 0
        self._pulse_on = False
        self._anim_refs = []
        self._data_lock = threading.Lock()
        self._pending_sessions = None
        self._collapsed = False
        self._topmost = True
        self._last_sessions = []

        self._build()
        self._bind()
        self._tick()
        self._animate()
        self.root.mainloop()

    # ── 构建界面 ──

    def _build(self):
        outer = tk.Frame(self.root, bg=C["border"], padx=1, pady=1)
        outer.pack(fill=tk.BOTH, expand=True)
        self._body = tk.Frame(outer, bg=C["bg"])
        self._body.pack(fill=tk.BOTH, expand=True)

        # ── 顶栏 ──
        self._header = tk.Frame(self._body, bg=C["surface"], height=36)
        self._header.pack(fill=tk.X)
        self._header.pack_propagate(False)

        self._title_lbl = tk.Label(
            self._header, text=" ◆ Claude Code", bg=C["surface"],
            fg=C["accent"], font=("Segoe UI", 10, "bold"), anchor="w",
        )
        self._title_lbl.pack(side=tk.LEFT, fill=tk.Y)

        self._badge = tk.Label(
            self._header, text=" 0 ", bg=C["accent"], fg="#060612",
            font=("Consolas", 9, "bold"), padx=6,
        )
        self._badge.pack(side=tk.LEFT, padx=(8, 0))

        self._close_btn = tk.Label(
            self._header, text=" x ", bg=C["surface"], fg=C["dim"],
            font=("Consolas", 11, "bold"), cursor="hand2",
        )
        self._close_btn.pack(side=tk.RIGHT, padx=(0, 4), fill=tk.Y)
        self._close_btn.bind("<ButtonPress-1>", lambda e: self.root.destroy())
        self._close_btn.bind("<Enter>",
            lambda e: self._close_btn.config(fg=C["red"], bg=C["card"]))
        self._close_btn.bind("<Leave>",
            lambda e: self._close_btn.config(fg=C["dim"], bg=C["surface"]))

        # ── 内容区 ──
        self._canvas = tk.Canvas(self._body, bg=C["bg"],
                                 highlightthickness=0, height=0)
        self._inner = tk.Frame(self._canvas, bg=C["bg"])
        self._inner.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._cwin = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._canvas.pack(fill=tk.BOTH, expand=True)

        # ── 底栏 ──
        self._footer = tk.Frame(self._body, bg=C["surface"], height=20)
        self._footer.pack(fill=tk.X)
        self._footer.pack_propagate(False)

        self._ft_left = tk.Label(
            self._footer, text="", bg=C["surface"], fg=C["dim"],
            font=("Consolas", 8), anchor="w",
        )
        self._ft_left.pack(side=tk.LEFT, padx=6, fill=tk.Y)

        self._ft_right = tk.Label(
            self._footer, text="", bg=C["surface"], fg=C["dim"],
            font=("Consolas", 8), anchor="e",
        )
        self._ft_right.pack(side=tk.RIGHT, padx=6, fill=tk.Y)

    # ── 事件绑定 ──

    def _bind(self):
        for w in (self._header, self._title_lbl, self._badge):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<Double-Button-1>", self._toggle_collapse)
        self._canvas.bind("<MouseWheel>",
            lambda e: self._canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<Configure>", self._on_resize)

        # 右键菜单
        self._menu = tk.Menu(self.root, tearoff=0, bg=C["surface"],
                             fg=C["text"], activebackground=C["accent"],
                             activeforeground="#060612",
                             font=("Segoe UI", 9))
        self._menu.add_command(label="置顶切换", command=self._toggle_topmost)
        self._menu.add_separator()
        self._menu.add_command(label="退出", command=self.root.destroy)
        for w in (self._header, self._title_lbl, self._badge):
            w.bind("<ButtonPress-3>", self._show_menu)

    def _show_menu(self, e):
        self._menu.tk_popup(e.x_root, e.y_root)

    def _toggle_topmost(self):
        self._topmost = not self._topmost
        self.root.attributes("-topmost", self._topmost)

    def _toggle_collapse(self, e=None):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._canvas.pack_forget()
            self._footer.pack_forget()
            self.root.geometry(f"{WIN_WIDTH}x38")
        else:
            self._footer.pack(fill=tk.X, side=tk.BOTTOM)
            self._canvas.pack(fill=tk.BOTH, expand=True)
            if self._last_sessions:
                self._render(self._last_sessions)
            else:
                self.root.geometry(f"{WIN_WIDTH}x140")

    def _drag_start(self, e):
        self._drag["x"] = e.x_root - self.root.winfo_x()
        self._drag["y"] = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        x = e.x_root - self._drag["x"]
        y = e.y_root - self._drag["y"]
        self.root.geometry(f"+{x}+{y}")

    def _on_resize(self, e):
        if e.widget is self.root:
            self._canvas.itemconfig(self._cwin, width=e.width - 4)

    # ── 文件活跃检测 ──

    def _check_file_growing(self, filepath):
        key = str(filepath)
        try:
            cur_size = filepath.stat().st_size
        except OSError:
            self._file_sizes.pop(key, None)
            return False
        prev = self._file_sizes.get(key)
        self._file_sizes[key] = cur_size
        if prev is None:
            return False
        return cur_size > prev

    # ── 动画循环 (400ms) ──

    def _animate(self):
        self._frame = (self._frame + 1) % len(WORKER_FRAMES)
        self._pulse_on = not self._pulse_on

        for ref in self._anim_refs:
            st = ref["status"]
            w = ref["worker"]
            action_lbl = ref.get("action")
            if w is None:
                continue

            # 颜色脉动：亮 ↔ 暗 交替
            if st == "thinking":
                w.config(text=WORKER_FRAMES[self._frame])
                pulse_fg = C["pink"] if self._pulse_on else _darken(C["pink"], 0.5)
            elif st == "generating":
                w.config(text=WORKER_FRAMES[(self._frame + 2) % len(WORKER_FRAMES)])
                pulse_fg = C["cyan"] if self._pulse_on else _darken(C["cyan"], 0.5)
            elif st == "working":
                w.config(text=SPIN[self._frame % len(SPIN)])
                pulse_fg = C["green"] if self._pulse_on else _darken(C["green"], 0.5)
            else:
                w.config(text="  ")
                pulse_fg = C["dim"]

            w.config(fg=pulse_fg)
            if action_lbl:
                action_lbl.config(fg=pulse_fg)

        self.root.after(ANIM_MS, self._animate)

    # ── 数据刷新 (2000ms) ──

    def _tick(self):
        """启动后台数据采集，同时检查是否有待渲染的数据"""
        with self._data_lock:
            sessions = self._pending_sessions
            self._pending_sessions = None

        if sessions is not None:
            self._render(sessions)

        threading.Thread(target=self._collect, daemon=True).start()
        self.root.after(REFRESH_MS, self._tick)

    def _collect(self):
        """后台线程：执行耗时的 psutil 和文件 IO 操作"""
        try:
            procs = find_claude_processes()
            now = time.time()
            used_files = set()
            sessions = []

            for proc in procs:
                s = Session()
                s.pid = proc["pid"]
                s.cwd = proc.get("cwd", "")
                s.project_name = Path(s.cwd).name if s.cwd else "unknown"

                sf = _find_session_for_pid(s.pid, s.cwd, used_files)
                if sf:
                    used_files.add(str(sf))
                    s.session_file = sf

                    is_growing = self._check_file_growing(sf)
                    file_mtime = sf.stat().st_mtime
                    age = now - file_mtime
                    s.elapsed = _fmt_time(age)

                    s.git_branch = _read_git_branch(sf)

                    action_type, action_text = _get_last_action(sf)
                    s.action_type = action_type
                    s.action = action_text

                    # ── 精细化状态判定 ──
                    if is_growing or age < 15:
                        if action_type == "thinking":
                            s.status = "thinking"
                        elif action_type == "tool":
                            s.status = "working"
                        else:
                            s.status = "generating"
                    elif action_type == "text" or not action_type:
                        s.status = "idle"
                        s.action = "Ready for input"
                    elif action_type == "thinking":
                        s.status = "thinking"
                    elif action_type == "tool" and age < 60:
                        s.status = "working"
                    else:
                        s.status = "idle"
                        s.action = "Ready for input"
                else:
                    s.status = "idle"
                    s.action = "No session file"
                    s.elapsed = "-"

                sessions.append(s)

            with self._data_lock:
                self._pending_sessions = sessions
        except Exception:
            pass

    def _render(self, sessions):
        """主线程：用采集到的数据更新 UI"""
        self._last_sessions = sessions
        self._anim_refs.clear()
        self._badge.config(text=f" {len(sessions)} ")
        for w in self._inner.winfo_children():
            w.destroy()

        if not sessions:
            self._render_empty()
        else:
            for s in sessions:
                self._render_card(s)

        pids_text = ", ".join(str(s.pid) for s in sessions) or "-"
        self._ft_left.config(text=f"PIDs: {pids_text}")
        self._ft_right.config(text=time.strftime("%H:%M:%S"))

        # 动态高度（和原始版本完全一致的逻辑）
        if sessions:
            total = sum(
                95 if s.status in ("thinking", "generating") else 65
                for s in sessions
            )
            h = 36 + total + 20 + 4
        else:
            h = 140
        h = min(h, 600)
        if not self._collapsed:
            self.root.geometry(f"{WIN_WIDTH}x{h}")

    # ── 渲染空状态 ──

    def _render_empty(self):
        f = tk.Frame(self._inner, bg=C["bg"])
        f.pack(fill=tk.X, padx=20, pady=22)
        tk.Label(f, text="No Claude Code running",
                 bg=C["bg"], fg=C["dim"], font=("Segoe UI", 11)).pack()

    # ── 渲染会话卡片（保持原始两行布局，仅更新配色和脉动） ──

    def _render_card(self, s: Session):
        cfg = STATUS_CFG.get(s.status, STATUS_CFG["idle"])
        fg = cfg["fg"]
        is_active = s.status != "idle"
        show_worker = s.status in ("thinking", "generating")

        # ── 卡片边框 ──
        border_c = fg if is_active else C["border"]
        card = tk.Frame(self._inner, bg=C["card"],
                        highlightbackground=border_c, highlightthickness=1)
        card.pack(fill=tk.X, padx=6, pady=3, ipady=2)

        # 顶部状态色条（活跃时显示）
        tk.Frame(card, bg=fg if is_active else C["border"], height=2).pack(fill=tk.X)

        # ── 上行: 状态灯 + 标签 + 项目 + 分支 + 时间 ──
        top = tk.Frame(card, bg=C["card"])
        top.pack(fill=tk.X, padx=10, pady=(6, 0))

        tk.Label(top, text="●", bg=C["card"], fg=fg,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        tk.Label(top, text=f" {cfg['label']}", bg=C["card"], fg=fg,
                 font=("Consolas", 8, "bold")).pack(side=tk.LEFT)
        tk.Label(top, text=s.project_name, bg=C["card"], fg=C["text"],
                 font=("Segoe UI", 10, "bold"), anchor="w"
                 ).pack(side=tk.LEFT, padx=(8, 0))

        if s.git_branch:
            br = s.git_branch if len(s.git_branch) <= 18 else "..." + s.git_branch[-15:]
            tk.Label(top, text=br, bg=C["surface"], fg=C["accent"],
                     font=("Consolas", 8), padx=4).pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(top, text=s.elapsed, bg=C["card"], fg=C["dim"],
                 font=("Consolas", 9)).pack(side=tk.RIGHT)

        # ── 下行: 小人动画 + 操作描述 ──
        bot = tk.Frame(card, bg=C["card"])
        bot.pack(fill=tk.X, padx=10, pady=(4, 6))

        if show_worker:
            # 小人干活动画 (3 行)
            worker_lbl = tk.Label(
                bot, text="  ", bg=C["card"], fg=fg,
                font=("Consolas", 9), anchor="nw", justify="left",
            )
            worker_lbl.pack(side=tk.LEFT, padx=(0, 6))

            action_lbl = tk.Label(
                bot, text=s.action, bg=C["card"], fg=fg,
                font=("Consolas", 9), anchor="nw", justify="left",
                wraplength=140,
            )
            action_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

            self._anim_refs.append({
                "status": s.status,
                "worker": worker_lbl,
                "action": action_lbl,
            })

        elif s.status == "working":
            # Spinner + 工具操作
            worker_lbl = tk.Label(
                bot, text=">", bg=C["card"], fg=fg,
                font=("Consolas", 10, "bold"),
            )
            worker_lbl.pack(side=tk.LEFT)

            action_lbl = tk.Label(
                bot, text=f" {s.action}", bg=C["card"], fg=fg,
                font=("Consolas", 9), anchor="w",
                wraplength=170, justify="left",
            )
            action_lbl.pack(side=tk.LEFT, fill=tk.X)

            self._anim_refs.append({
                "status": s.status,
                "worker": worker_lbl,
                "action": action_lbl,
            })

        else:
            # IDLE - 暗淡显示
            tk.Label(
                bot, text=f"  {s.action}", bg=C["card"], fg=C["dim"],
                font=("Consolas", 9), anchor="w",
            ).pack(side=tk.LEFT, fill=tk.X)

            self._anim_refs.append({
                "status": s.status,
                "worker": None,
            })


# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    Monitor()
