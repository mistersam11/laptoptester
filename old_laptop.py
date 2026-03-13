#!/usr/bin/env python3
"""
Laptop Hardware Tester – tkinter version (stability-hardened)
Designed for compatibility with older hardware (Intel Core 2 era+)

Key stability changes vs your original:
- Fix duplicate fullscreen watchdog (only one, no overrideredirect loop)
- Optional imports for cv2/numpy/psutil/Pillow/sounddevice so missing deps don't crash the app
- More robust camera handling (tries /dev/video0..3, lower default res, backoff retry, avoids hammering)
- Bind/unbind keyboard handlers per-screen (less global input weirdness)
- Skip dmidecode unless root (avoids slow/failure paths)
- Sync button lockout during sync + small retry/backoff
- Safer poweroff flow (normal first, then sysrq, then forced)
- Global crash catcher writes a log (especially useful on live USB)

Dependencies (recommended via apt on antiX/Debian-based):
  sudo apt install python3 python3-tk python3-psutil python3-numpy python3-opencv python3-pil python3-pil.imagetk \
                   alsa-utils v4l-utils dmidecode
Optional:
  sudo apt install python3-sounddevice
"""

import os, re, sys, time, json, threading, subprocess, traceback, socket
import urllib.request, urllib.error
import tkinter as tk
from tkinter import font as tkfont

# --- Optional imports (don't crash if missing) ---
CV_AVAILABLE = True
NP_AVAILABLE = True
PSUTIL_AVAILABLE = True
PIL_AVAILABLE = False
SD_AVAILABLE = False

try:
    import cv2
except Exception:
    CV_AVAILABLE = False

try:
    import numpy as np
except Exception:
    NP_AVAILABLE = False

try:
    import psutil
except Exception:
    PSUTIL_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

try:
    import sounddevice as sd
    SD_AVAILABLE = True
except Exception:
    SD_AVAILABLE = False


# ─── Colours ──────────────────────────────────────────────────────────────────
BG         = "#141414"
FG         = "#F0F0F0"
GRAY       = "#3C3C3C"
LIGHT_GRAY = "#787878"
GREEN      = "#00AA00"
LT_GREEN   = "#78FF78"
BLUE       = "#4682FF"
RED        = "#C82828"
ORANGE     = "#FFA500"
KEY_BG     = "#909090"

# ─── Config ───────────────────────────────────────────────────────────────────
PUPPY_HOME       = "/mnt/home"
CONFIG_FILE      = (os.path.join(PUPPY_HOME, "laptoptester_ip_config.txt")
                    if os.path.isdir(PUPPY_HOME)
                    else os.path.join(os.path.dirname(__file__), "ip_config.txt"))
SERVER_PORT      = 5050
WIFI_STATUS_FILE = "/tmp/laptoptester_wifi_status.json"

# Crash log path (prefer /mnt/home on live USB if present)
CRASH_LOG = (os.path.join(PUPPY_HOME, "laptoptester_crash.log")
             if os.path.isdir(PUPPY_HOME) else os.path.join(os.path.dirname(__file__), "laptoptester_crash.log"))

# Reasonable global socket default timeout to avoid weird hangs
try:
    socket.setdefaulttimeout(6)
except Exception:
    pass


# ─── Server / IP helpers ──────────────────────────────────────────────────────

def load_saved_ip(default="192.168.3.84"):
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                return f.read().strip()
    except Exception:
        pass
    return default


def save_ip(ip):
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            f.write(ip.strip())
    except Exception as e:
        print("Could not save IP:", e)


def ping_server(ip):
    try:
        r = urllib.request.urlopen(f"http://{ip}:{SERVER_PORT}/ping", timeout=3)
        return r.status == 200
    except Exception:
        return False


def post_laptop_data(ip, payload):
    try:
        url  = f"http://{ip}:{SERVER_PORT}/log"
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(url, data=data,
                                       headers={"Content-Type": "application/json"},
                                       method="POST")
        with urllib.request.urlopen(req, timeout=6) as resp:
            body = json.loads(resp.read().decode(errors="replace"))
            return True, body.get("message", "Success")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode(errors="replace"))
            return False, body.get("message", str(e))
        except Exception:
            return False, f"HTTP {getattr(e,'code','?')}"
    except Exception as e:
        return False, str(e)


def read_wifi_status():
    # 1) NetworkManager (nmcli)
    try:
        import shutil
        if shutil.which("nmcli"):
            # Are we connected?
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "WIFI,GSTATE,STATE", "general"],
                text=True, stderr=subprocess.DEVNULL
            ).strip().splitlines()
            # Example lines: WIFI:enabled, STATE:connected, etc.
            state = ""
            wifi  = ""
            for line in out:
                if line.startswith("STATE:"):
                    state = line.split(":", 1)[1].strip()
                if line.startswith("WIFI:"):
                    wifi = line.split(":", 1)[1].strip()

            if wifi == "disabled":
                return "WiFi: disabled", RED

            if state == "connected":
                ssid = subprocess.check_output(
                    ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                    text=True, stderr=subprocess.DEVNULL
                )
                # Find the active one
                for l in ssid.splitlines():
                    if l.startswith("yes:"):
                        return f"WiFi: Connected ({l.split(':',1)[1]})", GREEN
                return "WiFi: Connected", GREEN

            return "WiFi: Not connected", ORANGE
    except Exception:
        pass

    # 2) Connman (common on antiX)
    try:
        import shutil
        if shutil.which("connmanctl"):
            # If it prints "State = online" / "ready"
            out = subprocess.check_output(
                ["connmanctl", "state"],
                text=True, stderr=subprocess.DEVNULL
            ).strip().lower()
            if "online" in out or "ready" in out:
                # Best-effort SSID
                try:
                    ssid = subprocess.check_output(
                        ["iwgetid", "-r"],
                        text=True, stderr=subprocess.DEVNULL
                    ).strip()
                    if ssid:
                        return f"WiFi: Connected ({ssid})", GREEN
                except Exception:
                    pass
                return "WiFi: Connected", GREEN
            return "WiFi: Not connected", ORANGE
    except Exception:
        pass

    # 3) Fallback: iwgetid only
    try:
        import shutil
        if shutil.which("iwgetid"):
            ssid = subprocess.check_output(["iwgetid", "-r"], text=True,
                                           stderr=subprocess.DEVNULL).strip()
            if ssid:
                return f"WiFi: Connected ({ssid})", GREEN
            return "WiFi: Not connected", ORANGE
    except Exception:
        pass

    return "WiFi: status unavailable", ORANGE


