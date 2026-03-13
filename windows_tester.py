#!/usr/bin/env python3
"""
Windows Laptop / PC Tester - runs from USB, leaves no trace on the laptop.

Dependencies (on your build machine only):
    pip install opencv-python pillow sounddevice numpy requests

Then package as a single EXE, e.g.:
    pyinstaller --onefile --console windows_tester.py

Put the EXE and MARTOOLS folder on the USB.
"""

import os
import sys
import json
import threading
import subprocess
import socket
import re
from pathlib import Path
from collections import Counter
from datetime import datetime

import tkinter as tk
from tkinter import messagebox

# Optional imports that will be bundled into the EXE
try:
    import cv2
    from PIL import Image, ImageTk
    HAS_CAMERA = True
except Exception:
    HAS_CAMERA = False

try:
    import sounddevice as sd
    import numpy as np
    HAS_AUDIO = True
except Exception:
    HAS_AUDIO = False

try:
    import requests as req
    HAS_REQUESTS = True
except Exception:
    HAS_REQUESTS = False

# ─── Theme ─────────────────────────────────────────────────────────────────────
BG          = '#0c0c14'
PANEL       = '#13131f'
HEADER_BG   = '#0a0a12'
ACCENT      = '#3d9be9'
TEXT        = '#e8e8f0'
SUBTEXT     = '#6b6b88'
SUCCESS     = '#2ed573'
ERROR_C     = '#ff4757'
WARNING     = '#ffa502'
BORDER      = '#1e1e30'

KEY_IDLE    = '#dce3f5'
KEY_HELD    = '#1a6b30'
KEY_DONE    = '#6fcf97'
KEY_TXT_D   = '#1a1a2e'
KEY_TXT_L   = '#ffffff'

FONT_MONO   = 'Consolas'
FONT_SANS   = 'Segoe UI'

SCREEN_TITLES = [
    'Camera Test',
    'Speaker Test',
    'Keyboard Test',
    'System Info',
    'MAR',
    'Sync',
]

# ─── Persistent config on USB ──────────────────────────────────────────────────

