#!/usr/bin/env python3
# steamcmd_mod_manager.py
"""
SteamCMD Workshop Mod Manager — Windows & Linux GUI wrapper (PyQt6 + QtWebEngine).

Deps:  pip install PyQt6 PyQt6-WebEngine
Run:   python steamcmd_mod_manager.py      (Windows: py steamcmd_mod_manager.py)

The UI follows the system Qt palette by default. Open Settings to switch
between System / Dark / Light / Steam color schemes (more options later).

If the embedded browser stays blank on Linux (e.g. running as root), uncomment:
# os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal, QTimer, QSize, QEvent
from PyQt6.QtGui import (QAction, QDesktopServices, QColor, QIcon, QPainter,
                         QPen, QPixmap, QPalette)
from PyQt6.QtWebEngineWidgets import QWebEngineView  # import before QApplication
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QToolBar,
    QVBoxLayout, QWidget,
)

IS_WIN = os.name == "nt"


def config_dir():
    if IS_WIN:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "steamcmd-mod-manager"
    return Path.home() / ".config" / "steamcmd-mod-manager"


APP_DIR = config_dir()
CFG_PATH = APP_DIR / "config.json"
WORKSHOP_URL = "https://steamcommunity.com/app/{app_id}/workshop/"
ITEM_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={item_id}"
API_URL = "https://api.steampowered.com/ISteamRemoteStorage/{method}/v1/"

MOD_COLORS = {
    "red":     ("Red",     "#ff4655"),
    "orange":  ("Orange",  "#ff8a3d"),
    "amber":   ("Amber",   "#ffc233"),
    "green":   ("Green",   "#8bd450"),
    "cyan":    ("Cyan",    "#35d0c5"),
    "blue":    ("Blue",    "#3da5ff"),
    "violet":  ("Violet",  "#9d6bff"),
    "magenta": ("Magenta", "#ff4fa0"),
}
COLOR_ORDER = list(MOD_COLORS)

SORT_MODES = [
    ("name_asc",  "Name A–Z",      lambda i, m: (m.get("title") or i).lower(), False),
    ("name_desc", "Name Z–A",      lambda i, m: (m.get("title") or i).lower(), True),
    ("color",     "Color",         lambda i, m: (COLOR_ORDER.index(m["color"])
                                                 if m.get("color") in COLOR_ORDER
                                                 else len(COLOR_ORDER),
                                                 (m.get("title") or i).lower()), False),
    ("newest",    "Newest first",  lambda i, m: m.get("installed_at", 0), True),
    ("oldest",    "Oldest first",  lambda i, m: m.get("installed_at", 0), False),
    ("id",        "Workshop ID",   lambda i, m: int(i), False),
    ("status",    "Enabled first", lambda i, m: (not m.get("enabled", True),
                                                 (m.get("title") or i).lower()), False),
]


# ---------------------------------------------------------------- theming ---
def mix(a, b, t):
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def compute_theme(pal):
    window = pal.color(QPalette.ColorRole.Window)
    base = pal.color(QPalette.ColorRole.Base)
    text = pal.color(QPalette.ColorRole.WindowText)
    button = pal.color(QPalette.ColorRole.Button)
    accent = pal.color(QPalette.ColorRole.Highlight)
    accent_text = pal.color(QPalette.ColorRole.HighlightedText)
    is_dark = window.lightness() < 128
    if is_dark:
        info, success, warning, danger, violet = "#6cb6ff", "#7fd48a", "#f0b429", "#ff6b6b", "#c792ea"
    else:
        info, success, warning, danger, violet = "#1a66c9", "#1f7a33", "#a86400", "#c0392b", "#7a3fc0"
    return {
        "window": window.name(), "base": base.name(), "text": text.name(),
        "button": button.name(), "accent": accent.name(), "accent_text": accent_text.name(),
        "panel": mix(window, base, 0.60).name(),
        "border": mix(button, text, 0.30).name(),
        "border_strong": mix(button, text, 0.50).name(),
        "subtle": mix(text, window, 0.45).name(),
        "hover": mix(button, accent, 0.14).name(),
        "pressed": mix(button, text, 0.10).name(),
        "is_dark": is_dark,
        "info": info, "success": success, "warning": warning,
        "danger": danger, "violet": violet,
    }


def make_palette(kind):
    if kind == "dark":
        c = dict(window="#202225", base="#191b1d", text="#dce0e4", button="#2b2e32",
                 accent="#2a82da", accent_text="#ffffff", alt="#26292d")
    elif kind == "light":
        c = dict(window="#eff0f1", base="#ffffff", text="#232629", button="#f7f7f8",
                 accent="#308cc6", accent_text="#ffffff", alt="#f2f3f4")
    else:  # steam
        c = dict(window="#0c131c", base="#0d1723", text="#d7e4ef", button="#182634",
                 accent="#66c0f4", accent_text="#0c131c", alt="#101a26")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: c["window"],
        QPalette.ColorRole.WindowText: c["text"],
        QPalette.ColorRole.Base: c["base"],
        QPalette.ColorRole.AlternateBase: c["alt"],
        QPalette.ColorRole.Text: c["text"],
        QPalette.ColorRole.Button: c["button"],
        QPalette.ColorRole.ButtonText: c["text"],
        QPalette.ColorRole.Highlight: c["accent"],
        QPalette.ColorRole.HighlightedText: c["accent_text"],
        QPalette.ColorRole.ToolTipBase: c["base"],
        QPalette.ColorRole.ToolTipText: c["text"],
        QPalette.ColorRole.Link: c["accent"],
        QPalette.ColorRole.PlaceholderText: mix(QColor(c["text"]), QColor(c["window"]), 0.5).name(),
        QPalette.ColorRole.Mid: mix(QColor(c["button"]), QColor(c["text"]), 0.3).name(),
        QPalette.ColorRole.Dark: mix(QColor(c["window"]), QColor("#000000"), 0.2).name(),
        QPalette.ColorRole.Light: mix(QColor(c["button"]), QColor("#ffffff"), 0.15).name(),
        QPalette.ColorRole.Shadow: "#000000",
        QPalette.ColorRole.Midlight: mix(QColor(c["button"]), QColor("#ffffff"), 0.08).name(),
    }
    for role, val in roles.items():
        pal.setColor(role, QColor(val))
    dis = mix(QColor(c["text"]), QColor(c["window"]), 0.55)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, dis)
    return pal


QSS_TEMPLATE = """
QWidget { color: %(text)s; }
QMainWindow, QDialog { background: %(window)s; }
QToolTip { background: %(base)s; color: %(text)s; border: 1px solid %(border_strong)s;
           padding: 4px 8px; }
QToolBar { background: %(panel)s; border: none; border-bottom: 1px solid %(border)s;
           padding: 5px 8px; spacing: 6px; }
QToolBar QLabel { color: %(subtle)s; }
QToolBar QToolButton { background: %(button)s; border: 1px solid %(border)s;
                       border-radius: 6px; padding: 5px 11px; color: %(text)s; }