# ─── System info helpers ──────────────────────────────────────────────────────

def _is_root():
    try:
        return os.geteuid() == 0
    except Exception:
        return False


def get_system_info():
    def read_dmi(field):
        path = f"/sys/class/dmi/id/{field}"
        try:
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip()
        except Exception:
            pass
        return "Unavailable"

    manufacturer = read_dmi("sys_vendor")
    model        = read_dmi("product_name")
    serial       = read_dmi("product_serial")

    # Lenovo quirk you already had
    if manufacturer and "lenovo" in manufacturer.lower():
        version = read_dmi("product_version")
        if (version
                and version.lower() not in {"unavailable", "none", "n/a", ""}  # sane
                and not re.fullmatch(r"[\d\s]+", version)):                     # not only digits
            model = version

    # If model begins with manufacturer, strip it
    if manufacturer and model and model.lower().startswith(manufacturer.lower()):
        model = model[len(manufacturer):].strip()

    # Handle the HP issue you mentioned earlier (normalize vendor)
    # Many DMI tables say "Hewlett-Packard"; prefer "HP"
    if manufacturer and manufacturer.strip().lower() in {"hewlett-packard", "hewlett packard"}:
        manufacturer = "HP"

    # And if model begins with "HP " or manufacturer prefix, strip again
    if manufacturer and model:
        lowm = model.lower()
        if lowm.startswith("hp "):
            model = model[3:].strip()
        if lowm.startswith(manufacturer.lower() + " "):
            model = model[len(manufacturer):].strip()

    return manufacturer or "Unavailable", model or "Unavailable", serial or "Unavailable"


def get_cpu_info():
    model = "Unavailable"
    cores = threads = "Unavailable"

    if PSUTIL_AVAILABLE:
        try:
            cores   = psutil.cpu_count(logical=False) or "Unavailable"
            threads = psutil.cpu_count(logical=True)  or "Unavailable"
        except Exception:
            pass

    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "model name" in line:
                    model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    return model, cores, threads


def get_ram_info():
    total = "Unavailable"
    slots = []

    if PSUTIL_AVAILABLE:
        try:
            total = round(psutil.virtual_memory().total / (1024 ** 3), 2)
        except Exception:
            pass

    # Avoid dmidecode unless root (stability / speed)
    if not _is_root():
        slots.append("Run as root for detailed RAM info")
        return total, slots

    try:
        out = subprocess.check_output(["dmidecode", "--type", "17"],
                                      text=True, stderr=subprocess.DEVNULL)
        found_any = False
        for device in out.split("Memory Device"):
            if "Size:" in device and "No Module Installed" not in device:
                size = type_ = speed = locator = "Unknown"
                for line in device.splitlines():
                    line = line.strip()
                    if   line.startswith("Size:"):    size    = line.split(":", 1)[1].strip()
                    elif line.startswith("Type:"):    type_   = line.split(":", 1)[1].strip()
                    elif line.startswith("Speed:"):   speed   = line.split(":", 1)[1].strip()
                    elif line.startswith("Locator:"): locator = line.split(":", 1)[1].strip()
                slots.append(f"{locator} – {size} – {type_} – {speed}")
                found_any = True
        if not found_any:
            slots.append("RAM details unavailable")
    except Exception:
        slots.append("RAM details unavailable (dmidecode failed)")
    return total, slots


def get_battery_info():
    percent = None
    health  = "Unavailable"
    cycles  = "Unavailable"

    if PSUTIL_AVAILABLE:
        try:
            bat = psutil.sensors_battery()
            if bat:
                percent = bat.percent
        except Exception:
            pass

    try:
        base = "/sys/class/power_supply"
        if not os.path.isdir(base):
            return percent, health, cycles

        bats = sorted(d for d in os.listdir(base) if d.startswith("BAT"))
        if bats:
            bp = os.path.join(base, bats[0])

            def rv(n):
                p = os.path.join(bp, n)
                return open(p).read().strip() if os.path.exists(p) else None

            full   = rv("energy_full")        or rv("charge_full")
            design = rv("energy_full_design") or rv("charge_full_design")
            if full and design:
                try:
                    health = f"{min(round(float(full) / float(design) * 100), 100)}%"
                except Exception:
                    pass

            c = rv("cycle_count")
            if c:
                cycles = c

            if percent is None:
                now = rv("energy_now") or rv("charge_now")
                if now and full:
                    try:
                        percent = round(float(now) / float(full) * 100)
                    except Exception:
                        pass
    except Exception:
        pass

    return percent, health, cycles


# ─── Audio ────────────────────────────────────────────────────────────────────