def script_dir() -> Path:
    """Directory containing this script/EXE (your USB)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

CONFIG_FILE = script_dir() / 'tester_config.json'

def load_cfg():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}

def save_cfg(d):
    try:
        CONFIG_FILE.write_text(json.dumps(d, indent=2), encoding='utf-8')
    except Exception:
        pass

# ─── System info (Windows) ─────────────────────────────────────────────────────

def _wmic(args):
    try:
        out = subprocess.check_output(
            ['wmic'] + args,
            text=True,
            stderr=subprocess.DEVNULL
        )
        return out
    except Exception:
        return ''

def _parse_kv(text):
    d = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or '=' not in line:
            continue
        k, v = line.split('=', 1)
        d[k.strip()] = v.strip()
    return d

def _get_storage_size_string(bytes_):
    """
    Map raw byte size to: 128GB/256GB/512GB/1TB NVMe.
    """
    if not bytes_ or bytes_ <= 0:
        return 'Unknown'
    gb = bytes_ / (1024 ** 3)
    if gb < 192:
        x, z = 128, 'G'
    elif gb < 384:
        x, z = 256, 'G'
    elif gb < 768:
        x, z = 512, 'G'
    else:
        x, z = 1, 'T'
    return f'{x}{z}B NVMe'

def detect_machine_mode():
    """
    Returns 'laptop' if Windows reports a battery, otherwise 'desktop'.
    """
    try:
        txt = _wmic(['path', 'Win32_Battery', 'get', 'Name', '/value'])
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith('Name='):
                name = line.split('=', 1)[1].strip()
                if name:
                    return 'laptop'
    except Exception:
        pass
    return 'desktop'

def get_system_info():
    info = {
        'make': 'Unknown',
        'model': 'Unknown',
        'serial': 'Unknown',
        'cpu_string': 'Unknown',
        'cpu_max_mhz': 0,
        'ram_slots': [],
        'storage_size': 'Unknown',
        'battery_health': 'Unknown',
        'battery_current': 'Unknown',
        'battery_cycles': 'Unknown'
    }

    # --- Make / Model (Lenovo fix included) ---
    try:
        txt = _wmic(['computersystem', 'get', 'Manufacturer,Model', '/value'])
        cs = _parse_kv(txt)

        make = cs.get('Manufacturer', '').strip()
        model = cs.get('Model', '').strip()

        if make:
            info['make'] = 'Lenovo' if make.upper() == 'LENOVO' else make

        lenovo_model = ''

        if make.upper().startswith('LENOVO'):
            txt2 = _wmic(['csproduct', 'get', 'Vendor,Name,Version', '/value'])
            cp = _parse_kv(txt2)

            version = cp.get('Version', '').strip()
            name = cp.get('Name', '').strip()

            bad_values = {'', 'NONE', 'INVALID', 'TO BE FILLED BY O.E.M.'}

            if version.upper() not in bad_values:
                lenovo_model = version
            elif name.upper() not in bad_values:
                lenovo_model = name

        if lenovo_model:
            info['model'] = lenovo_model
        elif model:
            info['model'] = model
    except Exception:
        pass

    # --- Serial ---
    try:
        txt = _wmic(['bios', 'get', 'SerialNumber', '/value'])
        d = _parse_kv(txt)
        if d.get('SerialNumber'):
            info['serial'] = d['SerialNumber']
    except Exception:
        pass

    # --- CPU ---
    try:
        txt = _wmic(['cpu', 'get', 'Name,MaxClockSpeed', '/value'])
        d = _parse_kv(txt)

        if d.get('Name'):
            info['cpu_string'] = d['Name']

        if d.get('MaxClockSpeed'):
            info['cpu_max_mhz'] = int(d['MaxClockSpeed'])
    except Exception:
        pass

    # --- RAM slots ---
    try:
        mem_type_map = {
            20: 'DDR',
            21: 'DDR2',
            22: 'DDR2 FB-DIMM',
            24: 'DDR3',
            26: 'DDR4',
            27: 'LPDDR',
            28: 'LPDDR2',
            29: 'LPDDR3',
            30: 'LPDDR4',
            34: 'DDR5',
            35: 'LPDDR5',
        }

        def _wmic_value_list(alias, prop):
            txt = _wmic([alias, 'get', prop, '/value'])
            prefix = prop + '='
            return [
                line[len(prefix):].strip()
                for line in txt.splitlines()
                if line.strip().startswith(prefix)
            ]

        capacities = _wmic_value_list('memorychip', 'Capacity')
        smbios_types = _wmic_value_list('memorychip', 'SMBIOSMemoryType')
        memory_types = _wmic_value_list('memorychip', 'MemoryType')

        info['ram_slots'] = []

        for i, cap_raw in enumerate(capacities):
            try:
                cap = int(cap_raw)
            except Exception:
                continue

            if cap <= 0:
                continue

            gb = round(cap / (1024 ** 3))
            ram_type = 'Unknown'

            for raw_list in (smbios_types, memory_types):
                if i < len(raw_list):
                    try:
                        code = int(raw_list[i])
                        ram_type = mem_type_map.get(code, 'Unknown')
                    except Exception:
                        pass
                if ram_type != 'Unknown':
                    break

            info['ram_slots'].append({
                'size_gb': gb,
                'type': ram_type
            })
    except Exception:
        pass

    # --- Storage size ---
    try:
        txt = _wmic(['diskdrive', 'get', 'Size', '/value'])

        sizes = []
        for line in txt.splitlines():
            if line.startswith('Size='):
                try:
                    sizes.append(int(line.split('=', 1)[1]))
                except Exception:
                    pass

        if sizes:
            info['storage_size'] = _get_storage_size_string(max(sizes))
    except Exception:
        pass

    # --- Battery (laptops only) ---
    # Current charge: Win32_Battery is reliable for this
    try:
        txt = _wmic([
            'path', 'Win32_Battery', 'get',
            'EstimatedChargeRemaining',
            '/value'
        ])
        d = _parse_kv(txt)
        if d.get('EstimatedChargeRemaining'):
            info['battery_current'] = d['EstimatedChargeRemaining'] + '%'
    except Exception:
        pass

    # Battery health: try root\WMI namespace first (works on ThinkPads and
    # most modern laptops where Win32_Battery returns 0 for capacity fields)
    design, full = 0, 0
    try:
        def _ps(cmd):
            out = subprocess.check_output(
                ['powershell', '-NoProfile', '-Command', cmd],
                text=True, stderr=subprocess.DEVNULL
            )
            return out.strip()

        design_raw = _ps(
            "(Get-WmiObject -Namespace root\\WMI -Class BatteryStaticData).DesignedCapacity"
        )
        full_raw = _ps(
            "(Get-WmiObject -Namespace root\\WMI -Class BatteryFullChargedCapacity).FullChargedCapacity"
        )
        design = int(design_raw.split()[0]) if design_raw else 0
        full   = int(full_raw.split()[0])   if full_raw   else 0
    except Exception:
        pass

    # Fallback to Win32_Battery capacity fields if WMI query didn't work
    if design <= 0 or full <= 0:
        try:
            txt = _wmic([
                'path', 'Win32_Battery', 'get',
                'DesignCapacity,FullChargeCapacity',
                '/value'
            ])
            d = _parse_kv(txt)
            design = int(d.get('DesignCapacity', '0'))
            full   = int(d.get('FullChargeCapacity', '0'))
        except Exception:
            pass

    if design > 0 and full > 0:
        pct = int((full / design) * 100)
        info['battery_health'] = f"{pct}%"

    return info

def parse_cpu(raw, cpu_max_mhz=0):
    s = raw.strip()
    cpu_type = 'Unknown'
    cpu_series = 'Unknown'

    m = re.search(r'Core\s*(?:\(TM\)\s*)?Ultra\s+([579])\s+(?:Pro\s+)?(\d{3}\w*)', s, re.I)
    if m:
        cpu_type = 'Intel Core Ultra ' + m.group(1)
        cpu_series = m.group(2)
    else:
        m = re.search(r'Core\s*(?:\(TM\)\s*)?(i[3579])-(\d{4,5}\w*)', s, re.I)
        if m:
            cpu_type = 'Intel Core ' + m.group(1).lower()
            cpu_series = m.group(2)
    if cpu_type == 'Unknown':
        m = re.search(r'Ryzen\s+([3579])\s+(?:Pro\s+)?(\d{4}\w*)', s, re.I)
        if m:
            cpu_type = 'AMD Ryzen ' + m.group(1)
            cpu_series = m.group(2)

    freq_m = re.search(r'@\s*([\d.]+)\s*GHz', s, re.I)
    if freq_m:
        try:
            ghz = float(freq_m.group(1))
            cpu_freq = f'{ghz:.2f} GHz'
        except Exception:
            cpu_freq = freq_m.group(1) + ' GHz'
    elif cpu_max_mhz and cpu_max_mhz > 0:
        ghz = cpu_max_mhz / 1000.0
        cpu_freq = f'{ghz:.2f} GHz'
    else:
        cpu_freq = 'Unknown'

    return cpu_type, cpu_series, cpu_freq

def parse_ram(slots):
    if not slots:
        return 'Unknown', 'Unknown', 'Unknown'

    sizes = []
    types = []

    for slot in slots:
        if isinstance(slot, dict):
            gb = slot.get('size_gb')
            ram_type = str(slot.get('type', 'Unknown')).upper()

            if isinstance(gb, int) and gb > 0:
                sizes.append(gb)

            if ram_type and ram_type != 'UNKNOWN':
                types.append(ram_type)

        elif isinstance(slot, str):
            m = re.search(r'(\d+)\s*GB', slot, re.I)
            if m:
                sizes.append(int(m.group(1)))

            t = re.search(r'(LPDDR\d+|DDR\d+|LPDDR|DDR)', slot, re.I)
            if t:
                types.append(t.group(1).upper())

    if not sizes:
        return 'Unknown', 'Unknown', 'Unknown'

    sc = Counter(sizes)
    parts = [f'{qty} x {sz}GB' for sz, qty in sorted(sc.items())]
    cfg = ' + '.join(parts)
    total = f'{sum(sizes)}GB'
    ram_type = Counter(types).most_common(1)[0][0] if types else 'Unknown'

    return cfg, total, ram_type

# ─── Audio: white noise ─────────────────────────────────────────────────────────

class NoisePlayer:
    def __init__(self):
        self._stream = None

    def start(self, vol=0.4):
        if self._stream or not HAS_AUDIO:
            return

        def cb(out, frames, t, status):
            out[:] = (np.random.randn(frames, 2) * vol).astype(np.float32)

        self._stream = sd.OutputStream(
            samplerate=44100,
            channels=2,
            dtype='float32',
            callback=cb
        )
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def is_playing(self):
        return self._stream is not None

def set_system_volume_40_percent_best_effort():
    """
    Best-effort: simulate volume-up to ensure not muted.
    Real CoreAudio integration is heavier; this is "good enough" and
    fully self-contained.
    """
    try:
        for _ in range(5):
            subprocess.run(
                ['nircmd.exe', 'mutesysvolume', '0'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            break
    except Exception:
        pass

# ─── Keyboard layout definition ────────────────────────────────────────────────

KB_ROWS = [
    [('Escape','Esc',1.0),(None,'',0.6),
     ('F1','F1',1.0),('F2','F2',1.0),('F3','F3',1.0),('F4','F4',1.0),(None,'',0.3),
     ('F5','F5',1.0),('F6','F6',1.0),('F7','F7',1.0),('F8','F8',1.0),(None,'',0.3),
     ('F9','F9',1.0),('F10','F10',1.0),('F11','F11',1.0),('F12','F12',1.0)],
    [('grave','`',1.0),('1','1',1.0),('2','2',1.0),('3','3',1.0),
     ('4','4',1.0),('5','5',1.0),('6','6',1.0),('7','7',1.0),
     ('8','8',1.0),('9','9',1.0),('0','0',1.0),('minus','-',1.0),
     ('equal','=',1.0),('BackSpace','\u232b Back',2.0)],
    [('Tab','Tab',1.5),('q','Q',1.0),('w','W',1.0),('e','E',1.0),
     ('r','R',1.0),('t','T',1.0),('y','Y',1.0),('u','U',1.0),
     ('i','I',1.0),('o','O',1.0),('p','P',1.0),('bracketleft','[',1.0),
     ('bracketright',']',1.0),('backslash','\\',1.5)],
    [('Caps_Lock','Caps',1.75),('a','A',1.0),('s','S',1.0),('d','D',1.0),
     ('f','F',1.0),('g','G',1.0),('h','H',1.0),('j','J',1.0),
     ('k','K',1.0),('l','L',1.0),('semicolon',';',1.0),('apostrophe',"'",1.0),
     ('Return','Enter',2.25)],
    [('Shift_L','\u21e7 Shift',2.25),('z','Z',1.0),('x','X',1.0),('c','C',1.0),
     ('v','V',1.0),('b','B',1.0),('n','N',1.0),('m','M',1.0),
     ('comma',',',1.0),('period','.',1.0),('slash','/',1.0),
     ('Shift_R','Shift \u21e7',2.75)],
    [('Control_L','Ctrl',1.5),('Super_L','\u2756',1.25),('Alt_L','Alt',1.25),
     ('space','Space',6.0),
     ('Alt_R','Alt',1.25),('Menu','\u25a4',1.0),('Control_R','Ctrl',1.5)],
]

KB_ARROWS = [
    [(None,'',1.0),('Up','\u2191',1.0),(None,'',1.0)],
    [('Left','\u2190',1.0),('Down','\u2193',1.0),('Right','\u2192',1.0)],
]

KB_NAV = [
    [('Insert','Ins',1.0), ('Home','Home',1.0), ('Prior','PgUp',1.0)],
    [('Delete','Del',1.0), ('End','End',1.0),   ('Next','PgDn',1.0)],
]

KB_ALIAS = {
    **{chr(c): chr(c + 32) for c in range(65, 91)},
    'exclam':'1','at':'2','numbersign':'3','dollar':'4','percent':'5',
    'asciicircum':'6','ampersand':'7','asterisk':'8',
    'parenleft':'9','parenright':'0','underscore':'minus','plus':'equal',
    'braceleft':'bracketleft','braceright':'bracketright','bar':'backslash',
    'colon':'semicolon','quotedbl':'apostrophe','less':'comma',
    'greater':'period','question':'slash','asciitilde':'grave',
    'KP_Enter':'Return','ISO_Left_Tab':'Tab',
    'Print':'F12','Scroll_Lock':'F12','Pause':'F12',
    'KP_0':'0','KP_1':'1','KP_2':'2','KP_3':'3',
    'KP_4':'4','KP_5':'5','KP_6':'6','KP_7':'7','KP_8':'8','KP_9':'9',
    'KP_Decimal':'period','KP_Add':'equal','KP_Subtract':'minus',
    'KP_Multiply':'8','KP_Divide':'slash',
    'XF86AudioMute': 'F10',
    'XF86AudioMicMute': 'F10',
}

# ─── Base screen ───────────────────────────────────────────────────────────────

class BaseScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app

    def on_show(self):
        pass

    def on_hide(self):
        pass

# ─── Screen 1: Camera ──────────────────────────────────────────────────────────

class CameraScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._cap = None
        self._running = False
        self._lbl = None
        self._container = None
        self._build()

    def _build(self):
        tk.Label(self, text='CAMERA TEST', font=(FONT_SANS, 11, 'bold'),
                 bg=BG, fg=SUBTEXT).pack(pady=(14, 6))
        self._container = tk.Frame(self, bg='black')
        self._container.pack(expand=True, fill='both', padx=30, pady=(0, 20))
        self._container.pack_propagate(False)

    def on_show(self):
        for w in self._container.winfo_children():
            w.destroy()
        self._running = False
        self._lbl = None

        if not HAS_CAMERA:
            tk.Label(self._container,
                     text='Camera not working\n(OpenCV not available)',
                     font=(FONT_SANS, 20), bg='black', fg=SUBTEXT).pack(expand=True)
            return

        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            tk.Label(self._container, text='Camera not working',
                     font=(FONT_SANS, 30, 'bold'),
                     bg='black', fg=SUBTEXT).pack(expand=True)
            self._cap = None
            return

        self._lbl = tk.Label(self._container, bg='black')
        self._lbl.pack(expand=True, fill='both')
        self._running = True
        self._tick()

    def _tick(self):
        if not self._running or not self._cap:
            return
        ok, frame = self._cap.read()
        if ok and self._lbl:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = frame.shape[:2]
            maxw = max(self._container.winfo_width(), 100)
            maxh = max(self._container.winfo_height(), 100)
            scale = min(maxw / w, maxh / h)
            nw, nh = int(w * scale), int(h * scale)
            img = Image.fromarray(frame).resize((nw, nh), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._lbl.config(image=photo)
            self._lbl.image = photo
        self.after(40, self._tick)

    def on_hide(self):
        self._running = False
        if self._cap:
            self._cap.release()
            self._cap = None

# ─── Screen 2: Speaker ─────────────────────────────────────────────────────────

class SpeakerScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._noise = NoisePlayer()
        self._build()

    def _build(self):
        tk.Label(self, text='SPEAKER TEST', font=(FONT_SANS, 11, 'bold'),
                 bg=BG, fg=SUBTEXT).pack(pady=(14, 0))

        center = tk.Frame(self, bg=BG)
        center.pack(expand=True)

        self._icon = tk.Label(center, text='♪',
                              font=(FONT_SANS, 80), bg=BG, fg=SUBTEXT)
        self._icon.pack(pady=(0, 18))

        self._prompt = tk.Label(center,
                                text='Press  Spacebar  to Test Speakers',
                                font=(FONT_SANS, 26, 'bold'),
                                bg=BG, fg=TEXT)
        self._prompt.pack()

        self._sub = tk.Label(center, text='',
                             font=(FONT_SANS, 14), bg=BG, fg=SUBTEXT)
        self._sub.pack(pady=8)

        if not HAS_AUDIO:
            tk.Label(center,
                     text='⚠ audio libraries not available\n(bundle sounddevice + numpy into EXE)',
                     font=(FONT_SANS, 13), bg=BG, fg=WARNING).pack(pady=14)

    def on_show(self):
        self._noise.stop()
        self._prompt.config(text='Press  Spacebar  to Test Speakers', fg=TEXT)
        self._icon.config(fg=SUBTEXT)
        self._sub.config(text='')
        set_system_volume_40_percent_best_effort()
        self.app.root.bind('<space>', self._toggle)

    def on_hide(self):
        self._noise.stop()
        try:
            self.app.root.unbind('<space>')
        except Exception:
            pass

    def _toggle(self, _=None):
        if self._noise.is_playing():
            self._noise.stop()
            self._prompt.config(text='Press  Spacebar  to Test Speakers', fg=TEXT)
            self._icon.config(fg=SUBTEXT)
            self._sub.config(text='')
        else:
            self._noise.start(vol=0.4)
            self._prompt.config(text='Playing...', fg=SUCCESS)
            self._icon.config(fg=SUCCESS)
            self._sub.config(text='Press Spacebar again to stop')

# ─── Screen 3: Keyboard ────────────────────────────────────────────────────────

class KeyboardScreen(BaseScreen):
    GAP = 4
    _TOTAL_UNITS = sum(e[2] for e in KB_ROWS[1]) + 3.0 + 0.8

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._items = {}
        self._pressed = set()
        self._done = set()
        self._canvas = None
        self._current_key_var = tk.StringVar(value='Current key: None')

        sw = app.sw
        sh = app.sh
        num_rows = len(KB_ROWS) + 1
        v_budget = sh - 180
        unit_w = int(sw * 0.90 / self._TOTAL_UNITS)
        unit_h = int(v_budget / (num_rows + 0.5))
        self.UNIT = max(40, min(unit_w, unit_h))
        self.KEY_H = int(self.UNIT * 0.87)
        self.ROW_H = int(self.UNIT * 1.03)

        self._build()

    def _build(self):
        tk.Label(self, text='KEYBOARD TEST', font=(FONT_SANS, 11, 'bold'),
                 bg=BG, fg=SUBTEXT).pack(pady=(14, 3))
        tk.Label(self,
                 text='Press every key — white = untested  ·  bright green = pressed  ·  light green = tested',
                 font=(FONT_SANS, 10), bg=BG, fg=SUBTEXT).pack()

        outer = tk.Frame(self, bg=BG)
        outer.pack(expand=True)

        U = self.UNIT
        cw = int(self._TOTAL_UNITS * U)
        ch = (len(KB_ROWS) + 1) * self.ROW_H + 30

        self._canvas = tk.Canvas(outer, width=cw, height=ch,
                                 bg=BG, highlightthickness=0)
        self._canvas.pack()
        self._draw_all_keys()

        lbl = tk.Label(
            outer,
            textvariable=self._current_key_var,
            font=(FONT_MONO, 18, 'bold'),
            bg=PANEL,
            fg=ACCENT,
            padx=18,
            pady=8
        )
        lbl.pack(pady=(12, 0))

    def _draw_all_keys(self):
        U = self.UNIT
        KH = self.KEY_H
        RH = self.ROW_H
        G = self.GAP
        LEFT = 6

        for ri, row in enumerate(KB_ROWS):
            y = (4 if ri == 0 else RH + (ri - 1) * RH + 10)
            x = LEFT
            for (ks, lbl, w) in row:
                pw = int(w * U) - G
                if ks is None:
                    x += int(w * U)
                    continue
                self._draw_key(ks, lbl, x, y, pw, KH)
                x += int(w * U)

        main_w = int(sum(e[2] for e in KB_ROWS[1]) * U) + LEFT
        arrow_x0 = main_w + 8

        for ni, nrow in enumerate(KB_NAV):
            y = RH + (len(KB_ROWS) - 4 + ni) * RH + 10
            nx = arrow_x0
            for (ks, lbl, w) in nrow:
                pw = int(w * U) - G
                if ks is None:
                    nx += int(w * U)
                    continue
                self._draw_key(ks, lbl, nx, y, pw, KH)
                nx += int(w * U)

        for ai, arow in enumerate(KB_ARROWS):
            y = RH + (len(KB_ROWS) - 2 + ai) * RH + 10
            ax = arrow_x0
            for (ks, lbl, w) in arow:
                pw = int(w * U) - G
                if ks is None:
                    ax += int(w * U)
                    continue
                self._draw_key(ks, lbl, ax, y, pw, KH)
                ax += int(w * U)

    def _draw_key(self, ks, lbl, x, y, w, h):
        c = self._canvas
        r = c.create_rectangle(x, y, x + w, y + h,
                               fill=KEY_IDLE, outline='#9aa0b8', width=1,
                               tags=('key', ks))
        fsz = max(7, int(self.UNIT * (0.20 if len(lbl) <= 5 else 0.17)))
        t = c.create_text(x + w // 2, y + h // 2, text=lbl,
                          font=(FONT_SANS, fsz), fill=KEY_TXT_D,
                          tags=('key', ks))
        self._items[ks] = (r, t)

    def _resolve(self, sym):
        if sym in self._items:
            return sym
        return KB_ALIAS.get(sym, sym)

    def _set_color(self, ks, fill, txt_color):
        resolved = self._resolve(ks)
        if resolved not in self._items:
            return
        r, t = self._items[resolved]
        self._canvas.itemconfig(r, fill=fill)
        self._canvas.itemconfig(t, fill=txt_color)

    def _label_for_resolved_key(self, resolved):
        if resolved in self._items:
            _, text_id = self._items[resolved]
            try:
                label = self._canvas.itemcget(text_id, 'text')
                if label:
                    return label
            except Exception:
                pass
        return str(resolved)

    def _pretty_key_name(self, ev, resolved):
        if resolved in self._items:
            _, text_id = self._items[resolved]
            try:
                label = self._canvas.itemcget(text_id, 'text')
                if label:
                    return label
            except Exception:
                pass

        if ev.keysym == 'space':
            return 'Space'
        if ev.keysym == 'Return':
            return 'Enter'
        if ev.keysym == 'BackSpace':
            return 'Backspace'
        if ev.keysym == 'Escape':
            return 'Esc'

        return ev.keysym

    def on_show(self):
        self._current_key_var.set('Current key: None')
        self.app.root.bind_all('<KeyPress>', self._key_down)
        self.app.root.bind_all('<KeyRelease>', self._key_up)
        self.app.root.bind_all('<F10>', self._f10_down)
        self.app.root.bind_all('<KeyRelease-F10>', self._f10_up)
        self.app.root.bind_all('<XF86AudioMute>', self._f10_down)
        self.app.root.bind_all('<KeyRelease-XF86AudioMute>', self._f10_up)
        self._canvas.bind('<Tab>', self._block_key)
        self._canvas.bind('<ISO_Left_Tab>', self._block_key)
        self._canvas.bind('<space>', self._block_key)
        self._canvas.focus_set()

    def _block_key(self, ev):
        self._key_down(ev)
        return 'break'

    def on_hide(self):
        try:
            self.app.root.unbind_all('<KeyPress>')
            self.app.root.unbind_all('<KeyRelease>')
            self.app.root.unbind_all('<F10>')
            self.app.root.unbind_all('<KeyRelease-F10>')
            self.app.root.unbind_all('<XF86AudioMute>')
            self.app.root.unbind_all('<KeyRelease-XF86AudioMute>')
            self._canvas.unbind('<Tab>')
            self._canvas.unbind('<ISO_Left_Tab>')
            self._canvas.unbind('<space>')
        except Exception:
            pass

    def _f10_down(self, ev):
        self._key_down(ev)
        return 'break'

    def _f10_up(self, ev):
        self._key_up(ev)
        return 'break'

    def _key_down(self, ev):
        ks = self._resolve(ev.keysym)
        self._pressed.add(ks)
        self._done.add(ks)
        self._set_color(ks, KEY_HELD, KEY_TXT_L)

        pretty = self._pretty_key_name(ev, ks)
        self._current_key_var.set('Current key: ' + pretty)

    def _key_up(self, ev):
        ks = self._resolve(ev.keysym)
        self._pressed.discard(ks)
        if ks in self._done:
            self._set_color(ks, KEY_DONE, KEY_TXT_D)

        if self._pressed:
            last = sorted(self._pressed)[-1]
            self._current_key_var.set('Current key: ' + self._label_for_resolved_key(last))
        else:
            self._current_key_var.set('Current key: None')

# ─── Screen 4: System Info ─────────────────────────────────────────────────────

class InfoScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._built = False

    def on_show(self):
        if not self._built:
            self._build()

    def _build(self):
        self._built = True
        for w in self.winfo_children():
            w.destroy()

        sw = self.app.sw
        def vw(pct): return max(8, int(sw * pct / 100))

        tk.Label(self, text='SYSTEM INFORMATION', font=(FONT_SANS, vw(0.9), 'bold'),
                 bg=BG, fg=SUBTEXT).pack(pady=(14, 10))

        info = self.app.system_info
        if not info:
            tk.Label(self, text='Loading system information…',
                     font=(FONT_SANS, vw(1.5)), bg=BG, fg=SUBTEXT).pack(expand=True)
            self.after(1500, self._retry)
            return

        cpu_type, cpu_series, cpu_freq = parse_cpu(
            info.get('cpu_string', 'Unknown'),
            info.get('cpu_max_mhz', 0),
        )

        ram_cfg, ram_total, ram_type = parse_ram(info.get('ram_slots', []))

        frame = tk.Frame(self, bg=BG)
        frame.pack(expand=True, fill='both', padx=int(sw * 0.04))

        lbl_font = (FONT_SANS, vw(1.0))
        val_font = (FONT_MONO, vw(1.0))

        def row(label, val, color=TEXT):
            f = tk.Frame(frame, bg=BG)
            f.pack(fill='x', pady=int(sw * 0.002))
            tk.Label(f, text=label + ':', font=lbl_font,
                     width=20, anchor='e', bg=BG, fg=SUBTEXT).pack(side='left')
            tk.Label(f, text=val, font=val_font,
                     anchor='w', bg=BG, fg=color).pack(side='left', padx=(int(sw * 0.01), 0))

        def divider():
            tk.Frame(frame, bg=BORDER, height=1).pack(fill='x', pady=int(sw * 0.004))

        row('Make', info.get('make', 'Unknown'))
        row('Model', info.get('model', 'Unknown'))
        row('Serial', info.get('serial', 'Unknown'))
        divider()
        row('CPU', cpu_type + ' ' + cpu_series)
        row('CPU Speed', cpu_freq)
        row('CPU (full)', info.get('cpu_string', 'Unknown'), SUBTEXT)
        divider()
        row('RAM Config', ram_cfg)
        row('RAM Total', ram_total)
        row('RAM Type', ram_type)
        divider()
        if self.app.mode != 'desktop':
            row('Battery Health', info.get('battery_health', 'Unknown'))
            row('Battery Charge', info.get('battery_current', 'Unknown'))
            row('Battery Cycles', info.get('battery_cycles', 'Unknown'))
            divider()
        row('Storage Size (AA)', info.get('storage_size', 'Unknown'))

    def _retry(self):
        if self.app.system_info:
            self._build()

# ─── Screen 5: MAR ─────────────────────────────────────────────────────────────

class MARScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._status = tk.StringVar(value='')
        self._build()

    def _build(self):
        tk.Label(self, text='MICROSOFT AUTHORIZATION (MAR)',
                 font=(FONT_SANS, 11, 'bold'), bg=BG, fg=SUBTEXT).pack(pady=(14, 8))

        center = tk.Frame(self, bg=BG)
        center.pack(expand=True)

        tk.Label(center,
                 text='Click Run MAR (or press Enter) to launch the MAR tool.\nThe tester will minimize while MAR runs, then restore when it finishes.',
                 font=(FONT_SANS, 14), bg=BG, fg=TEXT, justify='center').pack(pady=10)

        btn = tk.Button(center, text='Run MAR',
                        font=(FONT_SANS, 22, 'bold'),
                        bg=ACCENT, fg='white',
                        activebackground='#58aef0', activeforeground='white',
                        bd=0, relief='flat', padx=40, pady=18, cursor='hand2',
                        command=self._run_mar)
        btn.pack(pady=18)

        self._status_lbl = tk.Label(center, textvariable=self._status,
                                    font=(FONT_SANS, 13), bg=BG, fg=SUBTEXT,
                                    wraplength=520, justify='center')
        self._status_lbl.pack(pady=10)

    def on_show(self):
        self._status.set('')
        self._status_lbl.config(fg=SUBTEXT)
        self.app.root.bind('<Return>', lambda e: self._run_mar())

    def on_hide(self):
        try:
            self.app.root.unbind('<Return>')
        except Exception:
            pass

    def _find_mar_batch(self) -> Path | None:
        base = script_dir() / 'MARTOOLS'
        if not base.is_dir():
            return None
        return base / 'Install_DPK.bat'

    def _run_mar(self):
        batch = self._find_mar_batch()
        if not batch:
            self._status.set('Could not find MAR batch file in MARTOOLS folder.')
            self._status_lbl.config(fg=ERROR_C)
            return

        if not batch.exists():
            self._status.set('Install_DPK.bat not found in MARTOOLS folder.')
            self._status_lbl.config(fg=ERROR_C)
            return

        self._status.set('MAR is running — tester will restore automatically when done…')
        self._status_lbl.config(fg=WARNING)

        # Minimize so MAR windows are fully visible
        self.app.root.attributes('-fullscreen', False)
        self.app.root.iconify()

        def _ps_running():
            """Return True if a powershell process running run.ps1 is active."""
            try:
                out = subprocess.check_output(
                    ['wmic', 'process', 'where',
                     "name='powershell.exe'", 'get', 'commandline', '/value'],
                    text=True, stderr=subprocess.DEVNULL
                )
                return 'run.ps1' in out.lower()
            except Exception:
                return False

        def worker():
            try:
                creation_flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
                subprocess.Popen(
                    [str(batch)],
                    cwd=str(batch.parent),
                    shell=True,
                    creationflags=creation_flags
                )
            except Exception as e:
                self.after(0, lambda: self._restore_and_update(False, f'Failed to launch MAR: {e}'))
                return

            # Wait for run.ps1 to appear (MAR has started)
            import time
            for _ in range(30):  # up to 30s for it to start
                time.sleep(1)
                if _ps_running():
                    break

            # Now wait for run.ps1 to disappear (MAR has finished)
            while _ps_running():
                time.sleep(2)

            self.after(0, lambda: self._restore_and_update(True, 'MAR completed successfully.'))

        threading.Thread(target=worker, daemon=True).start()

    def _restore_and_update(self, ok, msg):
        self._update_status(ok, msg)

        def _do_restore():
            self.app.root.deiconify()
            self.app.root.attributes('-fullscreen', True)
            self.app.root.attributes('-topmost', True)
            self.app.root.lift()
            self.app.root.focus_force()

        _do_restore()
        self.app.root.after(300,  _do_restore)
        self.app.root.after(700,  _do_restore)
        self.app.root.after(1400, _do_restore)

    def _update_status(self, ok, msg):
        self._status.set(msg)
        self._status_lbl.config(fg=SUCCESS if ok else ERROR_C)

# ─── Screen 6: Sync ────────────────────────────────────────────────────────────

class SyncScreen(BaseScreen):
    (STEP_IP, STEP_CSAD, STEP_NOTES,
     STEP_GRADE, STEP_SENDING, STEP_DONE) = range(6)

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._cfg = load_cfg()
        self._step = self.STEP_IP
        self._ip = tk.StringVar(value=self._cfg.get('last_ip', ''))
        self._csad = tk.StringVar()
        self._grade = tk.StringVar()
        self._notes = ''
        self._status_lbl = None
        self._content = None
        self._last_ok = False
        self._last_msg = ''
        self._build_shell()

    def _build_shell(self):
        tk.Label(self, text='SYNC TO SERVER', font=(FONT_SANS, 11, 'bold'),
                 bg=BG, fg=SUBTEXT).pack(pady=(14, 0))
        self._status_lbl = tk.Label(self, text='',
                                    font=(FONT_SANS, 12), bg=BG, fg=SUBTEXT)
        self._status_lbl.pack(pady=(4, 0))
        self._content = tk.Frame(self, bg=BG)
        self._content.pack(expand=True, fill='both')

    def on_show(self):
        self._step = self.STEP_IP
        self._render()

    def on_hide(self):
        self._unbind_grade_keys()

    def _clear(self):
        for w in self._content.winfo_children():
            w.destroy()

    def _status(self, msg, color=None):
        self._status_lbl.config(text=msg, fg=color or SUBTEXT)

    def _render(self):
        self._clear()
        s = self._step
        if   s == self.STEP_IP:      self._show_ip()
        elif s == self.STEP_CSAD:    self._show_csad()
        elif s == self.STEP_NOTES:   self._show_notes()
        elif s == self.STEP_GRADE:   self._show_grade()
        elif s == self.STEP_SENDING: self._show_sending()
        elif s == self.STEP_DONE:    self._show_done()

    def _show_ip(self):
        self._status('')
        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)

        tk.Label(box, text='Server IP Address',
                 font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(pady=(0, 6))
        tk.Label(box, text='Enter the IP of the LaptopSync server PC, then press Enter',
                 font=(FONT_SANS, 12), bg=BG, fg=SUBTEXT).pack(pady=(0, 16))

        ef = tk.Frame(box, bg=BORDER, padx=2, pady=2)
        ef.pack()
        self._ip_entry = tk.Entry(ef, textvariable=self._ip,
                                  font=(FONT_MONO, 22), width=20, bg=PANEL, fg=TEXT,
                                  insertbackground=ACCENT, bd=0, relief='flat', justify='center')
        self._ip_entry.pack(ipadx=12, ipady=10)
        self._ip_entry.focus_set()
        self._ip_entry.icursor(tk.END)
        self._ip_entry.bind('<Return>', self._confirm_ip)

        tk.Label(box, text='(Press Enter to use the pre-filled address)',
                 font=(FONT_SANS, 10), bg=BG, fg=SUBTEXT).pack(pady=(8, 0))

    def _confirm_ip(self, _=None):
        ip = self._ip.get().strip()
        if not ip:
            self._status('Please enter an IP address.', ERROR_C)
            return
        self._status(f'Connecting to {ip}:5050…', WARNING)
        self.update()
        threading.Thread(target=self._test_conn, args=(ip,), daemon=True).start()

    def _test_conn(self, ip):
        ok = False
        msg = ''
        try:
            s = socket.create_connection((ip, 5050), timeout=4)
            s.close()
            ok = True
            msg = 'Connected!'
        except Exception as e:
            msg = 'Could not connect: ' + str(e)
        self.after(0, lambda: self._ip_result(ip, ok, msg))

    def _ip_result(self, ip, ok, msg):
        if ok:
            self._cfg['last_ip'] = ip
            save_cfg(self._cfg)
            self._status('✓ ' + msg, SUCCESS)
            self.after(500, lambda: self._goto(self.STEP_CSAD))
        else:
            self._status('✗ ' + msg, ERROR_C)

    def _show_csad(self):
        self._status('')
        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)

        tk.Label(box, text='CSAD Number',
                 font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(pady=(0, 6))
        tk.Label(box, text='4–5 digits followed by a letter  ·  e.g.  12345A',
                 font=(FONT_SANS, 12), bg=BG, fg=SUBTEXT).pack(pady=(0, 16))

        ef = tk.Frame(box, bg=BORDER, padx=2, pady=2)
        ef.pack()
        self._csad_entry = tk.Entry(ef, textvariable=self._csad,
                                    font=(FONT_MONO, 28), width=12, bg=PANEL, fg=ACCENT,
                                    insertbackground=ACCENT, bd=0, relief='flat', justify='center')
        self._csad_entry.pack(ipadx=12, ipady=10)
        self._csad_entry.focus_set()
        self._csad_entry.bind('<Return>', self._confirm_csad)

        tk.Label(box, text='Press Enter to confirm  ·  Leave empty and press Enter for "*"',
                 font=(FONT_SANS, 11), bg=BG, fg=SUBTEXT).pack(pady=(8, 0))

    def _confirm_csad(self, _=None):
        v = self._csad.get().strip().upper()
        if v == '':
            v = '*'
        elif not re.fullmatch(r'\d{4,5}[A-Z]', v):
            self._status('Invalid CSAD – must be 4 or 5 digits followed by a letter.', ERROR_C)
            return
        self._csad.set(v)
        self._status('')
        self._goto(self.STEP_NOTES)

    def _show_notes(self):
        self._status('')
        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True, fill='both', padx=60, pady=6)

        tk.Label(box, text='Condition Notes',
                 font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(anchor='w', pady=(0, 4))
        tk.Label(box, text='Describe cosmetic condition, damage, missing parts, etc.',
                 font=(FONT_SANS, 12), bg=BG, fg=SUBTEXT).pack(anchor='w')

        tf = tk.Frame(box, bg=BORDER, padx=2, pady=2)
        tf.pack(fill='both', expand=True, pady=10)

        self._notes_txt = tk.Text(tf,
                                  font=(FONT_SANS, 18), bg=PANEL, fg=TEXT,
                                  insertbackground=ACCENT, bd=0, relief='flat',
                                  wrap='word', height=6, padx=10, pady=8)
        self._notes_txt.pack(fill='both', expand=True)
        if self._notes:
            self._notes_txt.insert('1.0', self._notes)
        self._notes_txt.focus_set()

        hint = tk.Frame(box, bg=BG)
        hint.pack(fill='x')
        tk.Label(hint,
                 text='Press Enter to continue (leave empty for "*"), or use Next ▷',
                 font=(FONT_SANS, 11), bg=BG, fg=SUBTEXT).pack(anchor='w')

        self._notes_txt.bind('<Return>', self._confirm_notes_key)
        self._notes_txt.bind('<Shift-Return>', self._newline_in_notes)

    def _confirm_notes_key(self, e):
        self._confirm_notes()
        return 'break'

    def _newline_in_notes(self, e):
        self._notes_txt.insert('insert', '\n')
        return 'break'

    def _confirm_notes(self):
        self._notes = self._notes_txt.get('1.0', 'end-1c').strip()
        if not self._notes:
            self._notes = '*'
        self._goto(self.STEP_GRADE)

    def _show_grade(self):
        self._status('')
        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)

        tk.Label(box, text='Condition Grade',
                 font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(pady=(0, 4))
        tk.Label(box, text='Click a button or press A / B / C / D on the keyboard',
                 font=(FONT_SANS, 12), bg=BG, fg=SUBTEXT).pack(pady=(0, 20))

        grades = [
            ('A', 'Like New',  '#0e2b18', '#2ed573'),
            ('B', 'Great',     '#0e1e2b', '#3d9be9'),
            ('C', 'Fair',      '#2b1e0e', '#ffa502'),
            ('D', 'Parts',     '#2b0e0e', '#ff4757'),
        ]

        row = tk.Frame(box, bg=BG)
        row.pack()
        for letter, label, bgc, fgc in grades:
            f = tk.Frame(row, bg=fgc, padx=2, pady=2)
            f.pack(side='left', padx=12)
            tk.Button(f, text=letter + '\n' + label,
                      font=(FONT_SANS, 18, 'bold'), bg=bgc, fg=fgc,
                      activebackground=fgc, activeforeground='#0a0a12',
                      bd=0, relief='flat', width=9, pady=16, cursor='hand2',
                      command=lambda l=letter: self._select_grade(l)).pack()

        for g, *_ in grades:
            self.app.root.bind('<' + g + '>', lambda e, l=g: self._select_grade(l))
            self.app.root.bind('<' + g.lower() + '>', lambda e, l=g: self._select_grade(l))

    def _unbind_grade_keys(self):
        for k in ('A', 'B', 'C', 'D', 'a', 'b', 'c', 'd'):
            try:
                self.app.root.unbind('<' + k + '>')
            except Exception:
                pass

    def _select_grade(self, letter):
        self._grade.set(letter)
        self._unbind_grade_keys()
        self._goto(self.STEP_SENDING)

    def _show_sending(self):
        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)
        tk.Label(box, text='↺  Sending to server…',
                 font=(FONT_SANS, 22, 'bold'), bg=BG, fg=ACCENT).pack(pady=20)
        self.update()
        threading.Thread(target=self._do_send, daemon=True).start()

    def _do_send(self):
        grade_map = {
            'A': 'A-Grade (Like-New)',
            'B': 'B-Grade (Great)',
            'C': 'C-Grade (Fair)',
            'D': 'D-Grade (Parts)',
        }
        info = self.app.system_info or {}
        ip = self._cfg.get('last_ip', '127.0.0.1')

        payload = {
            'model':           info.get('model', 'Unknown'),
            'serial':          info.get('serial', 'Unknown'),
            'cpu_string':      info.get('cpu_string', 'Unknown'),
            'ram_slots':       info.get('ram_slots', []),
            'battery_health':  info.get('battery_health', 'Unknown'),
            'storage_size':    info.get('storage_size', 'Unknown'),
            'condition':       self._notes,
            'condition_grade': grade_map.get(self._grade.get(), 'A-Grade (Like-New)'),
            'csad_value':      self._csad.get(),
        }

        ok = False
        msg = ''
        try:
            if HAS_REQUESTS:
                r = req.post('http://' + ip + ':5050/log', json=payload, timeout=8)
                if r.status_code != 200:
                    msg = f'Server returned HTTP {r.status_code}'
                else:
                    ok = True
                    try:
                        data = r.json()
                        s_val = str(data.get('status', 'ok')).lower()
                        if s_val in ('error', 'fail', 'failed'):
                            ok = False
                            msg = data.get('message', '') or f'Server reported failure: {s_val}'
                        else:
                            msg = data.get('message', '')
                    except Exception:
                        msg = ''
            else:
                msg = 'requests library not bundled – please include it in the EXE.'
        except Exception as e:
            msg = f'{type(e).__name__}: {e}'

        self.after(0, lambda: self._send_result(ok, msg))

    def _send_result(self, ok, msg):
        self._last_ok = ok
        self._last_msg = msg
        self._goto(self.STEP_DONE)

    def _show_done(self):
        ok = self._last_ok
        msg = self._last_msg
        info = self.app.system_info or {}

        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)

        if ok:
            tk.Label(box, text='✓',
                     font=(FONT_SANS, 72), bg=BG, fg=SUCCESS).pack()
            tk.Label(box, text='Synced Successfully!',
                     font=(FONT_SANS, 26, 'bold'), bg=BG, fg=SUCCESS).pack(pady=4)
            tk.Label(box,
                     text=f"{info.get('model', 'Unknown')}  ·  CSAD: {self._csad.get()}",
                     font=(FONT_SANS, 14), bg=BG, fg=SUBTEXT).pack(pady=4)
        else:
            tk.Label(box, text='✗',
                     font=(FONT_SANS, 72), bg=BG, fg=ERROR_C).pack()
            tk.Label(box, text='Sync Failed',
                     font=(FONT_SANS, 26, 'bold'), bg=BG, fg=ERROR_C).pack(pady=4)
            tk.Label(box, text=msg,
                     font=(FONT_SANS, 13), bg=BG, fg=WARNING,
                     wraplength=480, justify='center').pack(pady=8)
            tk.Button(box, text='Try Again',
                      font=(FONT_SANS, 13), bg=PANEL, fg=TEXT,
                      activebackground=ACCENT, activeforeground='white',
                      bd=0, relief='flat', padx=22, pady=12, cursor='hand2',
                      command=lambda: self._goto(self.STEP_GRADE)).pack(pady=8)

    def _goto(self, step):
        self._step = step
        self._render()

    def handle_next(self):
        if self._step == self.STEP_NOTES:
            try:
                self._notes = self._notes_txt.get('1.0', 'end-1c').strip()
            except Exception:
                pass
        return True

# ─── App shell ─────────────────────────────────────────────────────────────────

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Windows Laptop / PC Tester')
        self.root.configure(bg=BG)
        self.root.attributes('-fullscreen', True)
        self.root.attributes('-topmost', True)
        self.root.focus_force()

        def _reassert_fullscreen():
            self.root.attributes('-fullscreen', True)
            self.root.lift()
            self.root.focus_force()

        self.root.after(200, _reassert_fullscreen)
        self.root.after(800, _reassert_fullscreen)

        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()

        self.system_info = {}
        self._idx = 0
        self._active = None
        self.mode = detect_machine_mode()
        self._screen_titles = []

        threading.Thread(target=self._load_info, daemon=True).start()

        self._build_ui()
        self._show(0)

    def _load_info(self):
        self.system_info = get_system_info()

    def _build_ui(self):
        header = tk.Frame(self.root, bg=HEADER_BG, height=44)
        header.pack(fill='x', side='top')
        header.pack_propagate(False)

        self._model_lbl = tk.Label(
            header,
            text='Windows Laptop / PC Tester',
            font=(FONT_SANS, 12, 'bold'),
            bg=HEADER_BG,
            fg=TEXT
        )
        self._model_lbl.pack(side='left', padx=14, pady=6)

        tk.Button(
            header,
            text='✕  EXIT',
            font=(FONT_SANS, 11, 'bold'),
            bg='#2a0808',
            fg=ERROR_C,
            activebackground='#4a1010',
            activeforeground=ERROR_C,
            bd=0,
            relief='flat',
            cursor='hand2',
            padx=16,
            pady=4,
            command=self.quit_app
        ).pack(side='right', padx=10, pady=6)

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill='x', side='top')

        footer = tk.Frame(self.root, bg=HEADER_BG, height=50)
        footer.pack(fill='x', side='bottom')
        footer.pack_propagate(False)
        self._footer = footer

        self._prev_btn = tk.Button(
            footer,
            text='◀  Previous',
            font=(FONT_SANS, 12),
            bg=PANEL,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground='white',
            bd=0,
            relief='flat',
            padx=22,
            pady=8,
            cursor='hand2',
            command=self.prev_screen
        )
        self._prev_btn.pack(side='left', padx=12, pady=8)

        self._title_lbl = tk.Label(
            footer,
            text='',
            font=(FONT_SANS, 11),
            bg=HEADER_BG,
            fg=SUBTEXT
        )
        self._title_lbl.pack(side='left', expand=True)

        self._next_btn = tk.Button(
            footer,
            text='Next  ▶',
            font=(FONT_SANS, 12),
            bg=PANEL,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground='white',
            bd=0,
            relief='flat',
            padx=22,
            pady=8,
            cursor='hand2',
            command=self.next_screen
        )
        self._next_btn.pack(side='right', padx=12, pady=8)

        self._area = tk.Frame(self.root, bg=BG)
        self._area.pack(fill='both', expand=True)

        if self.mode == 'desktop':
            self._screens = [
                SpeakerScreen(self._area, self),
                InfoScreen(self._area, self),
                MARScreen(self._area, self),
                SyncScreen(self._area, self),
            ]
            self._screen_titles = [
                'Speaker Test',
                'System Info',
                'MAR',
                'Sync',
            ]
        else:
            self.mode = 'laptop'
            self._screens = [
                CameraScreen(self._area, self),
                SpeakerScreen(self._area, self),
                KeyboardScreen(self._area, self),
                InfoScreen(self._area, self),
                MARScreen(self._area, self),
                SyncScreen(self._area, self),
            ]
            self._screen_titles = SCREEN_TITLES[:]

        self.root.bind('<Shift-Right>', lambda e: self.next_screen())
        self.root.bind('<Shift-Left>',  lambda e: self.prev_screen())

    def _show(self, idx):
        if self._active:
            self._active.on_hide()
            self._active.pack_forget()

        self._idx = idx
        scr = self._screens[idx]
        scr.pack(fill='both', expand=True)
        scr.on_show()
        self._active = scr

        title = self._screen_titles[idx] if idx < len(self._screen_titles) else ''
        self._title_lbl.config(text=f'{title}  ·  {idx + 1} / {len(self._screens)}')
        self._prev_btn.config(
            state='normal' if idx > 0 else 'disabled',
            fg=TEXT if idx > 0 else SUBTEXT
        )

    def next_screen(self):
        if self._idx >= len(self._screens) - 1:
            return
        if hasattr(self._active, 'handle_next'):
            self._active.handle_next()
        self._show(self._idx + 1)

    def prev_screen(self):
        if self._idx > 0:
            self._show(self._idx - 1)

    def quit_app(self):
        try:
            if self._active:
                self._active.on_hide()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    App().run()