QToolBar QToolButton:hover { background: %(hover)s; border-color: %(accent)s; }
QToolBar QToolButton:pressed { background: %(pressed)s; }
QToolBar::separator { background: %(border)s; width: 1px; margin: 4px 6px; }
QStatusBar { background: %(panel)s; border-top: 1px solid %(border)s; color: %(subtle)s; }
QFrame#panel { background: %(panel)s; border: 1px solid %(border)s; border-radius: 10px; }
QLabel#panelTitle { color: %(accent)s; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel#dimLabel { color: %(subtle)s; font-size: 11px; }
QLineEdit, QComboBox, QPlainTextEdit, QListWidget {
    background: %(base)s; border: 1px solid %(border)s; border-radius: 6px;
    padding: 4px 8px; selection-background-color: %(accent)s;
    selection-color: %(accent_text)s; color: %(text)s; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QListWidget:focus {
    border-color: %(accent)s; }
QLineEdit#urlBar { background: %(base)s; border-color: %(border_strong)s; padding: 5px 10px; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 6px solid %(accent)s; margin-right: 8px; }
QComboBox QAbstractItemView { background: %(base)s; border: 1px solid %(border_strong)s;
    selection-background-color: %(accent)s; selection-color: %(accent_text)s; }
QPushButton { background: %(button)s; border: 1px solid %(border_strong)s; border-radius: 6px;
              padding: 5px 12px; color: %(text)s; }
QPushButton:hover { background: %(hover)s; border-color: %(accent)s; }
QPushButton:pressed { background: %(pressed)s; }
QPushButton:disabled { color: %(subtle)s; border-color: %(border)s; }
QPushButton#navBack, QPushButton#navFwd { background: %(info_bg)s;
    border: 1px solid %(info_bd)s; color: %(info)s; font-weight: 700; font-size: 15px; }
QPushButton#navBack:hover, QPushButton#navFwd:hover { background: %(info_bg_hover)s;
    border-color: %(info)s; }
QPushButton#navBack:disabled, QPushButton#navFwd:disabled { color: %(subtle)s;
    border-color: %(border)s; background: %(button)s; }
QPushButton#navReload { background: %(warning_bg)s; border: 1px solid %(warning_bd)s;
    color: %(warning)s; font-weight: 700; font-size: 13px; }
QPushButton#navReload:hover { background: %(warning_bg_hover)s; border-color: %(warning)s; }
QPushButton#navReload[loading="true"] { background: %(danger_bg)s;
    border-color: %(danger_bd)s; color: %(danger)s; }
QPushButton#navHome { background: %(button)s; border: 1px solid %(border_strong)s;
    color: %(subtle)s; font-weight: 700; font-size: 15px; }
QPushButton#navHome:hover { border-color: %(accent)s; color: %(text)s; }
QPushButton#copyBtn { background: %(violet_bg)s; border: 1px solid %(violet_bd)s; color: %(violet)s; }
QPushButton#copyBtn:hover { background: %(violet_bg_hover)s; border-color: %(violet)s; }
QPushButton#dlBtn { background: %(success_bg)s; border: 1px solid %(success_bd)s;
    color: %(success)s; font-weight: 700; }
QPushButton#dlBtn:hover { background: %(success_bg_hover)s; border-color: %(success)s; }
QPushButton#dlBtn:disabled { color: %(subtle)s; border-color: %(border)s; background: %(button)s; }
QPushButton#dangerBtn { background: %(danger_bg)s; border: 1px solid %(danger_bd)s; color: %(danger)s; }
QPushButton#dangerBtn:hover { background: %(danger_bg_hover)s; border-color: %(danger)s; }
QListWidget { padding: 3px; }
QListWidget::item { border: 1px solid transparent; border-bottom: 1px solid %(border)s;
    border-radius: 6px; margin: 1px 2px; }
QListWidget::item:hover { background: %(hover)s; border: 1px solid %(border_strong)s; }
QListWidget::item:selected { background: %(accent_sel)s; border: 1px solid %(accent)s; }
QPlainTextEdit#log { font-family: "JetBrains Mono", "Fira Mono", "Consolas", monospace;
    font-size: 12px; background: %(base)s; color: %(text)s; border: 1px solid %(border)s; }
QFrame#webFrame { background: %(base)s; border: 1px solid %(border)s; border-radius: 8px; }
QMenu { background: %(base)s; border: 1px solid %(border_strong)s; border-radius: 8px; padding: 5px; }
QMenu::item { padding: 6px 26px 6px 12px; border-radius: 5px; color: %(text)s; }
QMenu::item:selected { background: %(accent)s; color: %(accent_text)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 5px 8px; }
QCheckBox { spacing: 8px; color: %(text)s; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid %(border_strong)s;
    border-radius: 4px; background: %(base)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }
