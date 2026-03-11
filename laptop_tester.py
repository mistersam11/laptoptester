#!/usr/bin/env python3
"""
LaptopTester - Linux laptop testing application
Collects hardware info and syncs to LaptopSync server (server.py)

Install dependencies:
    pip install opencv-python-headless pillow sounddevice numpy requests
    sudo apt install python3-tk
"""

import tkinter as tk
from tkinter import font as tkFont
import threading
import subprocess
import os, sys, json, time, re, socket
from pathlib import Path
from collections import Counter
from datetime import datetime

# ─── Optional imports ──────────────────────────────────────────────────────────
try:
    import cv2
    from PIL import Image, ImageTk
    HAS_CAMERA = True
except ImportError:
    HAS_CAMERA = False

try:
    import sounddevice as sd
    import numpy as np
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

try:
    import requests as req
    HAS_REQUESTS = True
except ImportError:
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

FONT_MONO   = 'Courier'
FONT_SANS   = 'DejaVu Sans'

SCREEN_TITLES = ['Camera Test', 'Speaker Test', 'Keyboard Test', 'System Info', 'Sync']

# ─── Persistent config ─────────────────────────────────────────────────────────
_FAKE_FS = {'tmpfs', 'ramfs', 'squashfs', 'aufs', 'overlay',
            'overlayfs', 'devtmpfs', 'sysfs', 'proc', 'cgroup',
            'cgroup2', 'pstore', 'efivarfs', 'securityfs', 'debugfs',
            'tracefs', 'configfs', 'fusectl', 'hugetlbfs', 'mqueue',
            'bpf', 'nfs', 'nfs4', 'iso9660', 'udf'}

def _find_persistent_dir():
    candidates = []
    try:
        mounts = Path('/proc/mounts').read_text().splitlines()
        for line in mounts:
            parts = line.split()
            if len(parts) < 3: continue
            device, mountpoint, fstype = parts[0], parts[1], parts[2]
            if fstype.lower() in _FAKE_FS: continue
            if not device.startswith('/dev/'): continue
            mp = Path(mountpoint)
            priority = 0 if str(mp).startswith('/mnt') else 1
            candidates.append((priority, mp))
    except: pass
    for letter in 'abcdefgh':
        for part in range(1, 5):
            candidates.append((2, Path('/mnt/sd' + letter + str(part))))
    candidates.sort(key=lambda x: x[0])
    for _, mp in candidates:
        try:
            if not mp.is_dir(): continue
            test = mp / '.tester_write_test'
            test.write_text('x')
            test.unlink()
            return mp
        except: continue
    return None

def _config_file():
    base = _find_persistent_dir()
    if base:
        d = base / 'LaptopTester'
        try:
            d.mkdir(exist_ok=True)
            return d / 'tester_config.json'
        except: pass
    return Path(sys.argv[0]).resolve().parent / '.tester_config.json'

CONFIG_FILE = _config_file()

def load_cfg():
    try: return json.loads(CONFIG_FILE.read_text())
    except: return {}

def save_cfg(d):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(d, indent=2))
    except: pass

# ─── System info ───────────────────────────────────────────────────────────────
def dmi_read(path):
    try:
        v = Path('/sys/class/dmi/id/' + path).read_text().strip()
        if v.lower() not in ('', 'unknown', 'not specified',
                              'to be filled by o.e.m.', 'default string'):
            return v
    except: pass
    return None