class WhiteNoisePlayer:
    def __init__(self):
        self._playing = False
        self._thread  = None

    def _play_loop(self):
        # If numpy missing, fallback to silence (or simply do nothing)
        if not NP_AVAILABLE:
            return

        sample_rate = 44100
        chunk       = sample_rate // 10  # 100ms chunks
        try:
            subprocess.call(["amixer", "sset", "Master", "unmute"],
                            stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            subprocess.call(["amixer", "sset", "Master", "40%"],
                            stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        except Exception:
            pass

        if SD_AVAILABLE:
            try:
                with sd.OutputStream(samplerate=sample_rate,
                                     channels=2, dtype="int16") as stream:
                    while self._playing:
                        noise = (np.random.uniform(-1, 1, (chunk, 2)) * 32767).astype(np.int16)
                        stream.write(noise)
            except Exception:
                # If sounddevice fails, just stop cleanly
                self._playing = False
        else:
            # Fallback: pipe raw S16_LE PCM to aplay
            try:
                proc = subprocess.Popen(
                    ["aplay", "-r", str(sample_rate), "-f", "S16_LE", "-c", "2", "-"],
                    stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
                )
                while self._playing:
                    noise = (np.random.uniform(-1, 1, (chunk, 2)) * 32767).astype(np.int16)
                    try:
                        proc.stdin.write(noise.tobytes())
                    except Exception:
                        break
                try:
                    if proc.stdin:
                        proc.stdin.close()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            except Exception:
                self._playing = False

    def start(self):
        if not self._playing:
            self._playing = True
            self._thread  = threading.Thread(target=self._play_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._playing = False

    @property
    def playing(self):
        return self._playing


# ─── Main Application ─────────────────────────────────────────────────────────

SCREEN_ORDER = ["camera", "speaker", "keyboard", "sysinfo", "final"]

GRADE_MAP = {
    "A": "A-Grade (Like-New)",
    "B": "B-Grade (Great)",
    "C": "C-Grade (Fair)",
    "D": "D-Grade (Parts)",
}


class LaptopTester:
    # ─── Small UI helper: always update UI from main thread ──────────────────
    def ui(self, fn, *args, **kwargs):
        try:
            self.root.after(0, lambda: fn(*args, **kwargs))
        except Exception:
            pass

    # ─── Fullscreen stability ────────────────────────────────────────────────
    def _apply_true_fullscreen(self):
        """Ask the WM for fullscreen (avoid overrideredirect loops for stability)."""
        try:
            self.root.attributes("-fullscreen", True)
        except Exception:
            pass
        try:
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

        # Optional EWMH nudge if wmctrl exists
        try:
            import shutil
            if shutil.which("wmctrl"):
                subprocess.call(
                    ["wmctrl", "-r", ":ACTIVE:", "-b", "add,fullscreen"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
        except Exception:
            pass

        # Ensure geometry matches screen
        try:
            w = self.root.winfo_screenwidth()
            h = self.root.winfo_screenheight()
            self.root.geometry(f"{w}x{h}+0+0")
        except Exception:
            pass

    def _fullscreen_watchdog(self):
        """Re-assert fullscreen occasionally (no overrideredirect)."""
        if not getattr(self, "_enforce_fullscreen", False):
            return
        self._apply_true_fullscreen()
        self.root.after(900, self._fullscreen_watchdog)

    def __init__(self):
        self.root = tk.Tk()
        self.root.configure(bg=BG)
        self.root.title("Laptop Hardware Tester")

        # --- Kiosk-ish mode ---
        self._enforce_fullscreen = True
        self.root.after(120, self._apply_true_fullscreen)
        self.root.after(900, self._fullscreen_watchdog)

        # Disable common exit shortcuts (still allow our Exit button)
        self.root.bind("<Escape>", lambda e: "break")
        self.root.bind("<Alt-F4>", lambda e: "break")

        self.W = self.root.winfo_screenwidth()
        self.H = self.root.winfo_screenheight()

        # Font sizes scaled to screen height
        scale      = max(self.H / 768, 0.75)
        sz_large   = int(28 * scale)
        sz_med     = int(18 * scale)
        sz_small   = int(12 * scale)
        sz_key     = int(10 * scale)

        self.fnt_large = tkfont.Font(family="Arial", size=sz_large, weight="bold")
        self.fnt_med   = tkfont.Font(family="Arial", size=sz_med)
        self.fnt_small = tkfont.Font(family="Arial", size=sz_small)
        self.fnt_key   = tkfont.Font(family="Arial", size=sz_key)

        self.current_idx = 0
        self._frames     = {}

        # Keyboard bindings (bound/unbound per screen)
        self._kbd_bound = False

        # Sync lock
        self._sync_in_progress = False

        self._build_all_screens()
        self._show_screen(0)
        self.root.mainloop()

    # ─── Navigation ───────────────────────────────────────────────────────────
    def _show_screen(self, idx):
        name = SCREEN_ORDER[idx]
        for f in self._frames.values():
            f.pack_forget()
        self._frames[name].pack(fill=tk.BOTH, expand=True)
        cb = getattr(self, f"_on_show_{name}", None)
        if cb:
            cb()
        self.current_idx = idx

    def _go_next(self):
        nxt = self.current_idx + 1
        if nxt < len(SCREEN_ORDER):
            self._on_leave()
            self._show_screen(nxt)

    def _go_back(self):
        prv = self.current_idx - 1
        if prv >= 0:
            self._on_leave()
            self._show_screen(prv)

    def _on_leave(self):
        name = SCREEN_ORDER[self.current_idx]
        cb   = getattr(self, f"_on_leave_{name}", None)
        if cb:
            cb()

    def quit_app(self):
        self._enforce_fullscreen = False
        self._on_leave()
        try:
            self.root.destroy()
        except Exception:
            pass
        sys.exit(0)

    # ─── Shared helpers ───────────────────────────────────────────────────────
    def _styled_button(self, parent, text, command, bg=GRAY, **kw):
        return tk.Button(
            parent, text=text, command=command,
            font=self.fnt_med, bg=bg, fg=FG,
            activebackground=BLUE, activeforeground=FG,
            relief=tk.FLAT, bd=0,
            padx=16, pady=8,
            cursor="hand2",
            **kw
        )

    def _nav_bar(self, frame, show_back=True, show_next=True,
                 next_label="Continue", extra_buttons=()):
        """
        Pack a navigation bar at the bottom of *frame*.
        *extra_buttons* is a list of (text, command, bg) tuples inserted
        between the right-side buttons and the Exit button.
        """
        bar = tk.Frame(frame, bg=BG, pady=10)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=20)

        if show_back:
            self._styled_button(bar, "◀  Previous", self._go_back).pack(side=tk.LEFT)

        # Right side: Exit | extra... | Next
        self._exit_btn = self._styled_button(bar, "Exit", self.quit_app, bg=RED)
        self._exit_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # Keep references for enabling/disabling (sync button, etc.)
        self._extra_btn_widgets = getattr(self, "_extra_btn_widgets", [])
        for btn_text, btn_cmd, btn_bg in reversed(extra_buttons):
            b = self._styled_button(bar, btn_text, btn_cmd, bg=btn_bg)
            b.pack(side=tk.RIGHT, padx=(0, 8))
            self._extra_btn_widgets.append((btn_text, b))

        if show_next:
            self._styled_button(bar, next_label + "  ▶", self._go_next).pack(side=tk.RIGHT, padx=(0, 8))

        return bar

    def _title(self, frame, text):
        lbl = tk.Label(frame, text=text, font=self.fnt_large,
                       bg=BG, fg=FG, anchor=tk.W)
        lbl.pack(fill=tk.X, padx=40, pady=(20, 6))
        tk.Frame(frame, bg=LIGHT_GRAY, height=1).pack(fill=tk.X, padx=40)
        return lbl

    def _set_sync_buttons_enabled(self, enabled: bool):
        # extra buttons are stored with text; enable/disable those related to sync/power
        for txt, btn in getattr(self, "_extra_btn_widgets", []):
            try:
                btn.config(state=(tk.NORMAL if enabled else tk.DISABLED))
            except Exception:
                pass

    # ─── Build all screens ────────────────────────────────────────────────────
    def _build_all_screens(self):
        self._build_camera()
        self._build_speaker()
        self._build_keyboard()
        self._build_sysinfo()
        self._build_final()

    # =========================================================================
    # 1. CAMERA
    # =========================================================================
    def _build_camera(self):
        f = tk.Frame(self.root, bg=BG)
        self._frames["camera"] = f

        self._title(f, "Camera Test")

        self._cam_canvas = tk.Canvas(f, bg="#000000", highlightthickness=0)
        self._cam_canvas.pack(fill=tk.BOTH, expand=True, padx=40, pady=10)

        self._nav_bar(f, show_back=False)

        self._cap           = None
        self._cam_running   = False
        self._cam_photo     = None
        self._cam_failcount = 0
        self._cam_index     = None
        self._cam_next_retry_ms = 500

    def _on_show_camera(self):
        self._cam_running   = True
        self._cam_failcount = 0
        self._cam_index     = None
        self._cam_next_retry_ms = 300

        if not CV_AVAILABLE:
            self._cam_show_message("OpenCV (cv2) not installed\n(camera test unavailable)", RED)
            return

        # Try to open a camera quickly, then start ticking
        self._try_open_camera()
        self.root.after(120, self._cam_tick)

    def _on_leave_camera(self):
        self._cam_running = False
        self._close_camera()

    def _close_camera(self):
        try:
            if self._cap:
                self._cap.release()
        except Exception:
            pass
        self._cap = None
        self._cam_index = None

    def _cam_show_message(self, text, color=FG):
        cw = self._cam_canvas.winfo_width()  or 640
        ch = self._cam_canvas.winfo_height() or 480
        self._cam_canvas.delete("all")
        self._cam_canvas.create_text(
            cw // 2, ch // 2,
            text=text,
            fill=color, font=self.fnt_med, justify=tk.CENTER
        )

    def _try_open_camera(self):
        """Try /dev/video0..3 until one opens; set low-res for speed/stability."""
        self._close_camera()

        for idx in range(0, 4):
            try:
                cap = cv2.VideoCapture(idx)
                if cap and cap.isOpened():
                    # Set conservative res to reduce CPU and driver issues
                    try:
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                        # some backends support this
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                    self._cap = cap
                    self._cam_index = idx
                    self._cam_failcount = 0
                    self._cam_next_retry_ms = 250
                    return True
                try:
                    cap.release()
                except Exception:
                    pass
            except Exception:
                continue

        self._cap = None
        self._cam_index = None
        return False

    def _cam_tick(self):
        if not self._cam_running:
            return

        # If Pillow missing, we can still show "camera active" message
        if not PIL_AVAILABLE:
            if self._cap and self._cap.isOpened():
                self._cam_show_message("Camera active\n(install Pillow for live preview)", FG)
            else:
                # Try to open camera with backoff
                opened = self._try_open_camera()
                if not opened:
                    self._cam_show_message("No camera detected\n(retrying…)", ORANGE)
                    self.root.after(min(self._cam_next_retry_ms, 2000), self._cam_tick)
                    self._cam_next_retry_ms = min(int(self._cam_next_retry_ms * 1.6), 2000)
                    return
            self.root.after(500, self._cam_tick)
            return

        # Pillow is available; attempt to read frames
        if not (self._cap and self._cap.isOpened()):
            opened = self._try_open_camera()
            if not opened:
                self._cam_show_message("No camera detected\n(retrying…)", ORANGE)
                self.root.after(min(self._cam_next_retry_ms, 2000), self._cam_tick)
                self._cam_next_retry_ms = min(int(self._cam_next_retry_ms * 1.6), 2000)
                return

        ret = False
        frame = None
        try:
            ret, frame = self._cap.read()
        except Exception:
            ret = False

        if not ret or frame is None:
            self._cam_failcount += 1
            if self._cam_failcount >= 10:
                # Re-open camera after repeated failures
                self._close_camera()
                self._cam_show_message("Camera read failed\n(retrying…)", ORANGE)
                self.root.after(min(self._cam_next_retry_ms, 2000), self._cam_tick)
                self._cam_next_retry_ms = min(int(self._cam_next_retry_ms * 1.6), 2000)
                return
            self.root.after(120, self._cam_tick)
            return

        self._cam_failcount = 0

        try:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = frame.shape[:2]
            cw = self._cam_canvas.winfo_width()
            ch = self._cam_canvas.winfo_height()

            if cw < 2 or ch < 2:
                self.root.after(60, self._cam_tick)
                return

            scale = min(cw / w, ch / h)
            nw = max(int(w * scale), 1)
            nh = max(int(h * scale), 1)
            img = Image.fromarray(frame).resize((nw, nh), Image.BILINEAR)
            self._cam_photo = ImageTk.PhotoImage(img)
            self._cam_canvas.delete("all")
            self._cam_canvas.create_image(cw // 2, ch // 2, anchor=tk.CENTER, image=self._cam_photo)
        except Exception:
            self._cam_show_message("Camera preview error\n(check Pillow/OpenCV)", RED)

        # run around 20–30 fps when working; slower machines can handle it
        self.root.after(40, self._cam_tick)

    # =========================================================================
    # 2. SPEAKER
    # =========================================================================
    def _build_speaker(self):
        f = tk.Frame(self.root, bg=BG)
        self._frames["speaker"] = f

        self._title(f, "Speaker Test")

        self._noise_player = WhiteNoisePlayer()

        centre = tk.Frame(f, bg=BG)
        centre.place(relx=0.5, rely=0.42, anchor=tk.CENTER)

        msg = "Press SPACEBAR or the button to test speakers"
        if not NP_AVAILABLE:
            msg = "NumPy not installed → white-noise generator unavailable\n(install python3-numpy)"

        self._spk_status = tk.Label(
            centre, text=msg,
            font=self.fnt_med, bg=BG, fg=FG, wraplength=int(self.W * 0.7)
        )
        self._spk_status.pack(pady=20)

        self._spk_btn = self._styled_button(
            centre, "▶   Play White Noise", self._toggle_noise, bg=GRAY
        )
        self._spk_btn.pack()

        self._spk_space_bound = False

        self._nav_bar(f)

    def _on_show_speaker(self):
        # bind space only while on speaker screen
        if not self._spk_space_bound:
            self.root.bind("<space>", self._spk_space)
            self._spk_space_bound = True

    def _spk_space(self, _event):
        if self.current_idx == SCREEN_ORDER.index("speaker"):
            self._toggle_noise()

    def _on_leave_speaker(self):
        self._noise_player.stop()
        try:
            self._spk_btn.config(text="▶   Play White Noise", bg=GRAY)
        except Exception:
            pass
        if NP_AVAILABLE:
            try:
                self._spk_status.config(text="Press SPACEBAR or the button to test speakers")
            except Exception:
                pass
        # unbind space to avoid toggles elsewhere
        try:
            self.root.unbind("<space>")
        except Exception:
            pass
        self._spk_space_bound = False

    def _toggle_noise(self):
        if not NP_AVAILABLE:
            self._spk_status.config(text="NumPy not installed → cannot generate white noise", fg=ORANGE)
            return

        if self._noise_player.playing:
            self._noise_player.stop()
            self._spk_btn.config(text="▶   Play White Noise", bg=GRAY)
            self._spk_status.config(text="Press SPACEBAR or the button to test speakers", fg=FG)
        else:
            self._noise_player.start()
            self._spk_btn.config(text="■   Stop", bg=RED)
            self._spk_status.config(text="Playing white noise…", fg=FG)

    # =========================================================================
    # 3. KEYBOARD
    # =========================================================================
    def _build_keyboard(self):
        f = tk.Frame(self.root, bg=BG)
        self._frames["keyboard"] = f

        self._title(f, "Keyboard Test")

        self._kbd_canvas = tk.Canvas(f, bg=BG, highlightthickness=0)
        self._kbd_canvas.pack(fill=tk.BOTH, expand=True, padx=20, pady=8)
        self._kbd_canvas.bind("<Configure>", lambda _: self._draw_keyboard())

        self._key_states = {}
        self._last_key   = None

        self._nav_bar(f)

    def _on_show_keyboard(self):
        # Bind only while this screen is active (stability)
        if not self._kbd_bound:
            self.root.bind("<KeyPress>", self._kbd_key_press)
            self.root.bind("<Shift-Right>", lambda e: self._go_next())
            self.root.bind("<Shift-Left>",  lambda e: self._go_back())
            self._kbd_bound = True
        self.root.after(60, self._draw_keyboard)

    def _on_leave_keyboard(self):
        # Unbind to avoid capturing keys globally
        if self._kbd_bound:
            try:
                self.root.unbind("<KeyPress>")
                self.root.unbind("<Shift-Right>")
                self.root.unbind("<Shift-Left>")
            except Exception:
                pass
            self._kbd_bound = False

    def _kbd_key_press(self, event):
        if self.current_idx != SCREEN_ORDER.index("keyboard"):
            return

        ksym = event.keysym

        if re.fullmatch(r"[Ff]\d+", ksym):
            name = ksym.upper()
        else:
            name_map = {
                "Return":           "ENTER",
                "Caps_Lock":        "CAPS",
                "Shift_L":          "SHIFT",
                "Shift_R":          "SHIFT",
                "Control_L":        "CTRL",
                "Control_R":        "CTRL",
                "Alt_L":            "ALT",
                "Alt_R":            "ALT",
                "Delete":           "DEL",
                "Insert":           "INS",
                "Prior":            "PGUP",
                "Next":             "PGDN",
                "BackSpace":        "BACKSPACE",
                "Tab":              "TAB",
                "Escape":           "ESC",
                "space":            "SPACE",
                "Up":               "↑",
                "Down":             "↓",
                "Left":             "←",
                "Right":            "→",
                "Home":             "HOME",
                "End":              "END",
                "minus":            "-",
                "equal":            "=",
                "bracketleft":      "[",
                "bracketright":     "]",
                "backslash":        "\\",
                "BackSlash":        "\\",
                "semicolon":        ";",
                "apostrophe":       "'",
                "grave":            "`",
                "comma":            ",",
                "period":           ".",
                "slash":            "/",
                "underscore":       "-",
                "plus":             "=",
                "braceleft":        "[",
                "braceright":       "]",
                "bar":              "\\",
                "colon":            ";",
                "quotedbl":         "'",
                "asciitilde":       "`",
                "less":             ",",
                "greater":          ".",
                "question":         "/",
            }
            name = name_map.get(ksym, ksym.upper())

        self._key_states[name] = True
        self._last_key = name
        self._draw_keyboard()

    def _draw_keyboard(self):
        c  = self._kbd_canvas
        cw = c.winfo_width()  or max(self.W - 80, 800)
        ch = c.winfo_height() or max(self.H - 220, 400)
        c.delete("all")

        f_keys    = ["ESC"] + [f"F{i}" for i in range(1, 13)]
        main_rows = [
            ["`","1","2","3","4","5","6","7","8","9","0","-","=","BACKSPACE"],
            ["TAB","Q","W","E","R","T","Y","U","I","O","P","[","]","\\"],
            ["CAPS","A","S","D","F","G","H","J","K","L",";","'","ENTER"],
            ["SHIFT","Z","X","C","V","B","N","M",",",".","/","SHIFT"],
        ]
        bottom_row = ["CTRL","FN","ALT","SPACE","ALT","CTRL"]
        nav_top    = ["INS","HOME","PGUP"]
        nav_bottom = ["DEL","END","PGDN"]

        width_mult = {
            "TAB": 1.5, "CAPS": 1.5, "ENTER": 1.5,
            "SHIFT": 2.0, "BACKSPACE": 1.8, "SPACE": 6.0,
        }

        base_kw = 56
        base_kh = 46
        base_g  = 6

        total_base_w = (15 + 1) * (base_kw + base_g)
        nav_cols_w   = 3 * (base_kw + base_g)
        total_w      = total_base_w + base_g * 3 + nav_cols_w

        num_rows     = 1 + len(main_rows) + 1
        total_base_h = num_rows * (base_kh + base_g) + (3 + 1) * (base_kh + base_g)

        scale = min(cw / total_w, ch / total_base_h, 1.0)
        kw    = max(int(base_kw * scale), 20)
        kh    = max(int(base_kh * scale), 16)
        g     = max(int(base_g  * scale), 3)
        kf    = f"Arial {max(int(10 * scale), 8)}"

        keyboard_w = int(15.3 * (kw + g))
        nav_w      = 3 * (kw + g)
        full_w     = keyboard_w + g * 4 + nav_w
        sx         = max((cw - full_w) // 2, 4)
        sy         = g * 2

        def draw_key(x, y, w, h, label):
            pressed = label in self._key_states
            is_last = label == self._last_key
            if is_last:
                fill, txt_col = LT_GREEN, "#000000"
            elif pressed:
                fill, txt_col = GREEN, "#000000"
            else:
                fill, txt_col = KEY_BG, "#111111"
            c.create_rectangle(x, y, x + w, y + h,
                               fill=fill, outline="#222222", width=1)
            c.create_rectangle(x + 2, y + 2, x + w - 2, y + h - 2,
                               fill=fill, outline="")
            c.create_text(x + w // 2, y + h // 2,
                          text=label, font=kf, fill=txt_col, anchor=tk.CENTER)

        x, y = sx, sy
        for key in f_keys:
            draw_key(x, y, kw, kh, key)
            x += kw + g
        y += kh + g * 2

        for row in main_rows:
            x = sx
            for key in row:
                w = int(kw * width_mult.get(key, 1.0))
                draw_key(x, y, w, kh, key)
                x += w + g
            y += kh + g

        x  = sx
        y += g
        for key in bottom_row:
            w = int(kw * width_mult.get(key, 1.0))
            draw_key(x, y, w, kh, key)
            x += w + g
        y += kh + g * 2

        nav_x = sx + keyboard_w + g * 4
        for i, key in enumerate(nav_top):
            draw_key(nav_x + i * (kw + g), y, kw, kh, key)
        for i, key in enumerate(nav_bottom):
            draw_key(nav_x + i * (kw + g), y + kh + g, kw, kh, key)

        arrow_y = y + 2 * (kh + g)
        draw_key(nav_x + kw,             arrow_y,             kw, kh, "↑")
        draw_key(nav_x,                  arrow_y + kh + g,    kw, kh, "←")
        draw_key(nav_x + kw,             arrow_y + kh + g,    kw, kh, "↓")
        draw_key(nav_x + 2 * (kw + g),   arrow_y + kh + g,    kw, kh, "→")

    # =========================================================================
    # 4. SYSTEM INFO
    # =========================================================================
    def _build_sysinfo(self):
        f = tk.Frame(self.root, bg=BG)
        self._frames["sysinfo"] = f

        self._title(f, "Full System Information")

        self._sysinfo_text = tk.Text(
            f, bg=BG, fg=FG, font=self.fnt_small,
            relief=tk.FLAT, state=tk.DISABLED,
            cursor="arrow", wrap=tk.WORD,
            selectbackground=BG, highlightthickness=0
        )
        self._sysinfo_text.pack(fill=tk.BOTH, expand=True, padx=50, pady=10)

        self._nav_bar(f)

    def _on_show_sysinfo(self):
        self._sysinfo_text.config(state=tk.NORMAL)
        self._sysinfo_text.delete("1.0", tk.END)
        self._sysinfo_text.insert(tk.END, "Loading…")
        self._sysinfo_text.config(state=tk.DISABLED)
        threading.Thread(target=self._load_sysinfo, daemon=True).start()

    def _load_sysinfo(self):
        manufacturer, model, serial = get_system_info()
        cpu_model, cores, threads   = get_cpu_info()
        total_ram, ram_slots        = get_ram_info()
        percent, health, cycles     = get_battery_info()

        lines = [
            "═══  SYSTEM  ═══",
            f"  Manufacturer  :  {manufacturer}",
            f"  Model         :  {model}",
            f"  Serial        :  {serial}",
            "",
            "═══  CPU  ═══",
            f"  Model         :  {cpu_model}",
            f"  Cores         :  {cores}    Threads : {threads}",
            "",
            "═══  RAM  ═══",
            f"  Total         :  {total_ram} GB" if total_ram != "Unavailable" else "  Total         :  Unavailable",
        ]
        for slot in ram_slots:
            lines.append(f"    {slot}")
        lines += [
            "",
            "═══  BATTERY  ═══",
            f"  Charge        :  {percent if percent is not None else 'N/A'}%",
            f"  Health        :  {health}",
            f"  Cycle Count   :  {cycles}",
        ]
        self.ui(self._set_sysinfo_text, "\n".join(lines))

    def _set_sysinfo_text(self, text):
        t = self._sysinfo_text
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        t.insert(tk.END, text)
        t.config(state=tk.DISABLED)

    # =========================================================================
    # 5. FINAL SCREEN
    # =========================================================================
    def _build_final(self):
        f = tk.Frame(self.root, bg=BG)
        self._frames["final"] = f

        top_bar = tk.Frame(f, bg=BG)
        top_bar.pack(fill=tk.X, padx=20, pady=(12, 0))
        self._wifi_lbl = tk.Label(top_bar, text="WiFi: checking…",
                                  font=self.fnt_small, bg=BG, fg=ORANGE)
        self._wifi_lbl.pack(side=tk.RIGHT)

        self._title(f, "Finished")

        self._server_lbl = tk.Label(
            f, text=f"Server: {load_saved_ip()}:{SERVER_PORT}",
            font=self.fnt_small, bg=BG, fg=LIGHT_GRAY
        )
        self._server_lbl.pack(pady=(4, 0))

        self._sync_status_lbl = tk.Label(
            f, text="", font=self.fnt_med, bg=BG, fg=FG
        )
        self._sync_status_lbl.pack(pady=10)

        self._wiz_outer = tk.Frame(f, bg=GRAY, bd=0, relief=tk.FLAT)
        self._wiz_outer.pack(fill=tk.X, padx=int(self.W * 0.1), pady=8)

        self._sync_ip_var    = tk.StringVar(value=load_saved_ip())
        self._sync_csad_var  = tk.StringVar()
        self._sync_cond_text = None
        self._sync_grade_var = tk.StringVar(value="A-Grade (Like-New)")

        self._wiz_pages = {}
        self._build_wiz_ip()
        self._build_wiz_connecting()
        self._build_wiz_csad()
        self._build_wiz_cond()
        self._build_wiz_grade()
        self._close_wizard()

        self._nav_bar(
            f, show_next=False,
            extra_buttons=[
                ("Sync to Log", self._start_sync, BLUE),
                ("Power Off",   self._power_off,  RED),
            ]
        )

        # Grab sync button widget for disable/enable
        self._sync_btn = None
        for txt, btn in getattr(self, "_extra_btn_widgets", []):
            if txt == "Sync to Log":
                self._sync_btn = btn
                break

        self._wifi_timer = None

    def _on_show_final(self):
        self._server_lbl.config(text=f"Server: {load_saved_ip()}:{SERVER_PORT}")
        self._wifi_refresh()

    def _on_leave_final(self):
        try:
            if self._wifi_timer:
                self.root.after_cancel(self._wifi_timer)
        except Exception:
            pass
        self._wifi_timer = None

    def _wifi_refresh(self):
        text, color = read_wifi_status()
        self._wifi_lbl.config(text=text, fg=color)
        self._wifi_timer = self.root.after(3000, self._wifi_refresh)

    # ── Wizard internals ──────────────────────────────────────────────────────
    def _build_wiz_ip(self):
        p = tk.Frame(self._wiz_outer, bg=GRAY, padx=20, pady=14)
        tk.Label(p, text="Enter Server IP Address:",
                 font=self.fnt_med, bg=GRAY, fg=FG).pack(anchor=tk.W)
        e = tk.Entry(p, textvariable=self._sync_ip_var,
                     font=self.fnt_large, bg=BG, fg=LT_GREEN,
                     insertbackground=LT_GREEN, relief=tk.FLAT, width=22)
        e.pack(anchor=tk.W, pady=6)
        e.bind("<Return>",  lambda _: self._wiz_ip_ok())
        e.bind("<Escape>",  lambda _: self._close_wizard())
        row = tk.Frame(p, bg=GRAY)
        row.pack(anchor=tk.W)
        self._styled_button(row, "OK",     self._wiz_ip_ok,    BLUE).pack(side=tk.LEFT, padx=(0, 8))
        self._styled_button(row, "Cancel", self._close_wizard, GRAY).pack(side=tk.LEFT)
        self._wiz_ip_entry = e
        self._wiz_pages["ip"] = p

    def _build_wiz_connecting(self):
        p = tk.Frame(self._wiz_outer, bg=GRAY, padx=20, pady=22)
        tk.Label(p, text="Connecting to server…",
                 font=self.fnt_med, bg=GRAY, fg=ORANGE).pack()
        self._wiz_pages["connecting"] = p

    def _build_wiz_csad(self):
        p = tk.Frame(self._wiz_outer, bg=GRAY, padx=20, pady=14)
        header = tk.Frame(p, bg=GRAY)
        header.pack(fill=tk.X)
        tk.Label(header, text="Enter CSAD (5 digits + letter) or leave empty:",
                 font=self.fnt_med, bg=GRAY, fg=FG).pack(side=tk.LEFT)
        self._csad_cnt = tk.Label(header, text="0/6",
                                  font=self.fnt_small, bg=GRAY, fg=LIGHT_GRAY)
        self._csad_cnt.pack(side=tk.RIGHT)
        e = tk.Entry(p, textvariable=self._sync_csad_var,
                     font=self.fnt_large, bg=BG, fg=LT_GREEN,
                     insertbackground=LT_GREEN, relief=tk.FLAT, width=12)
        e.pack(anchor=tk.W, pady=6)
        e.bind("<Return>",  lambda _: self._wiz_csad_ok())
        e.bind("<Escape>",  lambda _: self._close_wizard())
        self._sync_csad_var.trace_add(
            "write", lambda *_: self._csad_cnt.config(text=f"{len(self._sync_csad_var.get())}/6")
        )
        row = tk.Frame(p, bg=GRAY)
        row.pack(anchor=tk.W)
        self._styled_button(row, "OK",     self._wiz_csad_ok,  BLUE).pack(side=tk.LEFT, padx=(0, 8))
        self._styled_button(row, "Cancel", self._close_wizard, GRAY).pack(side=tk.LEFT)
        self._wiz_csad_entry = e
        self._wiz_pages["csad"] = p

    def _build_wiz_cond(self):
        p = tk.Frame(self._wiz_outer, bg=GRAY, padx=20, pady=14)
        tk.Label(p, text="Enter condition notes → then press OK to choose grade:",
                 font=self.fnt_med, bg=GRAY, fg=FG).pack(anchor=tk.W)
        t = tk.Text(p, font=self.fnt_med, bg=BG, fg=LT_GREEN,
                    insertbackground=LT_GREEN, relief=tk.FLAT,
                    height=3, wrap=tk.WORD, highlightthickness=0)
        t.pack(fill=tk.X, pady=6)
        t.bind("<Control-Return>", lambda _: self._wiz_cond_ok())
        row = tk.Frame(p, bg=GRAY)
        row.pack(anchor=tk.W)
        self._styled_button(row, "OK",     self._wiz_cond_ok,  BLUE).pack(side=tk.LEFT, padx=(0, 8))
        self._styled_button(row, "Cancel", self._close_wizard, GRAY).pack(side=tk.LEFT)
        self._sync_cond_text = t
        self._wiz_pages["cond"] = p

    def _build_wiz_grade(self):
        p = tk.Frame(self._wiz_outer, bg=GRAY, padx=20, pady=14)
        tk.Label(p, text="Select condition grade:",
                 font=self.fnt_med, bg=GRAY, fg=FG).pack(anchor=tk.W, pady=(0, 8))
        row = tk.Frame(p, bg=GRAY)
        row.pack(anchor=tk.W)
        for key, label in GRADE_MAP.items():
            self._styled_button(
                row, f"{key}  –  {label}",
                lambda l=label: self._wiz_grade_ok(l),
                BLUE
            ).pack(side=tk.LEFT, padx=(0, 8))
        self._styled_button(row, "Cancel", self._close_wizard, GRAY).pack(side=tk.LEFT)
        self._wiz_pages["grade"] = p

    # ── Wizard page switching ─────────────────────────────────────────────────
    def _show_wiz_page(self, name):
        for p in self._wiz_pages.values():
            p.pack_forget()
        self._wiz_pages[name].pack(fill=tk.X)

    def _close_wizard(self):
        for p in self._wiz_pages.values():
            p.pack_forget()

    # ── Wizard step callbacks ─────────────────────────────────────────────────
    def _start_sync(self):
        if self._sync_in_progress:
            return
        self._sync_status_lbl.config(text="", fg=FG)
        self._sync_ip_var.set(load_saved_ip())
        self._show_wiz_page("ip")
        self.root.after(50, lambda: (
            self._wiz_ip_entry.focus_set(),
            self._wiz_ip_entry.select_range(0, tk.END)
        ))

    def _wiz_ip_ok(self):
        if self._sync_in_progress:
            return
        ip = self._sync_ip_var.get().strip()
        if ip:
            save_ip(ip)
        self._show_wiz_page("connecting")
        threading.Thread(target=self._do_connect, args=(ip,), daemon=True).start()

    def _do_connect(self, ip):
        ok = ping_server(ip)
        if ok:
            self.ui(self._connected)
        else:
            self.ui(self._connect_failed, ip)

    def _connected(self):
        self._sync_csad_var.set("")
        if self._sync_cond_text:
            self._sync_cond_text.delete("1.0", tk.END)
        self._show_wiz_page("csad")
        self.root.after(50, lambda: self._wiz_csad_entry.focus_set())

    def _connect_failed(self, ip):
        self._close_wizard()
        self._sync_status_lbl.config(text=f"Failed to connect to {ip}:{SERVER_PORT}", fg=RED)

    def _wiz_csad_ok(self):
        if self._sync_in_progress:
            return
        val = self._sync_csad_var.get().strip()
        if val == "" or re.fullmatch(r"\d{5}[A-Za-z]", val):
            self._sync_csad_var.set(val.upper())
            self._show_wiz_page("cond")
            self.root.after(50, lambda: self._sync_cond_text.focus_set())
        else:
            self._sync_status_lbl.config(text="CSAD: 5 digits + one letter, or leave empty", fg=RED)

    def _wiz_cond_ok(self):
        if self._sync_in_progress:
            return
        self._show_wiz_page("grade")

    def _wiz_grade_ok(self, grade):
        if self._sync_in_progress:
            return
        self._sync_grade_var.set(grade)
        self._close_wizard()

        self._sync_in_progress = True
        if self._sync_btn:
            try:
                self._sync_btn.config(state=tk.DISABLED)
            except Exception:
                pass
        self._sync_status_lbl.config(text="Syncing…", fg=ORANGE)

        threading.Thread(target=self._do_submit_with_retry, args=(grade,), daemon=True).start()

    def _do_submit_with_retry(self, grade):
        # Up to 3 attempts with small backoff
        last_ok = False
        last_msg = "Unknown error"
        for attempt in range(1, 4):
            ok, msg = self._do_submit_once(grade)
            last_ok, last_msg = ok, msg
            if ok:
                break
            time.sleep(0.6 * attempt)

        if last_ok:
            self.ui(self._sync_status_lbl.config, text="✔  SYNC SUCCESSFUL", fg=GREEN)
        else:
            self.ui(self._sync_status_lbl.config, text=f"✘  SYNC FAILED: {last_msg}", fg=RED)

        def unlock():
            self._sync_in_progress = False
            if self._sync_btn:
                try:
                    self._sync_btn.config(state=tk.NORMAL)
                except Exception:
                    pass
        self.ui(unlock)

    def _do_submit_once(self, grade):
        try:
            _, model, serial      = get_system_info()
            cpu_string, _, _      = get_cpu_info()
            _, ram_slots          = get_ram_info()
            _, battery_health, _  = get_battery_info()
            condition = (self._sync_cond_text.get("1.0", tk.END).strip()
                         if self._sync_cond_text else "")

            payload = {
                "model":           model,
                "serial":          serial,
                "cpu_string":      cpu_string,
                "ram_slots":       ram_slots,
                "battery_health":  battery_health,
                "condition":       condition,
                "csad_value":      self._sync_csad_var.get().strip(),
                "condition_grade": grade,
            }
            ip = self._sync_ip_var.get().strip() or load_saved_ip()
            ok, msg = post_laptop_data(ip, payload)
            return ok, msg
        except Exception as e:
            return False, str(e)

    # ── Power off ─────────────────────────────────────────────────────────────
    def _power_off(self):
        ip = self._sync_ip_var.get().strip() or load_saved_ip()
        save_ip(ip)

        # Best effort to flush
        try:
            subprocess.call(["sync"])
            subprocess.call(["sync"])
        except Exception:
            pass

        # Try normal poweroff first
        for cmd in (["poweroff"], ["/sbin/poweroff"], ["shutdown", "-h", "now"]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # give it a moment; if it works, we won't return
                time.sleep(3)
                break
            except Exception:
                continue

        # If still alive, try sysrq poweroff
        try:
            with open("/proc/sys/kernel/sysrq", "w") as f:
                f.write("1")
            time.sleep(0.2)
            with open("/proc/sysrq-trigger", "w") as f:
                f.write("o")
            time.sleep(5)
        except Exception:
            pass

        # Last resort forced
        for cmd in (["poweroff", "-f"], ["/sbin/poweroff", "-f"]):
            try:
                subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(3)
            except Exception:
                continue

    # =========================================================================
    # Error screen helper
    # =========================================================================
    def show_fatal_error(self, message: str):
        try:
            # Clear and show a simple fatal message
            for f in self._frames.values():
                f.pack_forget()
            f = tk.Frame(self.root, bg=BG)
            f.pack(fill=tk.BOTH, expand=True)
            self._title(f, "ERROR")
            lbl = tk.Label(
                f,
                text=message,
                font=self.fnt_med,
                bg=BG,
                fg=RED,
                wraplength=int(self.W * 0.85),
                justify=tk.LEFT
            )
            lbl.pack(padx=40, pady=30, anchor=tk.W)
            self._styled_button(f, "Exit", self.quit_app, bg=RED).pack(padx=40, pady=10, anchor=tk.W)
        except Exception:
            # If even UI fails, just print
            print(message, file=sys.stderr)


# ─── Entry point with crash catcher ───────────────────────────────────────────
def _write_crash_log(exc: BaseException):
    try:
        tb = traceback.format_exc()
        with open(CRASH_LOG, "a") as f:
            f.write("\n" + "="*70 + "\n")
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write(tb + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        LaptopTester()
    except Exception as e:
        _write_crash_log(e)
        # Try to show a minimal Tk error window
        try:
            root = tk.Tk()
            root.configure(bg=BG)
            root.title("Laptop Hardware Tester - Crash")
            w = root.winfo_screenwidth()
            h = root.winfo_screenheight()
            try:
                root.attributes("-fullscreen", True)
            except Exception:
                pass
            msg = (
                "The tester crashed.\n\n"
                f"Error: {e}\n\n"
                f"A crash log was saved to:\n{CRASH_LOG}\n\n"
                "Take a photo of this screen, then reboot."
            )
            lbl = tk.Label(root, text=msg, bg=BG, fg=RED, font=("Arial", 18), justify=tk.LEFT,
                           wraplength=int(w * 0.85))
            lbl.pack(padx=40, pady=40, anchor=tk.W)
            btn = tk.Button(root, text="Exit", bg=RED, fg=FG, font=("Arial", 16),
                            relief=tk.FLAT, command=lambda: root.destroy())
            btn.pack(padx=40, pady=10, anchor=tk.W)
            root.mainloop()
        except Exception:
            # Fall back to stderr if GUI can't come up
            print("Tester crashed:", e, file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)