QCheckBox::indicator:hover { border-color: %(accent)s; }
QSplitter::handle { background: %(window)s; }
QSplitter::handle:horizontal { width: 7px; }
QSplitter::handle:vertical { height: 7px; }
QSplitter::handle:hover { background: %(border_strong)s; }
QScrollBar:vertical { background: %(window)s; width: 10px; margin: 2px; border-radius: 5px; }
QScrollBar::handle:vertical { background: %(border_strong)s; min-height: 26px; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: %(accent)s; }
QScrollBar:horizontal { background: %(window)s; height: 10px; margin: 2px; border-radius: 5px; }
QScrollBar::handle:horizontal { background: %(border_strong)s; min-width: 26px; border-radius: 5px; }
QScrollBar::handle:horizontal:hover { background: %(accent)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QDialogButtonBox QPushButton { min-width: 80px; }
"""


def build_qss(tc):
    win = QColor(tc["window"])
    d = dict(tc)
    for name in ("info", "success", "warning", "danger", "violet"):
        col = QColor(tc[name])
        d[name + "_bg"] = mix(win, col, 0.10).name()
        d[name + "_bg_hover"] = mix(win, col, 0.18).name()
        d[name + "_bd"] = mix(win, col, 0.55).name()
    d["accent_sel"] = mix(win, QColor(tc["accent"]), 0.18).name()
    return QSS_TEMPLATE % d


# ---------------------------------------------------------------- config ----
def load_cfg():
    if CFG_PATH.is_file():
        try:
            cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
            cfg.setdefault("color_scheme", "system")
            return cfg
        except Exception:
            pass
    return {"steamcmd": "", "last_profile": "", "sort_mode": "name_asc",
            "color_scheme": "system", "profiles": []}


def save_cfg(cfg):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CFG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def detect_steamcmd():
    w = shutil.which("steamcmd")
    if w:
        return w
    if IS_WIN:
        candidates = [
            Path.cwd() / "steamcmd.exe",
            Path.home() / "steamcmd" / "steamcmd.exe",
            Path("C:/steamcmd/steamcmd.exe"),
            Path("D:/steamcmd/steamcmd.exe"),
            Path("C:/ProgramData/chocolatey/bin/steamcmd.exe"),
            Path.home() / "scoop" / "shims" / "steamcmd.exe",
        ]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "steamcmd" / "steamcmd.exe")
    else:
        candidates = [
            Path("~/steamcmd/steamcmd.sh").expanduser(),
            Path("~/.local/share/Steam/steamcmd/steamcmd.sh").expanduser(),
            Path("~/.steam/steamcmd/steamcmd.sh").expanduser(),
            Path("/usr/games/steamcmd"), Path("/usr/bin/steamcmd"),
            Path("/opt/steamcmd/steamcmd.sh"),
        ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return ""


def hide_dir(path):
    """Mark .disabled as hidden on Windows (no-op elsewhere)."""
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass


# ------------------------------------------------------------- steam api ----
def api_post(method, fields):
    req = urllib.request.Request(API_URL.format(method=method),
                                 data=urllib.parse.urlencode(fields).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_details(ids):
    """Public endpoint, no API key. Returns {id: details} incl. time_updated."""
    out = {}
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        fields = {"itemcount": len(chunk)}
        for i, x in enumerate(chunk):
            fields[f"publishedfileids[{i}]"] = str(x)
        resp = api_post("GetPublishedFileDetails", fields)["response"]
        for d in resp.get("publishedfiledetails", []):
            out[str(d.get("publishedfileid"))] = d
    return out


def fetch_collection_children(cid):
    resp = api_post("GetCollectionDetails",
                    {"collectioncount": 1, "publishedfileids[0]": str(cid)})["response"]
    det = (resp.get("collectiondetails") or [{}])[0]
    if det.get("result") != 1:
        return []
    return [str(c["publishedfileid"]) for c in det.get("children", [])]


# ------------------------------------------------- existing-folder import ---
def detect_workshop_id(d):
    """Best-effort workshop id: numeric name, RimWorld About files, Arma meta.cpp."""
    if d.name.isdigit():
        return d.name
    try:
        pf = d / "About" / "PublishedFileId.txt"
        if pf.is_file():
            t = pf.read_text(encoding="utf-8", errors="ignore").strip()
            if t.isdigit() and t != "0":
                return t
        ab = d / "About" / "About.xml"
        if ab.is_file():
            m = re.search(r"<publishedFileId>\s*(\d+)\s*</publishedFileId>",
                          ab.read_text(encoding="utf-8", errors="ignore"), re.I)
            if m and m.group(1) != "0":
                return m.group(1)
        meta = d / "meta.cpp"
        if meta.is_file():
            m = re.search(r"publishedid\s*=\s*(\d+)",
                          meta.read_text(encoding="utf-8", errors="ignore"), re.I)
            if m and m.group(1) != "0":
                return m.group(1)
    except OSError:
        pass
    return None


def scan_mods_folder(mods_dir):
    base = Path(mods_dir)
    found, skipped = {}, 0
    if not base.is_dir():
        return found, skipped

    def consider(d, enabled):
        nonlocal skipped
        iid = detect_workshop_id(d)
        if iid:
            found.setdefault(iid, {"name": d.name, "path": str(d), "enabled": enabled})
        else:
            skipped += 1

    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            consider(d, True)
    dis = base / ".disabled"
    if dis.is_dir():
        for d in sorted(dis.iterdir()):
            if d.is_dir():
                consider(d, False)
    return found, skipped


def color_icon(hexstr):
    pm = QPixmap(14, 14)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(hexstr))
    p.setPen(QPen(QColor(0, 0, 0, 90), 1))
    p.drawEllipse(1, 1, 12, 12)
    p.end()
    return QIcon(pm)


class FnThread(QThread):
    """Run a blocking function off the UI thread."""
    ok = pyqtSignal(object)
    err = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.ok.emit(self._fn())
        except Exception as e:
            self.err.emit(str(e))


class WorkshopView(QWebEngineView):
    """QWebEngineView that follows target=_blank links in the same view.

    Steam's 'Required Items' links open in a new window/tab; the stock
    QWebEngineView.createWindow() returns None, so those clicks silently do
    nothing. We return a temporary view and redirect its first navigation
    back into this one.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._popups = []

    def createWindow(self, wtype):
        popup = QWebEngineView()
        self._popups.append(popup)

        def redirect(url):
            if url.isValid() and not url.isEmpty():
                self.setUrl(url)
            if popup in self._popups:
                self._popups.remove(popup)
            popup.deleteLater()

        popup.urlChanged.connect(redirect)
        return popup


# ---------------------------------------------------------- steamcmd worker --
class SteamWorker(QThread):
    """Sequential job queue: steamcmd downloads, then copy into mods folder."""
    log = pyqtSignal(str)
    item_done = pyqtSignal(str, str, bool, object)  # profile, item_id, ok, meta

    def __init__(self):
        super().__init__()
        self.jobs = queue.Queue()
        self._proc = None
        self._stop = False
        self._cancel_current = False

    def enqueue(self, job):
        self.jobs.put(job)

    def cancel(self):
        self._cancel_current = True
        try:
            while True:
                self.jobs.get_nowait()
        except queue.Empty:
            pass
        p = self._proc
        if p and p.poll() is None:
            p.terminate()

    def shutdown(self):
        self._stop = True
        self.cancel()
        self.jobs.put(None)

    def run(self):
        while not self._stop:
            job = self.jobs.get()
            if job is None:
                break
            try:
                self._process(job)
            except Exception as e:
                self.log.emit(f"[{job['item_id']}] internal error: {e}")
                self.item_done.emit(job["profile"], job["item_id"], False, job.get("meta"))

    def _process(self, job):
        item = job["item_id"]
        self._cancel_current = False
        ok, src = False, None
        for attempt in range(1, 4):
            if self._stop or self._cancel_current:
                break
            self.log.emit(f"--- downloading {item} (attempt {attempt}/3) ---")
            ok, src = self._run_once(job)
            if ok:
                break
            if attempt < 3 and not (self._stop or self._cancel_current):
                self.log.emit(f"[{item}] failed, retrying in 5 s…")
                time.sleep(5)
        if not ok or not src:
            self.log.emit(f"[{item}] FAILED.")
            self.item_done.emit(job["profile"], item, False, job.get("meta"))
            return
        dst = Path(job["mods_dir"]) / item
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.rmtree(dst)
            self.log.emit(f"[{item}] copying to {dst} …")
            shutil.copytree(src, dst)
        except Exception as e:
            self.log.emit(f"[{item}] copy failed: {e}")
            self.item_done.emit(job["profile"], item, False, job.get("meta"))
            return
        self.log.emit(f"[{item}] installed.")
        self.item_done.emit(job["profile"], item, True, job.get("meta"))

    def _run_once(self, job):
        args = [job["steamcmd"]]
        if job.get("force_windows"):
            args += ["+@sSteamCmdForcePlatformType", "windows"]
        if job.get("username") and not job.get("password"):
            args += ["+@NoPromptForPassword", "1"]  # rely on cached login
        if job.get("username"):
            args += ["+login", job["username"]]
            if job.get("password"):
                args += [job["password"]]
        else:
            args += ["+login", "anonymous"]
        args += ["+workshop_download_item", str(job["app_id"]), str(job["item_id"]), "+quit"]

        out = []
        try:
            self._proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in self._proc.stdout:
                out.append(line)
                self.log.emit(line.rstrip())
            self._proc.wait()
        except Exception as e:
            self.log.emit(f"steamcmd error: {e}")
            return False, None
        finally:
            self._proc = None

        text = "".join(out)
        ok = f"Success. Downloaded item {job['item_id']}" in text
        m = re.search(rf'Downloaded item {job["item_id"]} to "([^"]+)"', text)
        src = m.group(1) if m else None
        if not src:
            bases = [Path(job["steamcmd"]).parent]
            if not IS_WIN:
                bases += [Path.home() / ".local/share/Steam/steamcmd", Path.home() / "Steam"]
            for base in bases:
                c = base / "steamapps/workshop/content" / str(job["app_id"]) / str(job["item_id"])
                if c.is_dir():
                    src = str(c)
                    break
        return (ok and src is not None), src


# ---------------------------------------------------------- profile dialog --
class ProfileDialog(QDialog):
    def __init__(self, parent, data=None, taken=()):
        super().__init__(parent)
        self.setWindowTitle("Game profile")
        self.taken = set(taken)
        d = data or {}
        f = QFormLayout(self)
        self.name = QLineEdit(d.get("name", ""))
        self.appid = QLineEdit(str(d.get("app_id", "")))
        self.appid.setPlaceholderText("e.g. 294100 for RimWorld")
        self.mods_dir = QLineEdit(d.get("mods_dir", ""))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.mods_dir)
        h.addWidget(browse)
        self.user = QLineEdit(d.get("username", ""))
        self.pw = QLineEdit(d.get("password", ""))
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.force = QCheckBox("Force Windows platform (needed for some games' mods)")
        self.force.setChecked(bool(d.get("force_windows", False)))
        f.addRow("Profile name:", self.name)
        f.addRow("Steam App ID:", self.appid)
        f.addRow("Mods folder:", row)
        f.addRow("Steam login (optional):", self.user)
        f.addRow("Password:", self.pw)
        f.addRow("", self.force)
        note = QLabel("Empty login = anonymous (works for most games' workshops).\n"
                      "If a game requires an account, the password is stored in plain\n"
                      "text in the config file. For Steam Guard, log in once in a terminal.\n"
                      "If the mods folder already contains mods, you will be offered\n"
                      "to import and re-download them.")
        note.setWordWrap(True)
        note.setObjectName("dimLabel")
        f.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose mods folder",
                                             self.mods_dir.text() or str(Path.home()))
        if d:
            self.mods_dir.setText(d)

    def accept(self):
        if not self.name.text().strip() or self.name.text().strip() in self.taken:
            QMessageBox.warning(self, "Profile", "Enter a unique profile name.")
            return
        if not self.appid.text().strip().isdigit():
            QMessageBox.warning(self, "Profile", "App ID must be a number.")
            return
        if not self.mods_dir.text().strip():
            QMessageBox.warning(self, "Profile", "Choose a mods folder.")
            return
        super().accept()

    def values(self):
        return {"name": self.name.text().strip(),
                "app_id": self.appid.text().strip(),
                "mods_dir": self.mods_dir.text().strip(),
                "username": self.user.text().strip(),
                "password": self.pw.text(),
                "force_windows": self.force.isChecked()}