def _infer_ram_type_from_cpu(cpu_string):
    s = cpu_string.upper()
    m = re.search(r'\bI[3579]-(\d{4,5})', s)
    if m:
        gen = int(m.group(1)) // 1000
        if gen <= 5: return 'DDR3'
        return 'DDR4'
    if 'CORE ULTRA' in s: return 'DDR4'
    m = re.search(r'\b[NJ](\d{4})', s)
    if m: return 'DDR4' if int(m.group(1)) >= 4000 else 'DDR3'
    m = re.search(r'RYZEN\s+(?:AI\s+)?[3579]\s+(?:PRO\s+)?(\d{4})', s)
    if m: return 'DDR5' if (int(m.group(1)) // 1000) >= 7 else 'DDR4'
    if re.search(r'\b(ATHLON|A[468]|A1[02])\b', s): return 'DDR4'
    return None

def get_system_info():
    info = dict(make='Unknown', model='Unknown', serial='Unknown',
                cpu_string='Unknown', ram_slots=[],
                battery_health='Unknown', battery_current='Unknown',
                battery_cycles='Unknown')
    info['make']   = dmi_read('sys_vendor')   or 'Unknown'
    info['model']  = dmi_read('product_name') or 'Unknown'
    info['serial'] = dmi_read('product_serial')
    if not info['serial']:
        try:
            r = subprocess.run(['sudo', '-n', 'dmidecode', '-t', 'system'],
                               capture_output=True, text=True, timeout=3)
            m = re.search(r'Serial Number:\s*(.+)', r.stdout)
            if m:
                v = m.group(1).strip()
                if v.lower() not in ('unknown', 'not specified', ''):
                    info['serial'] = v
        except: pass
    info['serial'] = info['serial'] or 'Unknown'
    try:
        cpu_txt = Path('/proc/cpuinfo').read_text()
        m = re.search(r'model name\s*:\s*(.+)', cpu_txt)
        if m: info['cpu_string'] = m.group(1).strip()
    except: pass
    try:
        output = subprocess.check_output(['dmidecode', '--type', '17'], text=True, stderr=subprocess.DEVNULL)
        slots = []
        for device in output.split('Memory Device')[1:]:
            if 'Size:' not in device or 'No Module Installed' in device: continue
            size = rtype = 'Unknown'
            for line in device.splitlines():
                line = line.strip()
                if line.startswith('Size:'):
                    size = line.split(':', 1)[1].strip()
                    mb_m = re.match(r'(\d+)\s*MB', size, re.I)
                    if mb_m:
                        mb = int(mb_m.group(1))
                        size = (str(mb // 1024) + 'GB') if mb >= 1024 else (str(mb) + 'MB')
                    else: size = re.sub(r'\s+', '', size)
                elif line.startswith('Type:'):
                    t_val = line.split(':', 1)[1].strip()
                    if re.match(r'(LP)?DDR\d+', t_val, re.I): rtype = t_val.split()[0].upper()
            if size and size.lower() not in ('unknown', '0'): slots.append(size + ' ' + rtype)
        if slots: info['ram_slots'] = slots
    except: pass
    if info['ram_slots'] and all('Unknown' in s for s in info['ram_slots']):
        inferred = _infer_ram_type_from_cpu(info.get('cpu_string', ''))
        if inferred: info['ram_slots'] = [re.sub(r'\bUnknown\b', inferred, s) for s in info['ram_slots']]
    _load_battery(info)
    return info

def _load_battery(info):
    try:
        r = subprocess.run(['upower', '-e'], capture_output=True, text=True, timeout=3)
        bats = [p.strip() for p in r.stdout.splitlines() if 'bat' in p.lower()]
        if bats:
            r2 = subprocess.run(['upower', '-i', bats[0]], capture_output=True, text=True, timeout=3)
            t = r2.stdout
            m = re.search(r'capacity:\s*([\d.]+)%', t)
            if m: info['battery_health'] = '{}%'.format(round(float(m.group(1))))
            m = re.search(r'percentage:\s*([\d.]+)%', t)
            if m: info['battery_current'] = '{:.1f}%'.format(float(m.group(1)))
            m = re.search(r'charge-cycles:\s*(\d+)', t)
            if m: info['battery_cycles'] = m.group(1)
            return
    except: pass
    try:
        for d in sorted(Path('/sys/class/power_supply').glob('BAT*')):
            def rv(n):
                p = d / n
                return int(p.read_text().strip()) if p.exists() else None
            full   = rv('energy_full')        or rv('charge_full')
            design = rv('energy_full_design') or rv('charge_full_design')
            now    = rv('energy_now')         or rv('charge_now')
            cyc    = (d / 'cycle_count').read_text().strip() if (d / 'cycle_count').exists() else None
            if full and design and design > 0: info['battery_health'] = '{}%'.format(round(full / design * 100))
            if full and now and full > 0: info['battery_current'] = '{:.1f}%'.format(now / full * 100)
            if cyc and cyc not in ('0', ''): info['battery_cycles'] = cyc
            break
    except: pass

def _get_wifi_interface():
    try:
        for iface in os.listdir('/sys/class/net'):
            if os.path.exists(f'/sys/class/net/{iface}/wireless'): return iface
    except: pass
    return None

def get_wifi():
    iface = _get_wifi_interface()
    if not iface: return 'No WiFi Adapter', False
    try: ssid = subprocess.run(['iwgetid', '-r'], capture_output=True, text=True, timeout=2).stdout.strip()
    except: ssid = ''
    ip = None
    try:
        out = subprocess.run(['ip', '-4', 'addr', 'show', iface], capture_output=True, text=True, timeout=2).stdout
        for line in out.splitlines():
            if line.strip().startswith('inet '):
                ip = line.split()[1].split('/')[0]
                break
    except: pass
    if ssid and ip: return 'WiFi: ' + ssid, True
    elif ssid: return 'WiFi: ' + ssid + ' (no IP)', False
    else: return 'WiFi: Off', False

def short_model(info):
    model = info.get('model', 'Unknown')
    for pfx in ('Dell ', 'HP ', 'Hewlett-Packard ', 'Lenovo ', 'Acer ', 'Asus ', 'Toshiba ', 'Samsung '):
        if model.startswith(pfx): return model[len(pfx):]
    return model

def parse_cpu(raw):
    s = raw.strip()
    cpu_type = cpu_series = 'Unknown'
    m = re.search(r'Core\s*(?:\(TM\)\s*)?Ultra\s+([579])\s+(?:Pro\s+)?(\d{3}\w*)', s, re.I)
    if m:
        cpu_type, cpu_series = 'Intel Core Ultra ' + m.group(1), m.group(2)
    else:
        m = re.search(r'Core\s*(?:\(TM\)\s*)?(i[3579])-(\d{4,5}\w*)', s, re.I)
        if m: cpu_type, cpu_series = 'Intel Core ' + m.group(1).lower(), m.group(2)
        else:
            m = re.search(r'Celeron\s*(?:\(R\)\s*)?(?:CPU\s+)?([NJ]\d{4})', s, re.I)
            if m: cpu_type, cpu_series = 'Intel Celeron', m.group(1).upper()
            else:
                m = re.search(r'Ryzen\s+([3579])\s+(?:Pro\s+)?(\d{4}\w*)', s, re.I)
                if m: cpu_type, cpu_series = 'AMD Ryzen ' + m.group(1), m.group(2)
    freq_m = re.search(r'@\s*([\d.]+\s*GHz)', s, re.I)
    return cpu_type, cpu_series, (freq_m.group(1).replace(' ', '') if freq_m else 'Unknown')

def parse_ram(slots):
    sizes = []
    ram_type = 'Unknown'
    for s in slots:
        if not s: continue
        m = re.search(r'(\d+)\s*GB', s, re.I)
        if m: sizes.append(int(m.group(1)))
        t = re.search(r'(LPDDR\d+|DDR\d+)', s, re.I)
        if t: ram_type = t.group(1).upper()
    if not sizes: return 'Unknown', 'Unknown', 'Unknown'
    sc = Counter(sizes)
    return ' + '.join([f'{qty} x {sz}GB' for sz, qty in sc.items()]), str(sum(sizes)) + 'GB', ram_type

# ─── Audio ─────────────────────────────────────────────────────────────────────
class NoisePlayer:
    def __init__(self): self._stream = None
    def start(self, vol=0.4):
        if self._stream or not HAS_AUDIO: return
        def cb(out, frames, t, status): out[:] = (np.random.randn(frames, 2) * vol).astype(np.float32)
        self._stream = sd.OutputStream(samplerate=44100, channels=2, dtype='float32', callback=cb)
        self._stream.start()
    def stop(self):
        if self._stream:
            self._stream.stop(); self._stream.close(); self._stream = None
    def is_playing(self): return self._stream is not None

def set_volume(pct=80):
    for cmd in [['amixer', 'set', 'Master', str(pct) + '%'], ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', str(pct) + '%']]:
        try: subprocess.run(cmd, capture_output=True, timeout=2)
        except: pass

# ─── Keyboard ──────────────────────────────────────────────────────────────────
KB_ROWS = [
    [('Escape','Esc',1.0),(None,'',0.6),('F1','F1',1.0),('F2','F2',1.0),('F3','F3',1.0),('F4','F4',1.0),(None,'',0.3),('F5','F5',1.0),('F6','F6',1.0),('F7','F7',1.0),('F8','F8',1.0),(None,'',0.3),('F9','F9',1.0),('F10','F10',1.0),('F11','F11',1.0),('F12','F12',1.0)],
    [('grave','`',1.0),('1','1',1.0),('2','2',1.0),('3','3',1.0),('4','4',1.0),('5','5',1.0),('6','6',1.0),('7','7',1.0),('8','8',1.0),('9','9',1.0),('0','0',1.0),('minus','-',1.0),('equal','=',1.0),('BackSpace','\u232b Back',2.0)],
    [('Tab','Tab',1.5),('q','Q',1.0),('w','W',1.0),('e','E',1.0),('r','R',1.0),('t','T',1.0),('y','Y',1.0),('u','U',1.0),('i','I',1.0),('o','O',1.0),('p','P',1.0),('bracketleft','[',1.0),('bracketright',']',1.0),('backslash','\\',1.5)],
    [('Caps_Lock','Caps',1.75),('a','A',1.0),('s','S',1.0),('d','D',1.0),('f','F',1.0),('g','G',1.0),('h','H',1.0),('j','J',1.0),('k','K',1.0),('l','L',1.0),('semicolon',';',1.0),('apostrophe',"'",1.0),('Return','Enter',2.25)],
    [('Shift_L','\u21e7 Shift',2.25),('z','Z',1.0),('x','X',1.0),('c','C',1.0),('v','V',1.0),('b','B',1.0),('n','N',1.0),('m','M',1.0),('comma',',',1.0),('period','.',1.0),('slash','/',1.0),('Shift_R','Shift \u21e7',2.75)],
    [('Control_L','Ctrl',1.5),('Super_L','\u2756',1.25),('Alt_L','Alt',1.25),('space','Space',6.0),('Alt_R','Alt',1.25),('Menu','\u25a4',1.0),('Control_R','Ctrl',1.5)],
]

# FIX 1: F10 explicit mapping so it doesn't get masked by XF86AudioMute or other media keys
KB_ALIAS = {
    **{chr(c): chr(c + 32) for c in range(65, 91)},
    'exclam':'1','at':'2','numbersign':'3','dollar':'4','percent':'5','asciicircum':'6','ampersand':'7',
    'asterisk':'8','parenleft':'9','parenright':'0','underscore':'minus','plus':'equal','braceleft':'bracketleft',
    'braceright':'bracketright','bar':'backslash','colon':'semicolon','quotedbl':'apostrophe','less':'comma',
    'greater':'period','question':'slash','asciitilde':'grave','KP_Enter':'Return','ISO_Left_Tab':'Tab',
    'Print':'F12','Scroll_Lock':'F12','Pause':'F12',
    'F10': 'F10',
    'XF86AudioMute': 'F10'
}

class HeaderBar(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=HEADER_BG, height=44)
        self.pack_propagate(False)
        self.app = app
        left = tk.Frame(self, bg=HEADER_BG)
        left.pack(side='left', padx=14, pady=6)
        self.model_lbl = tk.Label(left, text='Detecting\u2026', font=(FONT_SANS, 12, 'bold'), bg=HEADER_BG, fg=TEXT)
        self.model_lbl.pack(side='left')
        tk.Label(left, text='  \u2502  ', font=(FONT_SANS, 12), bg=HEADER_BG, fg=SUBTEXT).pack(side='left')
        self.wifi_dot = tk.Label(left, text='\u25cf', font=(FONT_SANS, 11), bg=HEADER_BG, fg=SUBTEXT)
        self.wifi_dot.pack(side='left')
        self.wifi_lbl = tk.Label(left, text='WiFi: Checking\u2026', font=(FONT_SANS, 11), bg=HEADER_BG, fg=SUBTEXT)
        self.wifi_lbl.pack(side='left', padx=(4, 0))
        tk.Button(self, text='\u2715  EXIT', font=(FONT_SANS, 11, 'bold'), bg='#2a0808', fg=ERROR_C, activebackground='#4a1010', activeforeground=ERROR_C, bd=0, relief='flat', cursor='hand2', padx=16, pady=4, takefocus=0, command=app.quit_app).pack(side='right', padx=10, pady=6)
        self._poll_wifi()
    def set_model(self, name): self.model_lbl.config(text=name)
    def _poll_wifi(self):
        def worker():
            status, connected = get_wifi()
            color = SUCCESS if connected else (WARNING if 'connect' in status.lower() else SUBTEXT)
            self.wifi_lbl.config(text=status, fg=color); self.wifi_dot.config(fg=color)
        threading.Thread(target=worker, daemon=True).start()
        self.after(4000, self._poll_wifi)

class FooterBar(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=HEADER_BG, height=50)
        self.pack_propagate(False)
        self.app = app
        self.prev_btn = tk.Button(self, text='\u25c4  Previous', font=(FONT_SANS, 12), bg=PANEL, fg=TEXT, activebackground=ACCENT, activeforeground='white', bd=0, relief='flat', padx=22, pady=8, cursor='hand2', takefocus=0, command=app.prev_screen)
        self.prev_btn.pack(side='left', padx=12, pady=8)
        self.title_lbl = tk.Label(self, text='', font=(FONT_SANS, 11), bg=HEADER_BG, fg=SUBTEXT)
        self.title_lbl.pack(side='left', expand=True)
        self.next_btn = tk.Button(self, text='Next  \u25ba', font=(FONT_SANS, 12), bg=PANEL, fg=TEXT, activebackground=ACCENT, activeforeground='white', bd=0, relief='flat', padx=22, pady=8, cursor='hand2', takefocus=0, command=app.next_screen)
        self.next_btn.pack(side='right', padx=12, pady=8)
        self.power_btn = tk.Button(self, text='\u23fb  Power Off', font=(FONT_SANS, 12, 'bold'), bg='#2a0808', fg=ERROR_C, activebackground='#4a1010', activeforeground=ERROR_C, bd=0, relief='flat', padx=22, pady=8, cursor='hand2', takefocus=0, command=app.poweroff)
    def update_nav(self, idx, total):
        self.title_lbl.config(text=SCREEN_TITLES[idx] + '  \u00b7  ' + str(idx + 1) + ' / ' + str(total))
        self.prev_btn.config(state='normal' if idx > 0 else 'disabled', fg=TEXT if idx > 0 else SUBTEXT)
        if idx == total - 1:
            self.next_btn.pack_forget(); self.power_btn.pack(side='right', padx=12, pady=8)
        else:
            self.power_btn.pack_forget(); self.next_btn.pack(side='right', padx=12, pady=8)

class BaseScreen(tk.Frame):
    def __init__(self, parent, app): super().__init__(parent, bg=BG); self.app = app
    def on_show(self): pass
    def on_hide(self): pass

class CameraScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app); self._cap = None; self._running = False; self._lbl = None; self._container = None; self._build()
    def _build(self):
        tk.Label(self, text='CAMERA TEST', font=(FONT_SANS, 11, 'bold'), bg=BG, fg=SUBTEXT).pack(pady=(14, 6))
        self._container = tk.Frame(self, bg='black'); self._container.pack(expand=True, fill='both', padx=30, pady=(0, 20)); self._container.pack_propagate(False)
    def on_show(self):
        for w in self._container.winfo_children(): w.destroy()
        self._running = False; self._lbl = None
        if not HAS_CAMERA:
            tk.Label(self._container, text='Camera library missing', font=(FONT_SANS, 20), bg='black', fg=SUBTEXT).pack(expand=True); return
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            tk.Label(self._container, text='Camera not found', font=(FONT_SANS, 30, 'bold'), bg='black', fg=SUBTEXT).pack(expand=True); self._cap = None; return
        self._lbl = tk.Label(self._container, bg='black'); self._lbl.pack(expand=True, fill='both'); self._running = True; self._tick()
    def _tick(self):
        if not self._running or not self._cap: return
        ok, frame = self._cap.read()
        if ok and self._lbl:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = frame.shape[:2]; maxw, maxh = max(self._container.winfo_width(), 100), max(self._container.winfo_height(), 100)
            scale = min(maxw / w, maxh / h); nw, nh = int(w * scale), int(h * scale)
            img = Image.fromarray(frame).resize((nw, nh), Image.LANCZOS); photo = ImageTk.PhotoImage(img)
            self._lbl.config(image=photo); self._lbl.image = photo
        self.after(40, self._tick)
    def on_hide(self):
        self._running = False
        if self._cap: self._cap.release(); self._cap = None

class SpeakerScreen(BaseScreen):
    def __init__(self, parent, app):
        super().__init__(parent, app); self._noise = NoisePlayer(); self._build()
    def _build(self):
        tk.Label(self, text='SPEAKER TEST', font=(FONT_SANS, 11, 'bold'), bg=BG, fg=SUBTEXT).pack(pady=(14, 0))
        center = tk.Frame(self, bg=BG); center.pack(expand=True)
        self._icon = tk.Label(center, text='\u266a', font=(FONT_SANS, 80), bg=BG, fg=SUBTEXT); self._icon.pack(pady=(0, 18))
        self._prompt = tk.Label(center, text='Press Space to Test Speakers', font=(FONT_SANS, 26, 'bold'), bg=BG, fg=TEXT); self._prompt.pack()
        self._sub = tk.Label(center, text='', font=(FONT_SANS, 14), bg=BG, fg=SUBTEXT); self._sub.pack(pady=8)
    def on_show(self): self._noise.stop(); set_volume(40); self.app.root.bind('<space>', self._toggle)
    def on_hide(self): self._noise.stop(); self.app.root.unbind('<space>')
    def _toggle(self, _=None):
        if self._noise.is_playing():
            self._noise.stop(); self._prompt.config(text='Press Space to Test Speakers', fg=TEXT); self._icon.config(fg=SUBTEXT); self._sub.config(text='')
        else:
            self._noise.start(vol=0.4); self._prompt.config(text='Playing\u2026', fg=SUCCESS); self._icon.config(fg=SUCCESS); self._sub.config(text='Press Space again to stop')

class KeyboardScreen(BaseScreen):
    GAP, _TOTAL_UNITS = 4, sum(e[2] for e in KB_ROWS[1]) + 4.1
    def __init__(self, parent, app):
        super().__init__(parent, app); self._items, self._pressed, self._done = {}, set(), set(); self._canvas = None; self._current_key_var = tk.StringVar(value='Current key: None')
        sw, sh = app.root.winfo_screenwidth(), app.root.winfo_screenheight()
        v_budget = sh - 180; unit_w = int(sw * 0.90 / self._TOTAL_UNITS); unit_h = int(v_budget / 7)
        self.UNIT = max(40, min(unit_w, unit_h)); self.KEY_H, self.ROW_H = int(self.UNIT * 0.87), int(self.UNIT * 1.03); self._build()
    def _build(self):
        tk.Label(self, text='KEYBOARD TEST', font=(FONT_SANS, 11, 'bold'), bg=BG, fg=SUBTEXT).pack(pady=(14, 3))
        outer = tk.Frame(self, bg=BG); outer.pack(expand=True)
        self._canvas = tk.Canvas(outer, width=int(self._TOTAL_UNITS * self.UNIT), height=(len(KB_ROWS) * self.ROW_H + 50), bg=BG, highlightthickness=0); self._canvas.pack()
        self._draw_all_keys()
        tk.Label(outer, textvariable=self._current_key_var, font=(FONT_MONO, 18, 'bold'), bg=PANEL, fg=ACCENT, padx=18, pady=8).pack(pady=(12, 0))
    def _draw_all_keys(self):
        U, KH, RH, G, LEFT = self.UNIT, self.KEY_H, self.ROW_H, self.GAP, 6
        for ri, row in enumerate(KB_ROWS):
            y, x = (4 if ri == 0 else RH + (ri - 1) * RH + 10), LEFT
            for (ks, lbl, w) in row:
                pw = int(w * U) - G
                if ks: self._draw_key(ks, lbl, x, y, pw, KH)
                x += int(w * U)
    def _draw_key(self, ks, lbl, x, y, w, h):
        c = self._canvas; r = c.create_rectangle(x, y, x + w, y + h, fill=KEY_IDLE, outline='#9aa0b8', width=1, tags=('key', ks))
        fsz = max(7, int(self.UNIT * (0.20 if len(lbl) <= 5 else 0.17)))
        t = c.create_text(x + w // 2, y + h // 2, text=lbl, font=(FONT_SANS, fsz), fill=KEY_TXT_D, tags=('key', ks))
        self._items[ks] = (r, t)
    def _resolve(self, sym): return KB_ALIAS.get(sym, sym)
    def _set_color(self, ks, fill, txt_color):
        res = self._resolve(ks)
        if res in self._items: r, t = self._items[res]; self._canvas.itemconfig(r, fill=fill); self._canvas.itemconfig(t, fill=txt_color)
    def on_show(self):
        self.app.root.bind_all('<KeyPress>', self._key_down); self.app.root.bind_all('<KeyRelease>', self._key_up)
        self._canvas.bind('<Tab>', self._block_key); self._canvas.bind('<space>', self._block_key); self._canvas.focus_set()
    def _block_key(self, ev): self._key_down(ev); return 'break'
    def on_hide(self): self.app.root.unbind_all('<KeyPress>'); self.app.root.unbind_all('<KeyRelease>'); self._canvas.unbind('<Tab>'); self._canvas.unbind('<space>')
    def _key_down(self, ev):
        ks = self._resolve(ev.keysym); self._pressed.add(ks); self._done.add(ks); self._set_color(ks, KEY_HELD, KEY_TXT_L)
        self._current_key_var.set('Current key: ' + ev.keysym)
    def _key_up(self, ev):
        ks = self._resolve(ev.keysym); self._pressed.discard(ks)
        if ks in self._done: self._set_color(ks, KEY_DONE, KEY_TXT_D)
        self._current_key_var.set('Current key: ' + (sorted(self._pressed)[-1] if self._pressed else 'None'))

class InfoScreen(BaseScreen):
    def on_show(self): self._build()
    def _build(self):
        for w in self.winfo_children(): w.destroy()
        tk.Label(self, text='SYSTEM INFORMATION', font=(FONT_SANS, 14, 'bold'), bg=BG, fg=SUBTEXT).pack(pady=14)
        info = self.app.system_info
        if not info: tk.Label(self, text='Loading...', bg=BG, fg=TEXT).pack(); return
        c_t, c_s, c_f = parse_cpu(info.get('cpu_string', 'Unknown'))
        r_c, r_t, r_y = parse_ram(info.get('ram_slots', []))
        f = tk.Frame(self, bg=BG); f.pack(expand=True, fill='both', padx=40)
        def row(l, v):
            fr = tk.Frame(f, bg=BG); fr.pack(fill='x', pady=2)
            tk.Label(fr, text=l+':', font=(FONT_SANS, 11), width=15, anchor='e', bg=BG, fg=SUBTEXT).pack(side='left')
            tk.Label(fr, text=v, font=(FONT_MONO, 11), bg=BG, fg=TEXT).pack(side='left', padx=10)
        row('Make', info.get('make', 'Unknown')); row('Model', info.get('model', 'Unknown')); row('Serial', info.get('serial', 'Unknown'))
        tk.Frame(f, bg=BORDER, height=1).pack(fill='x', pady=10)
        row('CPU', c_t + ' ' + c_s); row('RAM', r_t + ' ' + r_y); row('Battery', info.get('battery_health', 'Unknown'))

class SyncScreen(BaseScreen):
    STEP_IP, STEP_CSAD, STEP_NOTES, STEP_GRADE, STEP_SENDING, STEP_DONE = range(6)
    def __init__(self, parent, app):
        super().__init__(parent, app); self._cfg = load_cfg(); self._step = self.STEP_IP; self._ip = tk.StringVar(value=self._cfg.get('last_ip', '')); self._csad, self._grade, self._notes = tk.StringVar(), tk.StringVar(), ''
        tk.Label(self, text='SYNC TO SERVER', font=(FONT_SANS, 11, 'bold'), bg=BG, fg=SUBTEXT).pack(pady=(14, 0))
        self._status_lbl = tk.Label(self, text='', font=(FONT_SANS, 12), bg=BG, fg=SUBTEXT); self._status_lbl.pack(pady=4)
        self._content = tk.Frame(self, bg=BG); self._content.pack(expand=True, fill='both')
    def on_show(self): self._step = self.STEP_IP; self._render()
    def _status(self, m, c=None): self._status_lbl.config(text=m, fg=c or SUBTEXT)
    def _render(self):
        for w in self._content.winfo_children(): w.destroy()
        if self._step == self.STEP_IP: self._show_ip()
        elif self._step == self.STEP_CSAD: self._show_csad()
        elif self._step == self.STEP_NOTES: self._show_notes()
        elif self._step == self.STEP_GRADE: self._show_grade()
        elif self._step == self.STEP_SENDING: self._show_sending()
        elif self._step == self.STEP_DONE: self._show_done()
    def _show_ip(self):
        box = tk.Frame(self._content, bg=BG); box.pack(expand=True)
        tk.Label(box, text='Server IP', font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(pady=10)
        e = tk.Entry(box, textvariable=self._ip, font=(FONT_MONO, 22), width=20, bg=PANEL, fg=TEXT, insertbackground=ACCENT, bd=0, relief='flat', justify='center'); e.pack(ipady=10); e.focus_set(); e.bind('<Return>', self._confirm_ip)
    def _confirm_ip(self, _=None):
        ip = self._ip.get().strip(); self._status('Connecting...', WARNING); self.update()
        def test():
            try: s = socket.create_connection((ip, 5050), timeout=3); s.close(); ok = True
            except: ok = False
            self.after(0, lambda: self._ip_res(ip, ok))
        threading.Thread(target=test, daemon=True).start()
    def _ip_res(self, ip, ok):
        if ok: self._cfg['last_ip'] = ip; save_cfg(self._cfg); self._status('Connected', SUCCESS); self.after(500, lambda: self._goto(self.STEP_CSAD))
        else: self._status('Failed to connect', ERROR_C)
    def _show_csad(self):
        box = tk.Frame(self._content, bg=BG); box.pack(expand=True)
        tk.Label(box, text='CSAD Number', font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(pady=10)
        e = tk.Entry(box, textvariable=self._csad, font=(FONT_MONO, 28), width=12, bg=PANEL, fg=ACCENT, insertbackground=ACCENT, bd=0, relief='flat', justify='center'); e.pack(ipady=10); e.focus_set(); e.bind('<Return>', lambda _: self._goto(self.STEP_NOTES))
    
    def _show_notes(self):
        box = tk.Frame(self._content, bg=BG); box.pack(expand=True, fill='both', padx=60, pady=10)
        tk.Label(box, text='Condition Notes', font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(anchor='w')
        
        # FIX 2: Increased font size to 22
        self._notes_txt = tk.Text(box, font=(FONT_SANS, 22), bg=PANEL, fg=TEXT, insertbackground=ACCENT, bd=0, wrap='word', height=4, padx=15, pady=15)
        self._notes_txt.pack(fill='both', expand=True, pady=10); self._notes_txt.focus_set()
        if self._notes: self._notes_txt.insert('1.0', self._notes)
        
        # FIX 3: Submit on Enter instead of Shift+Enter
        self._notes_txt.bind('<Return>', self._confirm_notes)
    
    def _confirm_notes(self, _=None):
        self._notes = self._notes_txt.get('1.0', 'end-1c').strip() or '*'
        self._goto(self.STEP_GRADE); return 'break'
        
    def _show_grade(self):
        box = tk.Frame(self._content, bg=BG); box.pack(expand=True)
        tk.Label(box, text='Grade', font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(pady=10)
        r = tk.Frame(box, bg=BG); r.pack()
        for g in ['A', 'B', 'C', 'D']:
            tk.Button(r, text=g, font=(FONT_SANS, 20, 'bold'), width=4, command=lambda x=g: self._sel_grade(x)).pack(side='left', padx=10)
    def _sel_grade(self, g): self._grade.set(g); self._goto(self.STEP_SENDING)
    def _show_sending(self):
        tk.Label(self._content, text='Sending...', font=(FONT_SANS, 22), bg=BG, fg=ACCENT).pack(expand=True)
        def send():
            info = self.app.system_info; p = {'model': info.get('model'), 'serial': info.get('serial'), 'condition': self._notes, 'grade': self._grade.get(), 'csad': self._csad.get()}
            try: r = req.post(f"http://{self._ip.get()}:5050/log", json=p, timeout=5); ok = r.status_code == 200
            except: ok = False
            self.after(0, lambda: self._goto(self.STEP_DONE, ok))
        threading.Thread(target=send, daemon=True).start()
    def _goto(self, s, ok=None): self._step, self._last_ok = s, ok; self._render()
    def _show_done(self):
        m = 'Success!' if getattr(self, '_last_ok', False) else 'Failed'
        tk.Label(self._content, text=m, font=(FONT_SANS, 30, 'bold'), bg=BG, fg=SUCCESS if 'Succ' in m else ERROR_C).pack(expand=True)
    def handle_next(self):
        if self._step == self.STEP_NOTES: self._notes = self._notes_txt.get('1.0', 'end-1c').strip()
        return True

class App:
    def __init__(self):
        self.root = tk.Tk(); self.root.attributes('-fullscreen', True); self.root.attributes('-topmost', True); self.root.configure(bg=BG)
        self.system_info, self._idx, self._active = {}, 0, None
        threading.Thread(target=self._load_info, daemon=True).start()
        self._build_ui(); self._show(0)
    def _load_info(self): self.system_info = get_system_info(); self.root.after(0, lambda: self._header.set_model(short_model(self.system_info)))
    def _build_ui(self):
        self._header = HeaderBar(self.root, self); self._header.pack(fill='x')
        self._footer = FooterBar(self.root, self); self._footer.pack(fill='x', side='bottom')
        self._area = tk.Frame(self.root, bg=BG); self._area.pack(fill='both', expand=True)
        self._screens = [CameraScreen(self._area, self), SpeakerScreen(self._area, self), KeyboardScreen(self._area, self), InfoScreen(self._area, self), SyncScreen(self._area, self)]
    def _show(self, i):
        if self._active: self._active.on_hide(); self._active.pack_forget()
        self._idx = i; s = self._screens[i]; s.pack(fill='both', expand=True); s.on_show(); self._active = s; self._footer.update_nav(i, len(self._screens))
    def next_screen(self):
        if self._idx < len(self._screens)-1:
            if hasattr(self._active, 'handle_next'): self._active.handle_next()
            self._show(self._idx + 1)
    def prev_screen(self):
        if self._idx > 0: self._show(self._idx - 1)
    def quit_app(self): self.root.destroy()
    
    def poweroff(self):
        """Immediate poweroff with several fallbacks for Puppy/Linux systems."""
        # FIX 4: Immediate hardware cutoff via SysRq (Bypasses all GUI prompts instantly)
        try:
            subprocess.run(['sh', '-c', 'echo 1 > /proc/sys/kernel/sysrq'], timeout=1)
            subprocess.run(['sh', '-c', 'echo o > /proc/sysrq-trigger'], timeout=1)
        except Exception: 
            pass
            
        # Fallbacks if SysRq doesn't work right away
        commands = [
            ['poweroff', '-f', '-n'],
            ['busybox', 'poweroff', '-f'],
            ['systemctl', 'poweroff', '--force', '--force'],
            ['halt', '-f', '-p'],
            ['wmpoweroff'],
            ['sudo', 'poweroff', '-f'],
            ['sudo', 'systemctl', 'poweroff', '--force', '--force'],
            ['sudo', 'halt', '-f', '-p'],
        ]

        for cmd in commands:
            try:
                subprocess.Popen(cmd)
                # close the app immediately so the WM doesn't keep focus weirdness
                self.root.after(100, self.root.destroy)
                return
            except Exception:
                pass
                
        self.root.destroy()

    def run(self): self.root.mainloop()

if __name__ == '__main__': App().run()