# ----------------------------------------------------------- settings dialog -
class SettingsDialog(QDialog):
    def __init__(self, parent, current):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        head = QLabel("APPEARANCE")
        head.setObjectName("panelTitle")
        lay.addWidget(head)
        form = QFormLayout()
        self.scheme = QComboBox()
        for key, label in (("system", "System (default)"), ("dark", "Dark"),
                           ("light", "Light"), ("steam", "Steam Dark")):
            self.scheme.addItem(label, key)
        idx = next((i for i in range(self.scheme.count())
                    if self.scheme.itemData(i) == current), 0)
        self.scheme.setCurrentIndex(idx)
        self.scheme.currentIndexChanged.connect(
            lambda _: parent.apply_scheme(self.scheme.currentData()))
        form.addRow("Color scheme:", self.scheme)
        lay.addLayout(form)
        note = QLabel("The application follows your system Qt palette by default.\n"
                      "More settings will be added here over time.")
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.clicked.connect(lambda _: self.accept())
        lay.addWidget(bb)


# ------------------------------------------------------------- main window --
class MainWindow(QMainWindow):
    def __init__(self, system_palette):
        super().__init__()
        self.system_palette = QPalette(system_palette)
        self.setWindowTitle("SteamCMD Mod Manager")
        self.resize(1320, 860)
        self.setMinimumSize(1024, 660)
        self.cfg = load_cfg()
        self.pending = set()
        self._current_item = None
        self._loading = False
        self.tc = compute_theme(self.system_palette)

        self.worker = SteamWorker()
        self.worker.log.connect(self.log)
        self.worker.item_done.connect(self.on_item_done)
        self.worker.start()

        self._build_ui()
        self.apply_scheme(self.cfg.get("color_scheme", "system"))
        self.reload_profiles()
        self.ensure_steamcmd()

    # ---- ui ----
    def _build_ui(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addWidget(QLabel(" Profile: "))
        self.profile_box = QComboBox()
        self.profile_box.setMinimumWidth(180)
        tb.addWidget(self.profile_box)
        for text, slot in (("New…", self.new_profile),
                           ("Edit…", self.edit_profile),
                           ("Delete", self.delete_profile)):
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb.addAction(a)
        tb.addSeparator()
        self.sc_label = QLabel()
        self.sc_label.setMaximumWidth(380)
        tb.addWidget(self.sc_label)
        a = QAction("Locate…", self)
        a.triggered.connect(self.choose_steamcmd)
        tb.addAction(a)
        tb.addSeparator()
        a = QAction(" Settings", self)
        a.triggered.connect(self.open_settings)
        tb.addAction(a)

        self.addToolBarBreak()
        tb2 = QToolBar("Actions")
        tb2.setMovable(False)
        self.addToolBar(tb2)
        for text, slot in (("+ Add by ID…", self.add_by_id),
                           ("⟳ Update all mods", self.update_all),
                           ("✕ Cancel queue", self.cancel_queue),
                           ("Open mods folder", self.open_mods_folder)):
            a = QAction(text, self)
            a.triggered.connect(slot)
            tb2.addAction(a)

        # -- left: mod library --
        left = QFrame()
        left.setObjectName("panel")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("MOD LIBRARY")
        t.setObjectName("panelTitle")
        self.mod_count = QLabel("no profile")
        self.mod_count.setObjectName("dimLabel")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.mod_count)
        lv.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name or ID…")
        self.search.setClearButtonEnabled(True)
        lv.addWidget(self.search)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Sort"))
        self.sort_box = QComboBox()
        for key, label, _, _ in SORT_MODES:
            self.sort_box.addItem(label, key)
        saved = self.cfg.get("sort_mode", "name_asc")
        self.sort_box.setCurrentIndex(
            max(0, next((i for i, m in enumerate(SORT_MODES) if m[0] == saved), 0)))
        row.addWidget(self.sort_box, 1)
        self.btn_import = QPushButton("⇪ Import")
        self.btn_import.setToolTip("Scan the mods folder and import existing workshop mods")
        row.addWidget(self.btn_import)
        lv.addLayout(row)

        self.mod_list = QListWidget()
        lv.addWidget(self.mod_list, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        b_on = QPushButton("All on")
        b_off = QPushButton("All off")
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setObjectName("dangerBtn")
        bottom.addWidget(b_on)
        bottom.addWidget(b_off)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_remove)
        lv.addLayout(bottom)

        # -- right: browser with nav panel --
        right = QFrame()
        right.setObjectName("panel")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 10, 10, 10)
        rv.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_back = QPushButton("←")
        self.btn_back.setObjectName("navBack")
        self.btn_back.setToolTip("Back")
        self.btn_fwd = QPushButton("→")
        self.btn_fwd.setObjectName("navFwd")
        self.btn_fwd.setToolTip("Forward")
        self.btn_reload = QPushButton("↻")
        self.btn_reload.setObjectName("navReload")
        self.btn_reload.setProperty("loading", False)
        self.btn_reload.setToolTip("Reload")
        self.btn_home = QPushButton("⌂")
        self.btn_home.setObjectName("navHome")
        self.btn_home.setToolTip("Workshop home")
        for b in (self.btn_back, self.btn_fwd, self.btn_reload, self.btn_home):
            b.setFixedSize(40, 30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            bar.addWidget(b)
        self.url_bar = QLineEdit()
        self.url_bar.setObjectName("urlBar")
        self.url_bar.setPlaceholderText("Page link — Enter to go, paste an ID to open a mod")
        self.url_bar.setMinimumHeight(30)
        self.url_bar.setClearButtonEnabled(True)
        bar.addWidget(self.url_bar, 1)
        self.btn_copy = QPushButton("⧉ Copy")
        self.btn_copy.setObjectName("copyBtn")
        self.btn_copy.setToolTip("Copy page link")
        self.btn_copy.setMinimumHeight(30)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        bar.addWidget(self.btn_copy)
        self.btn_dl = QPushButton("⬇ Download")
        self.btn_dl.setObjectName("dlBtn")
        self.btn_dl.setEnabled(False)
        self.btn_dl.setMinimumHeight(30)
        self.btn_dl.setMinimumWidth(132)
        self.btn_dl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_dl.setToolTip("Open a workshop item page to download")
        bar.addWidget(self.btn_dl)
        rv.addLayout(bar)

        wf = QFrame()
        wf.setObjectName("webFrame")
        wfl = QVBoxLayout(wf)
        wfl.setContentsMargins(1, 1, 1, 1)
        self.view = WorkshopView()
        wfl.addWidget(self.view)
        rv.addWidget(wf, 1)

        # -- bottom: activity log --
        logp = QFrame()
        logp.setObjectName("panel")
        lpv = QVBoxLayout(logp)
        lpv.setContentsMargins(10, 8, 10, 10)
        lpv.setSpacing(6)
        lt = QLabel("ACTIVITY")
        lt.setObjectName("panelTitle")
        lpv.addWidget(lt)
        self.logbox = QPlainTextEdit()
        self.logbox.setObjectName("log")
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(5000)
        lpv.addWidget(self.logbox)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(left)
        top.addWidget(right)
        top.setSizes([360, 960])
        top.setChildrenCollapsible(False)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(logp)
        split.setSizes([590, 190])
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

        self.queue_label = QLabel("Queue: 0")
        self.statusBar().addPermanentWidget(self.queue_label)

        # -- connections --
        self.profile_box.currentIndexChanged.connect(self.on_profile_changed)
        self.search.textChanged.connect(lambda _: self.refresh_mods())
        self.sort_box.currentIndexChanged.connect(self.on_sort_changed)
        self.btn_import.clicked.connect(self.import_clicked)
        b_on.clicked.connect(lambda: self.set_all_enabled(True))
        b_off.clicked.connect(lambda: self.set_all_enabled(False))
        self.btn_remove.clicked.connect(lambda: self.remove_mod())
        self.mod_list.itemDoubleClicked.connect(self.open_item_page)
        self.btn_back.clicked.connect(self.view.back)
        self.btn_fwd.clicked.connect(self.view.forward)
        self.btn_reload.clicked.connect(self.reload_or_stop)
        self.btn_home.clicked.connect(self.go_home)
        self.url_bar.returnPressed.connect(self.navigate_from_bar)
        self.btn_copy.clicked.connect(self.copy_url)
        self.btn_dl.clicked.connect(self.download_current)
        self.view.urlChanged.connect(self.on_url_changed)
        self.view.loadStarted.connect(self.on_load_started)
        self.view.loadFinished.connect(self.on_load_finished)

    # ---- settings / theme ----
    def open_settings(self):
        SettingsDialog(self, self.cfg.get("color_scheme", "system")).exec()

    def apply_scheme(self, scheme):
        app = QApplication.instance()
        if scheme == "system":
            app.setPalette(QPalette(self.system_palette))
        else:
            app.setPalette(make_palette(scheme))
        self.tc = compute_theme(app.palette())
        app.setStyleSheet(build_qss(self.tc))
        self.cfg["color_scheme"] = scheme
        save_cfg(self.cfg)
        self.refresh_mods()

    def toggle_qss(self, on):
        col = QColor(self.tc["success"] if on else self.tc["danger"])
        win = QColor(self.tc["window"])
        bg = mix(win, col, 0.12).name()
        bd = mix(win, col, 0.55).name()
        hov = mix(win, col, 0.20).name()
        return (f"QPushButton{{background:{bg};border:1px solid {bd};border-radius:6px;"
                f"color:{col};font-weight:700;font-size:12px;}}"
                f"QPushButton:hover{{background:{hov};border-color:{col.name()};}}")

    def row_qss(self, hexs):
        """Tint the whole mod entry with its color tag (translucent so the
        list's hover/selection highlight still shows through)."""
        if hexs:
            c = QColor(hexs)
            a = 60 if self.tc["is_dark"] else 45
            bg = f"rgba({c.red()},{c.green()},{c.blue()},{a})"
            bd = hexs
        else:
            bg, bd = "transparent", "transparent"
        return (f"QWidget#modRow {{ background: {bg}; border-left: 3px solid {bd}; "
                f"border-radius: 6px; }}")

    # ---- helpers ----
    def log(self, msg):
        self.logbox.appendPlainText(msg)

    def profile(self):
        i = self.profile_box.currentIndex()
        if 0 <= i < len(self.cfg["profiles"]):
            return self.cfg["profiles"][i]
        return None

    def find_profile(self, name):
        return next((p for p in self.cfg["profiles"] if p["name"] == name), None)

    def ready(self):
        if self.profile() is None:
            QMessageBox.information(self, "Profile", "Create and select a game profile first.")
            return False
        return self.require_steamcmd()

    def require_steamcmd(self):
        p = self.cfg.get("steamcmd", "")
        if p and Path(p).is_file():
            return True
        return self.ensure_steamcmd()

    def update_queue_label(self):
        self.queue_label.setText(f"Queue: {len(self.pending)}")

    def _unpend(self, name, ids):
        for i in ids:
            self.pending.discard((name, i))
        self.update_queue_label()

    def refresh_steamcmd_label(self):
        p = self.cfg.get("steamcmd") or "(not set)"
        self.sc_label.setText(f" steamcmd: {p} ")
        self.sc_label.setToolTip(p)

    @staticmethod
    def _repolish(w):
        w.style().unpolish(w)
        w.style().polish(w)

    @staticmethod
    def mod_paths(p, iid):
        base = Path(p["mods_dir"])
        return base / iid, base / ".disabled" / iid

    def place_mod(self, p, iid, enabled):
        """Keep the mod folder in <mods>/ or <mods>/.disabled/ per its state."""
        on, off = self.mod_paths(p, iid)
        want, other = (on, off) if enabled else (off, on)
        try:
            if want.exists():
                if other.exists():
                    shutil.rmtree(other)
            elif other.exists():
                want.parent.mkdir(parents=True, exist_ok=True)
                if want.parent.name == ".disabled":
                    hide_dir(want.parent)
                shutil.move(str(other), str(want))
        except Exception as e:
            self.log(f"[{iid}] could not move folder: {e}")

    # ---- steamcmd setup ----
    def ensure_steamcmd(self):
        path = self.cfg.get("steamcmd", "")
        if path and Path(path).is_file():
            self.refresh_steamcmd_label()
            return True
        found = detect_steamcmd()
        if found:
            self.cfg["steamcmd"] = found
            save_cfg(self.cfg)
            self.refresh_steamcmd_label()
            self.log(f"Found steamcmd: {found}")
            return True
        self.refresh_steamcmd_label()
        QMessageBox.information(self, "steamcmd",
                                "steamcmd was not found automatically.\n"
                                "Please locate the steamcmd executable "
                                "(steamcmd.exe on Windows, steamcmd.sh on Linux).")
        return self.choose_steamcmd()

    def choose_steamcmd(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, "Select steamcmd executable", str(Path.home()),
            "steamcmd (steamcmd.exe steamcmd.sh);;All files (*)")
        if not fn:
            return False
        self.cfg["steamcmd"] = fn
        save_cfg(self.cfg)
        self.refresh_steamcmd_label()
        self.log(f"steamcmd set to: {fn}")
        return True

    # ---- profiles ----
    def reload_profiles(self):
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        for p in self.cfg["profiles"]:
            self.profile_box.addItem(p["name"])
        idx = next((i for i, p in enumerate(self.cfg["profiles"])
                    if p["name"] == self.cfg.get("last_profile")),
                   0 if self.cfg["profiles"] else -1)
        self.profile_box.setCurrentIndex(idx)
        self.profile_box.blockSignals(False)
        self.on_profile_changed()

    def on_profile_changed(self):
        p = self.profile()
        if p:
            self.cfg["last_profile"] = p["name"]
            save_cfg(self.cfg)
            self.view.setUrl(QUrl(WORKSHOP_URL.format(app_id=p["app_id"])))
        else:
            self.view.setUrl(QUrl("about:blank"))
        self.refresh_mods()

    def new_profile(self):
        dlg = ProfileDialog(self, taken=[p["name"] for p in self.cfg["profiles"]])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.values()
            v["items"] = {}
            Path(v["mods_dir"]).mkdir(parents=True, exist_ok=True)
            self.cfg["profiles"].append(v)
            self.cfg["last_profile"] = v["name"]
            save_cfg(self.cfg)
            self.reload_profiles()
            self.offer_import(v)

    def edit_profile(self):
        p = self.profile()
        if not p:
            return
        old_dir = p["mods_dir"]
        others = [x["name"] for x in self.cfg["profiles"] if x is not p]
        dlg = ProfileDialog(self, data=p, taken=others)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.values()
            v["items"] = p["items"]
            Path(v["mods_dir"]).mkdir(parents=True, exist_ok=True)
            self.cfg["profiles"][self.profile_box.currentIndex()] = v
            self.cfg["last_profile"] = v["name"]
            save_cfg(self.cfg)
            self.reload_profiles()
            if v["mods_dir"] != old_dir:
                self.offer_import(v)

    def delete_profile(self):
        p = self.profile()
        if not p:
            return
        if QMessageBox.question(self, "Delete profile",
                                f"Delete profile '{p['name']}'?\n"
                                "(Downloaded mod files are not deleted.)") \
                != QMessageBox.StandardButton.Yes:
            return
        self.cfg["profiles"].pop(self.profile_box.currentIndex())
        save_cfg(self.cfg)
        self.reload_profiles()

    # ---- import existing folder ----
    def import_clicked(self):
        p = self.profile()
        if p is None:
            QMessageBox.information(self, "Import", "Create and select a profile first.")
            return
        self.offer_import(p, interactive=True)

    def offer_import(self, p, interactive=False):
        found, skipped = scan_mods_folder(p["mods_dir"])
        if not found:
            if interactive:
                QMessageBox.information(
                    self, "Import",
                    "No workshop mods were detected in this folder.\n"
                    "IDs are read from numeric folder names, RimWorld About files\n"
                    "and Arma meta.cpp.")
            return
        n_off = sum(1 for v in found.values() if not v["enabled"])
        txt = f"Found {len(found)} workshop mod(s) in:\n{p['mods_dir']}\n"
        if n_off:
            txt += f"({n_off} currently disabled)\n"
        if skipped:
            txt += f"{skipped} folder(s) without a workshop ID were skipped.\n"
        txt += ("\nRe-download them all so this tool can manage and update them?\n"
                "Existing folders will be replaced with fresh copies.")
        if QMessageBox.question(self, "Import existing mods", txt,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        for iid, info in found.items():
            prev = p["items"].get(iid, {})
            p["items"][iid] = {"title": prev.get("title") or info["name"],
                               "enabled": prev.get("enabled", info["enabled"]),
                               "color": prev.get("color", ""),
                               "time_updated": prev.get("time_updated", 0),
                               "installed_at": prev.get("installed_at", 0),
                               "import_src": info["path"]}
        save_cfg(self.cfg)
        if p is self.profile():
            self.refresh_mods()
        self.log(f"Importing {len(found)} mod(s) from {p['mods_dir']} …")
        self.add_mods(list(found), p["name"])

    # ---- mod list ----
    def current_sort(self):
        key = self.sort_box.currentData()
        for k, _, fn, rev in SORT_MODES:
            if k == key:
                return fn, rev
        return SORT_MODES[0][2], SORT_MODES[0][3]

    def on_sort_changed(self):
        self.cfg["sort_mode"] = self.sort_box.currentData()
        save_cfg(self.cfg)
        self.refresh_mods()

    def selected_mod_id(self):
        it = self.mod_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def select_mod(self, iid):
        for r in range(self.mod_list.count()):
            if self.mod_list.item(r).data(Qt.ItemDataRole.UserRole) == iid:
                self.mod_list.setCurrentRow(r)
                return

    def refresh_mods(self):
        p = self.profile()
        sel = self.selected_mod_id()
        scroll = self.mod_list.verticalScrollBar().value()
        self.mod_list.clear()
        if not p:
            self.mod_count.setText("no profile")
            return
        flt = self.search.text().strip().lower()
        keyf, rev = self.current_sort()
        entries = [(i, m) for i, m in p["items"].items()
                   if not flt or flt in (m.get("title", "") + " " + i).lower()]
        entries.sort(key=lambda kv: keyf(kv[0], kv[1]), reverse=rev)
        for iid, meta in entries:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, iid)
            it.setSizeHint(QSize(0, 50))
            self.mod_list.addItem(it)
            self.mod_list.setItemWidget(it, self._make_row(iid, meta))
        on = sum(1 for m in p["items"].values() if m.get("enabled", True))
        self.mod_count.setText(f"{len(p['items'])} mods · {on} on")
        if sel:
            self.select_mod(sel)
        self.mod_list.verticalScrollBar().setValue(scroll)

    def _make_row(self, iid, meta):
        enabled = meta.get("enabled", True)
        c = MOD_COLORS.get(meta.get("color", ""))
        row = QWidget()
        row.setObjectName("modRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setStyleSheet(self.row_qss(c[1] if c else None))
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 3, 8, 3)
        h.setSpacing(8)

        tgl = QPushButton("I" if enabled else "O")
        tgl.setFixedSize(30, 28)
        tgl.setCursor(Qt.CursorShape.PointingHandCursor)
        tgl.setToolTip("Enabled (I) — click to disable" if enabled
                       else "Disabled (O) — click to enable")
        tgl.setStyleSheet(self.toggle_qss(enabled))
        tgl.clicked.connect(lambda _=False, i=iid: self.toggle_mod(i))
        h.addWidget(tgl)

        full = meta.get("title") or iid
        title = QLabel(full)
        title.setStyleSheet("color:%s;font-weight:600;background:transparent;"
                            % (self.tc["text"] if enabled else self.tc["subtle"]))
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setToolTip(f"{full}\nWorkshop ID: {iid}")
        ts = meta.get("installed_at", 0)
        date = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "pending"
        sub = QLabel(f"{iid}  ·  {date}")
        sub.setStyleSheet(f"color:{self.tc['subtle']};font-size:11px;background:transparent;")
        v = QVBoxLayout()
        v.setSpacing(1)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(title)
        v.addWidget(sub)
        h.addLayout(v, 1)

        # Right-click anywhere on the row opens the mod context menu;
        # a left press selects the row.
        for w in [row] + row.findChildren(QWidget):
            w.setProperty("mod_id", iid)
            w.installEventFilter(self)
        return row

    def eventFilter(self, obj, event):
        et = event.type()
        iid = obj.property("mod_id")
        if iid:
            iid = str(iid)
            if et == QEvent.Type.ContextMenu:
                self.show_mod_menu(iid, event.globalPos())
                return True
            if (et == QEvent.Type.MouseButtonDblClick
                    and event.button() == Qt.MouseButton.LeftButton
                    and not isinstance(obj, QPushButton)):
                self.open_item_page_by_id(iid)
                return True
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.select_mod(iid)
        return super().eventFilter(obj, event)

    def show_mod_menu(self, iid, global_pos):
        p = self.profile()
        if not p or iid not in p["items"]:
            return
        self.select_mod(iid)
        menu = QMenu(self)

        def act(text, slot, icon=None):
            a = menu.addAction(icon, text) if icon else menu.addAction(text)
            a.triggered.connect(slot)
            return a

        act("Re-download", lambda: self.redownload(iid))
        act("Update", lambda: self.update_mods([iid]))
        cm = menu.addMenu("Set color")
        for key, (label, hexs) in MOD_COLORS.items():
            a = cm.addAction(color_icon(hexs), label)
            a.triggered.connect(lambda _, k=key: self.set_color(iid, k))
        cm.addSeparator()
        a = cm.addAction("No color")
        a.triggered.connect(lambda: self.set_color(iid, ""))
        menu.addSeparator()
        act("Go to in file explorer", lambda: self.open_mod_folder(iid))
        act("Copy ID", lambda: self.copy_mod_id(iid))
        menu.addSeparator()
        act("Remove", lambda: self.remove_mod(iid))
        menu.exec(global_pos)

    def toggle_mod(self, iid):
        p = self.profile()
        if not p or iid not in p["items"]:
            return
        m = p["items"][iid]
        m["enabled"] = not m.get("enabled", True)
        self.place_mod(p, iid, m["enabled"])
        save_cfg(self.cfg)
        self.refresh_mods()
        self.log(f"[{iid}] {'enabled' if m['enabled'] else 'disabled (moved to .disabled)'}")

    def set_all_enabled(self, flag):
        p = self.profile()
        if not p or not p["items"]:
            return
        for iid, m in p["items"].items():
            if m.get("enabled", True) != flag:
                m["enabled"] = flag
                self.place_mod(p, iid, flag)
        save_cfg(self.cfg)
        self.refresh_mods()
        self.log(f"All mods {'enabled' if flag else 'disabled'}.")

    def set_color(self, iid, key):
        p = self.profile()
        if not p or iid not in p["items"]:
            return
        p["items"][iid]["color"] = key
        save_cfg(self.cfg)
        self.refresh_mods()

    def remove_mod(self, iid=None):
        p = self.profile()
        if not p:
            return
        iid = iid or self.selected_mod_id()
        if not iid or iid not in p["items"]:
            return
        if (p["name"], iid) in self.pending:
            QMessageBox.information(self, "Busy",
                                    "This mod is in the download queue.\n"
                                    "Cancel the queue or wait for it to finish first.")
            return
        title = p["items"][iid].get("title") or iid
        if QMessageBox.question(self, "Delete mod",
                                f"Delete '{title}' [{iid}]?\n"
                                "Its folder will be removed from the mods directory.") \
                != QMessageBox.StandardButton.Yes:
            return
        on, off = self.mod_paths(p, iid)
        for d in (on, off):
            shutil.rmtree(d, ignore_errors=True)
        p["items"].pop(iid, None)
        save_cfg(self.cfg)
        self.refresh_mods()
        self.log(f"[{iid}] deleted.")

    def redownload(self, iid):
        if self.ready():
            self.add_mods([iid], self.profile()["name"])

    def open_item_page(self, it):
        self.open_item_page_by_id(it.data(Qt.ItemDataRole.UserRole))

    def open_item_page_by_id(self, iid):
        self.view.setUrl(QUrl(ITEM_URL.format(item_id=iid)))

    def open_mod_folder(self, iid):
        p = self.profile()
        if not p:
            return
        on, off = self.mod_paths(p, iid)
        target = on if on.exists() else off if off.exists() else Path(p["mods_dir"])
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def open_mods_folder(self):
        p = self.profile()
        if p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(p["mods_dir"]))

    def copy_mod_id(self, iid):
        QApplication.clipboard().setText(iid)
        self.statusBar().showMessage(f"Copied ID {iid}", 2000)

    # ---- browser panel ----
    def on_url_changed(self, qurl):
        if not self.url_bar.hasFocus():
            self.url_bar.setText(qurl.toString())
        iid = None
        if "steamcommunity.com" in qurl.host() and "filedetails" in qurl.path():
            ids = urllib.parse.parse_qs(qurl.query()).get("id")
            if ids and ids[0].isdigit():
                iid = ids[0]
        self._current_item = iid
        self.btn_dl.setEnabled(bool(iid))
        self.btn_dl.setText(f"⬇ {iid}" if iid else "⬇ Download")
        self.btn_dl.setToolTip(f"Download workshop item {iid}" if iid
                               else "Open a workshop item page to download")
        self.update_nav_buttons()

    def update_nav_buttons(self):
        h = self.view.history()
        self.btn_back.setEnabled(h.canGoBack())
        self.btn_fwd.setEnabled(h.canGoForward())

    def reload_or_stop(self):
        if self._loading:
            self.view.stop()
        else:
            self.view.reload()

    def on_load_started(self):
        self._loading = True
        self.btn_reload.setText("✕")
        self.btn_reload.setProperty("loading", True)
        self.btn_reload.setToolTip("Stop loading")
        self._repolish(self.btn_reload)

    def on_load_finished(self, ok):
        self._loading = False
        self.btn_reload.setText("↻")
        self.btn_reload.setProperty("loading", False)
        self.btn_reload.setToolTip("Reload")
        self._repolish(self.btn_reload)
        self.update_nav_buttons()
        if not ok:
            self.statusBar().showMessage("Page failed to load", 3000)

    def go_home(self):
        p = self.profile()
        self.view.setUrl(QUrl(WORKSHOP_URL.format(app_id=p["app_id"]) if p else "about:blank"))

    def navigate_from_bar(self):
        t = self.url_bar.text().strip()
        if not t:
            return
        if t.isdigit():
            url = ITEM_URL.format(item_id=t)
        elif "://" not in t:
            url = "https://" + t
        else:
            url = t
        self.view.setUrl(QUrl(url))
        self.url_bar.clearFocus()

    def copy_url(self):
        QApplication.clipboard().setText(self.view.url().toString())
        self.btn_copy.setText("✓ Copied")
        QTimer.singleShot(1200, lambda: self.btn_copy.setText("⧉ Copy"))
        self.statusBar().showMessage("Link copied to clipboard", 2000)

    # ---- download ----
    def download_current(self):
        if self._current_item and self.ready():
            self.add_mods([self._current_item], self.profile()["name"])

    def add_by_id(self):
        if not self.ready():
            return
        text, ok = QInputDialog.getText(self, "Add by ID",
                                        "Workshop item ID(s) or URL(s), separated by spaces:")
        if not ok or not text.strip():
            return
        ids = re.findall(r"id=(\d+)", text) or re.findall(r"\d+", text)
        if ids:
            self.add_mods(ids, self.profile()["name"])

    def add_mods(self, ids, profile_name):
        p = self.find_profile(profile_name)
        if p is None:
            return
        ids = [i for i in dict.fromkeys(ids) if (profile_name, i) not in self.pending]
        if not ids:
            self.log("Nothing to add (already queued).")
            return
        for i in ids:
            self.pending.add((profile_name, i))
        self.update_queue_label()
        self.log(f"Fetching details for {len(ids)} item(s)…")
        t = FnThread(lambda: fetch_details(ids), self)
        t.ok.connect(lambda details: self._on_details_for_add(profile_name, ids, details))
        t.err.connect(lambda e: self._add_failed(profile_name, ids, f"API error: {e}"))
        t.finished.connect(t.deleteLater)
        t.start()

    def _add_failed(self, name, ids, msg):
        self.log(msg)
        self._unpend(name, ids)

    def _on_details_for_add(self, name, ids, details):
        p = self.find_profile(name)
        if p is None:
            self._unpend(name, ids)
            return
        for iid in ids:
            d = details.get(iid) or {}
            if d.get("result") == 1 and d.get("filetype") != 2:
                meta = {"title": d.get("title", ""),
                        "time_updated": int(d.get("time_updated", 0))}
                self.enqueue_job(p, iid, meta)
            else:  # maybe a collection
                self.log(f"[{iid}] not a regular item, trying as collection…")
                t = FnThread(lambda cid=iid: fetch_collection_children(cid), self)
                t.ok.connect(lambda kids, cid=iid: self._on_collection(name, cid, kids))
                t.err.connect(lambda e, cid=iid:
                              self._add_failed(name, [cid], f"collection lookup failed: {e}"))
                t.finished.connect(t.deleteLater)
                t.start()

    def _on_collection(self, name, cid, children):
        self._unpend(name, [cid])
        if children:
            self.log(f"Collection {cid}: {len(children)} items.")
            self.add_mods(children, name)
        else:
            self.log(f"[{cid}] not found on workshop (deleted, private or invalid id).")

    def enqueue_job(self, p, iid, meta):
        self.pending.add((p["name"], iid))
        self.update_queue_label()
        self.worker.enqueue({"profile": p["name"], "app_id": p["app_id"], "item_id": iid,
                             "steamcmd": self.cfg["steamcmd"], "mods_dir": p["mods_dir"],
                             "username": p.get("username", ""),
                             "password": p.get("password", ""),
                             "force_windows": p.get("force_windows", False),
                             "meta": meta})
        self.log(f"Queued: {meta.get('title') or iid} [{iid}]")

    def on_item_done(self, name, iid, ok, meta):
        self.pending.discard((name, iid))
        self.update_queue_label()
        p = self.find_profile(name)
        if p is None or not ok:
            return
        meta = meta or {}
        prev = p["items"].get(iid, {})
        entry = {"title": meta.get("title") or prev.get("title") or iid,
                 "time_updated": meta.get("time_updated", 0),
                 "installed_at": int(time.time()),
                 "enabled": prev.get("enabled", True),
                 "color": prev.get("color", "")}
        p["items"][iid] = entry
        save_cfg(self.cfg)
        self.place_mod(p, iid, entry["enabled"])
        old = prev.get("import_src")
        if old:
            sp = Path(old)
            on, off = self.mod_paths(p, iid)
            norm = lambda x: os.path.normpath(str(x))
            if sp.exists() and norm(sp) not in (norm(on), norm(off)):
                shutil.rmtree(sp, ignore_errors=True)
                self.log(f"[{iid}] removed old imported folder '{sp.name}'")
        if not entry["enabled"]:
            self.log(f"[{iid}] kept disabled (.disabled/{iid})")
        if p is self.profile():
            self.refresh_mods()

    # ---- updates ----
    def update_all(self):
        self.update_mods(None)

    def update_mods(self, ids=None):
        if not self.ready():
            return
        p = self.profile()
        name = p["name"]
        ids = [i for i in (ids if ids is not None else list(p["items"])) if i in p["items"]]
        if not ids:
            self.log("No mods installed in this profile.")
            return
        self.log(f"Checking {len(ids)} mod(s) for updates…")
        t = FnThread(lambda: fetch_details(ids), self)
        t.ok.connect(lambda d: self._on_update_details(name, d))
        t.err.connect(lambda e: self.log(f"Update check failed: {e}"))
        t.finished.connect(t.deleteLater)
        t.start()

    def _on_update_details(self, name, details):
        p = self.find_profile(name)
        if p is None:
            return
        queued = 0
        for iid, local in list(p["items"].items()):
            if iid not in details and len(details) < len(p["items"]):
                continue  # single-mod check: ignore the rest
            d = details.get(iid)
            if not d or d.get("result") != 1:
                self.log(f"[{iid}] not found on workshop (removed or made private?).")
                continue
            remote = int(d.get("time_updated", 0))
            if remote > int(local.get("time_updated", 0)):
                if (name, iid) in self.pending:
                    continue
                self.log(f"[{iid}] update available: {local.get('title', '')}")
                self.enqueue_job(p, iid, {"title": d.get("title", ""),
                                          "time_updated": remote})
                queued += 1
        self.log(f"Update check done: {queued} mod(s) queued." if queued
                 else "All checked mods are up to date.")

    def cancel_queue(self):
        self.worker.cancel()
        self.pending.clear()
        self.update_queue_label()
        self.log("Queue cancelled.")

    def closeEvent(self, event):
        save_cfg(self.cfg)
        self.worker.shutdown()
        if not self.worker.wait(5000):
            self.worker.terminate()
            self.worker.wait(1000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("steamcmd-mod-manager")
    app.setOrganizationName("steamcmd-mod-manager")
    w = MainWindow(QPalette(app.palette()))
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
