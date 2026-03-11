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
# In Frugal PuppyLinux the root filesystem lives in RAM, so anything written
# to a plain path is lost on reboot.  We need to find and write to a *real*
# partition on disk (the USB stick or an internal drive) that persists across
# boots and across swapping laptops.
#
# Strategy (tried in order):
#   1. Any mount-point under /mnt whose device is a real block device
#      (not tmpfs / ramfs / squashfs / aufs / overlay / devtmpfs etc.)
#      and that is writable.
#   2. /mnt/sda1, /mnt/sdb1 … /mnt/sdh4  scanned directly.
#   3. Fall back to the script directory (works on a normal Linux install
#      where the filesystem IS persistent).

_FAKE_FS = {'tmpfs', 'ramfs', 'squashfs', 'aufs', 'overlay',
            'overlayfs', 'devtmpfs', 'sysfs', 'proc', 'cgroup',
            'cgroup2', 'pstore', 'efivarfs', 'securityfs', 'debugfs',
            'tracefs', 'configfs', 'fusectl', 'hugetlbfs', 'mqueue',
            'bpf', 'nfs', 'nfs4', 'iso9660', 'udf'}

def _find_persistent_dir():
    """Return a writable Path on a real (non-RAM) filesystem, or None."""
    candidates = []

    # ── 1. Parse /proc/mounts ──────────────────────────────────────────────────
    try:
        mounts = Path('/proc/mounts').read_text().splitlines()
        for line in mounts:
            parts = line.split()
            if len(parts) < 3:
                continue
            device, mountpoint, fstype = parts[0], parts[1], parts[2]
            if fstype.lower() in _FAKE_FS:
                continue
            if not device.startswith('/dev/'):
                continue
            mp = Path(mountpoint)
            # Prefer /mnt/* mounts; accept others but rank lower
            priority = 0 if str(mp).startswith('/mnt') else 1
            candidates.append((priority, mp))
    except:
        pass

    # ── 2. Brute-force common PuppyLinux mount points ─────────────────────────
    for letter in 'abcdefgh':
        for part in range(1, 5):
            candidates.append((2, Path('/mnt/sd' + letter + str(part))))

    # Sort by priority so /mnt/* paths come first
    candidates.sort(key=lambda x: x[0])

    for _, mp in candidates:
        try:
            if not mp.is_dir():
                continue
            test = mp / '.tester_write_test'
            test.write_text('x')
            test.unlink()
            return mp          # first writable real mount wins
        except:
            continue
    return None

def _config_file():
    """Return Path to the config JSON on persistent storage (creates dir if needed)."""
    base = _find_persistent_dir()
    if base:
        d = base / 'LaptopTester'
        try:
            d.mkdir(exist_ok=True)
            return d / 'tester_config.json'
        except:
            pass
    # Fallback: next to the script (works on non-Frugal systems)
    return Path(sys.argv[0]).resolve().parent / '.tester_config.json'

# Resolve once at startup so every call is instant
CONFIG_FILE = _config_file()

def load_cfg():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except:
        return {}

def save_cfg(d):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(d, indent=2))
    except:
        pass

# ─── System info ───────────────────────────────────────────────────────────────
def dmi_read(path):
    try:
        v = Path('/sys/class/dmi/id/' + path).read_text().strip()
        if v.lower() not in ('', 'unknown', 'not specified',
                              'to be filled by o.e.m.', 'default string'):
            return v
    except:
        pass
    return None

def _infer_ram_type_from_cpu(cpu_string):
    """
    Infer DDR generation from CPU model string.
    Returns e.g. 'DDR4' or None if we can't tell.
    Intel: model number first digit(s) = generation.
    AMD Ryzen: series number maps to DDR gen.
    """
    s = cpu_string.upper()

    # Intel Core iX-NNNN  (e.g. i5-8250U → 8th gen → DDR4)
    m = re.search(r'\bI[3579]-(\d{4,5})', s)
    if m:
        gen = int(m.group(1)) // 1000
        if gen <= 3:   return 'DDR3'
        if gen <= 5:   return 'DDR3'   # Broadwell era
        if gen <= 11:  return 'DDR4'
        return 'DDR4'   # 12th/13th gen could be DDR5 but DDR4 is more common

    # Intel Core Ultra (12th gen+) → DDR4/DDR5 — default DDR4
    if 'CORE ULTRA' in s:
        return 'DDR4'

    # Intel Celeron / Pentium N/J series (Bay Trail, Gemini Lake) → DDR3/DDR4
    m = re.search(r'\b[NJ](\d{4})', s)
    if m:
        n = int(m.group(1))
        return 'DDR4' if n >= 4000 else 'DDR3'

    # AMD Ryzen NNNN (e.g. Ryzen 5 3500U → 3rd gen → DDR4)
    m = re.search(r'RYZEN\s+(?:AI\s+)?[3579]\s+(?:PRO\s+)?(\d{4})', s)
    if m:
        series = int(m.group(1)) // 1000
        return 'DDR5' if series >= 7 else 'DDR4'

    # AMD A-series / Athlon
    if re.search(r'\b(ATHLON|A[468]|A1[02])\b', s):
        return 'DDR4'

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
        except:
            pass
    info['serial'] = info['serial'] or 'Unknown'

    # CPU
    try:
        cpu_txt = Path('/proc/cpuinfo').read_text()
        m = re.search(r'model name\s*:\s*(.+)', cpu_txt)
        if m:
            info['cpu_string'] = m.group(1).strip()
    except:
        pass

    # ── RAM detection ─────────────────────────────────────────────────────────
    # Run dmidecode directly (no sudo) — PuppyLinux runs as root, so this
    # works without privilege escalation and returns proper Type: strings.
    try:
        output = subprocess.check_output(
            ['dmidecode', '--type', '17'], text=True, stderr=subprocess.DEVNULL
        )
        slots = []
        for device in output.split('Memory Device')[1:]:
            if 'Size:' not in device or 'No Module Installed' in device:
                continue
            size = rtype = 'Unknown'
            for line in device.splitlines():
                line = line.strip()
                if line.startswith('Size:'):
                    size = line.split(':', 1)[1].strip()
                    # Normalise "8192 MB" → "8GB"
                    mb_m = re.match(r'(\d+)\s*MB', size, re.I)
                    if mb_m:
                        mb = int(mb_m.group(1))
                        size = (str(mb // 1024) + 'GB') if mb >= 1024 else (str(mb) + 'MB')
                    else:
                        size = re.sub(r'\s+', '', size)
                elif line.startswith('Type:'):
                    t_val = line.split(':', 1)[1].strip()
                    if re.match(r'(LP)?DDR\d+', t_val, re.I):
                        rtype = t_val.split()[0].upper()
            if size and size.lower() not in ('unknown', '0'):
                slots.append(size + ' ' + rtype)
        if slots:
            info['ram_slots'] = slots
    except:
        pass

    # Fallback: infer type from CPU generation if dmidecode returned Unknown
    if info['ram_slots'] and all('Unknown' in s for s in info['ram_slots']):
        inferred = _infer_ram_type_from_cpu(info.get('cpu_string', ''))
        if inferred:
            info['ram_slots'] = [re.sub(r'\bUnknown\b', inferred, s)
                                  for s in info['ram_slots']]

    if not info['ram_slots']:
        try:
            m = re.search(r'MemTotal:\s*(\d+)', Path('/proc/meminfo').read_text())
            if m:
                gb = round(int(m.group(1)) / 1024 / 1024)
                info['ram_slots'] = [str(gb) + 'GB Unknown']
        except:
            pass

    _load_battery(info)
    return info

def _load_battery(info):
    try:
        r = subprocess.run(['upower', '-e'], capture_output=True, text=True, timeout=3)
        bats = [p.strip() for p in r.stdout.splitlines()
                if 'bat' in p.lower()]
        if bats:
            r2 = subprocess.run(['upower', '-i', bats[0]],
                                capture_output=True, text=True, timeout=3)
            t = r2.stdout
            m = re.search(r'capacity:\s*([\d.]+)%', t)
            if m:
                info['battery_health'] = '{}%'.format(round(float(m.group(1))))
            m = re.search(r'percentage:\s*([\d.]+)%', t)
            if m:
                info['battery_current'] = '{:.1f}%'.format(float(m.group(1)))
            m = re.search(r'charge-cycles:\s*(\d+)', t)
            if m:
                info['battery_cycles'] = m.group(1)
            return
    except:
        pass
    try:
        for d in sorted(Path('/sys/class/power_supply').glob('BAT*')):
            def rv(n):
                p = d / n
                return int(p.read_text().strip()) if p.exists() else None
            full   = rv('energy_full')        or rv('charge_full')
            design = rv('energy_full_design') or rv('charge_full_design')
            now    = rv('energy_now')         or rv('charge_now')
            cyc    = (d / 'cycle_count').read_text().strip() \
                     if (d / 'cycle_count').exists() else None
            if full and design and design > 0:
                info['battery_health'] = '{}%'.format(round(full / design * 100))
            if full and now and full > 0:
                info['battery_current'] = '{:.1f}%'.format(now / full * 100)
            if cyc and cyc not in ('0', ''):
                info['battery_cycles'] = cyc
            break
    except:
        pass

def _get_wifi_interface():
    """Return the first wireless interface found in /sys/class/net, or None."""
    try:
        for iface in os.listdir('/sys/class/net'):
            if os.path.exists(f'/sys/class/net/{iface}/wireless'):
                return iface
    except:
        pass
    return None

def get_wifi():
    iface = _get_wifi_interface()
    if not iface:
        return 'No WiFi Adapter', False

    # Get connected SSID via iwgetid
    try:
        ssid = subprocess.run(['iwgetid', '-r'], capture_output=True,
                              text=True, timeout=2).stdout.strip()
    except:
        ssid = ''

    # Get IP address via ip addr
    ip = None
    try:
        out = subprocess.run(['ip', '-4', 'addr', 'show', iface],
                             capture_output=True, text=True, timeout=2).stdout
        for line in out.splitlines():
            if line.strip().startswith('inet '):
                ip = line.split()[1].split('/')[0]
                break
    except:
        pass

    if ssid and ip:
        return 'WiFi: ' + ssid, True
    elif ssid:
        return 'WiFi: ' + ssid + ' (no IP)', False
    else:
        return 'WiFi: Off', False

def short_model(info):
    model = info.get('model', 'Unknown')
    for pfx in ('Dell ', 'HP ', 'Hewlett-Packard ', 'Lenovo ',
                'Acer ', 'Asus ', 'Toshiba ', 'Samsung '):
        if model.startswith(pfx):
            return model[len(pfx):]
    return model

# ─── CPU / RAM parsing ─────────────────────────────────────────────────────────
def parse_cpu(raw):
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
        else:
            m = re.search(r'Celeron\s*(?:\(R\)\s*)?(?:CPU\s+)?([NJ]\d{4})', s, re.I)
            if m:
                cpu_type = 'Intel Celeron'
                cpu_series = m.group(1).upper()
            else:
                m = re.search(r'Celeron\s*(?:\(R\)\s*)?(?:CPU\s+)?(\d{3,4}[A-Z]*)', s, re.I)
                if m:
                    cpu_type = 'Intel Celeron'
                    cpu_series = m.group(1).upper()
                else:
                    m = re.search(r'Pentium\s*(?:\(R\)\s*)?(?:(Silver|Gold)\s+)?(?:CPU\s+)?([NJ]?\d{3,5}\w*)', s, re.I)
                    if m:
                        g = (' ' + m.group(1).title()) if m.group(1) else ''
                        cpu_type = 'Intel Pentium' + g
                        cpu_series = m.group(2).upper()
                    else:
                        m = re.search(r'Atom\s*(?:\(TM\)\s*)?(?:x[357]-|CPU\s+)?([A-Z]\d{4,5}\w*)', s, re.I)
                        if m:
                            cpu_type = 'Intel Atom'
                            cpu_series = m.group(1).upper()
                        else:
                            m = re.search(r'Ryzen\s+AI\s+([3579])\s+(?:(?:Pro|HX)\s+)?(\d{3}\w*)', s, re.I)
                            if m:
                                cpu_type = 'AMD Ryzen AI ' + m.group(1)
                                cpu_series = m.group(2)
                            else:
                                m = re.search(r'Ryzen\s+([3579])\s+(?:Pro\s+)?(\d{4}\w*)', s, re.I)
                                if m:
                                    cpu_type = 'AMD Ryzen ' + m.group(1)
                                    cpu_series = m.group(2)
                                else:
                                    m = re.search(r'\bA(\d{1,2})-(\d{4}\w*)', s, re.I)
                                    if m:
                                        cpu_type = 'AMD A' + m.group(1)
                                        cpu_series = m.group(2)
                                    else:
                                        m = re.search(r'Athlon\s+(?:(Silver|Gold)\s+)?(\d{4}\w*)', s, re.I)
                                        if m:
                                            g = (' ' + m.group(1).title()) if m.group(1) else ''
                                            cpu_type = 'AMD Athlon' + g
                                            cpu_series = m.group(2)
                                        else:
                                            m = re.search(r'\bFX-(\d{4}\w*)', s, re.I)
                                            if m:
                                                cpu_type = 'AMD FX'
                                                cpu_series = m.group(1)
    freq_m = re.search(r'@\s*([\d.]+\s*GHz)', s, re.I)
    cpu_freq = freq_m.group(1).replace(' ', '') if freq_m else 'Unknown'
    return cpu_type, cpu_series, cpu_freq

def parse_ram(slots):
    sizes = []
    ram_type = 'Unknown'
    for s in slots:
        if not s:
            continue
        m = re.search(r'(\d+)\s*GB', s, re.I)
        if m:
            sizes.append(int(m.group(1)))
        # Match DDR3 / DDR4 / DDR5 / LPDDR4 / LPDDR5 etc.
        t = re.search(r'(LPDDR\d+|DDR\d+)', s, re.I)
        if t:
            ram_type = t.group(1).upper()
    if not sizes:
        return 'Unknown', 'Unknown', 'Unknown'
    sc = Counter(sizes)
    # Always format as "N x SizeGB [+ N x SizeGB ...]"
    parts = [f'{qty} x {sz}GB' for sz, qty in sc.items()]
    cfg = ' + '.join(parts)
    total = str(sum(sizes)) + 'GB'
    return cfg, total, ram_type

# ─── Audio ─────────────────────────────────────────────────────────────────────
class NoisePlayer:
    def __init__(self):
        self._stream = None

    def start(self, vol=0.4):
        if self._stream or not HAS_AUDIO:
            return
        def cb(out, frames, t, status):
            out[:] = (np.random.randn(frames, 2) * vol).astype(np.float32)
        self._stream = sd.OutputStream(samplerate=44100, channels=2,
                                        dtype='float32', callback=cb)
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def is_playing(self):
        return self._stream is not None

def set_volume(pct=80):
    for cmd in [
        ['amixer', 'set', 'Master', 'unmute'],
        ['amixer', 'set', 'Master', str(pct) + '%'],
        ['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '0'],
        ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', str(pct) + '%'],
    ]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=2)
        except:
            pass

# ─── Keyboard layout definition ────────────────────────────────────────────────
# (keysym, display_label, width_in_units)  --  None keysym = invisible spacer
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

# Navigation cluster drawn above the arrow keys (right of main block)
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
    # Print/Scroll/Pause have no dedicated key in the layout — alias to F12
    'Print':'F12','Scroll_Lock':'F12','Pause':'F12',
    'KP_0':'0','KP_1':'1','KP_2':'2','KP_3':'3',
    'KP_4':'4','KP_5':'5','KP_6':'6','KP_7':'7','KP_8':'8','KP_9':'9',
    'KP_Decimal':'period','KP_Add':'equal','KP_Subtract':'minus',
    'KP_Multiply':'8','KP_Divide':'slash',

    'XF86AudioMute': 'F10',
    'XF86AudioMicMute': 'F10',
}

# ─── Header ────────────────────────────────────────────────────────────────────
class HeaderBar(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=HEADER_BG, height=44)
        self.pack_propagate(False)
        self.app = app

        left = tk.Frame(self, bg=HEADER_BG)
        left.pack(side='left', padx=14, pady=6)

        self.model_lbl = tk.Label(left, text='Detecting\u2026',
            font=(FONT_SANS, 12, 'bold'), bg=HEADER_BG, fg=TEXT)
        self.model_lbl.pack(side='left')

        tk.Label(left, text='  \u2502  ', font=(FONT_SANS, 12),
            bg=HEADER_BG, fg=SUBTEXT).pack(side='left')

        self.wifi_dot = tk.Label(left, text='\u25cf', font=(FONT_SANS, 11),
            bg=HEADER_BG, fg=SUBTEXT)
        self.wifi_dot.pack(side='left')

        self.wifi_lbl = tk.Label(left, text='WiFi: Checking\u2026',
            font=(FONT_SANS, 11), bg=HEADER_BG, fg=SUBTEXT)
        self.wifi_lbl.pack(side='left', padx=(4, 0))

        tk.Button(self, text='\u2715  EXIT',
            font=(FONT_SANS, 11, 'bold'),
            bg='#2a0808', fg=ERROR_C,
            activebackground='#4a1010', activeforeground=ERROR_C,
            bd=0, relief='flat', cursor='hand2', padx=16, pady=4,
            takefocus=0, command=app.quit_app).pack(side='right', padx=10, pady=6)

        self._poll_wifi()

    def set_model(self, name):
        self.model_lbl.config(text=name)

    def _poll_wifi(self):
        def worker():
            status, connected = get_wifi()
            color = SUCCESS if connected else (WARNING if 'connect' in status.lower() else SUBTEXT)
            self.wifi_lbl.config(text=status, fg=color)
            self.wifi_dot.config(fg=color)
        threading.Thread(target=worker, daemon=True).start()
        self.after(4000, self._poll_wifi)

# ─── Footer ────────────────────────────────────────────────────────────────────
class FooterBar(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=HEADER_BG, height=50)
        self.pack_propagate(False)
        self.app = app

        self.prev_btn = tk.Button(self, text='\u25c4  Previous',
            font=(FONT_SANS, 12), bg=PANEL, fg=TEXT,
            activebackground=ACCENT, activeforeground='white',
            bd=0, relief='flat', padx=22, pady=8, cursor='hand2',
            takefocus=0, command=app.prev_screen)
        self.prev_btn.pack(side='left', padx=12, pady=8)

        self.title_lbl = tk.Label(self, text='',
            font=(FONT_SANS, 11), bg=HEADER_BG, fg=SUBTEXT)
        self.title_lbl.pack(side='left', expand=True)

        self.next_btn = tk.Button(self, text='Next  \u25ba',
            font=(FONT_SANS, 12), bg=PANEL, fg=TEXT,
            activebackground=ACCENT, activeforeground='white',
            bd=0, relief='flat', padx=22, pady=8, cursor='hand2',
            takefocus=0, command=app.next_screen)
        self.next_btn.pack(side='right', padx=12, pady=8)

        self.power_btn = tk.Button(self, text='\u23fb  Power Off',
            font=(FONT_SANS, 12, 'bold'),
            bg='#2a0808', fg=ERROR_C,
            activebackground='#4a1010', activeforeground=ERROR_C,
            bd=0, relief='flat', padx=22, pady=8, cursor='hand2',
            takefocus=0, command=app.poweroff)
        # power_btn is packed/unpacked by update_nav

    def update_nav(self, idx, total):
        self.title_lbl.config(
            text=SCREEN_TITLES[idx] + '  \u00b7  ' + str(idx + 1) + ' / ' + str(total))
        self.prev_btn.config(
            state='normal' if idx > 0 else 'disabled',
            fg=TEXT if idx > 0 else SUBTEXT)
        # On the last screen (Sync) swap Next for Power Off
        if idx == total - 1:
            self.next_btn.pack_forget()
            self.power_btn.pack(side='right', padx=12, pady=8)
        else:
            self.power_btn.pack_forget()
            self.next_btn.pack(side='right', padx=12, pady=8)

# ─── Base screen ───────────────────────────────────────────────────────────────
class BaseScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app

    def on_show(self):
        pass

    def on_hide(self):
        pass

# ─── Screen 1 : Camera ─────────────────────────────────────────────────────────
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
        # Prevent the label's image from propagating size requests back up to
        # the root window, which would cause an infinite grow loop on WMs
        # that don't enforce the -fullscreen geometry themselves.
        self._container.pack_propagate(False)

    def on_show(self):
        for w in self._container.winfo_children():
            w.destroy()
        self._running = False
        self._lbl = None

        if not HAS_CAMERA:
            tk.Label(self._container,
                text='Camera not working\n(pip install opencv-python pillow)',
                font=(FONT_SANS, 20), bg='black', fg=SUBTEXT).pack(expand=True)
            return

        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            tk.Label(self._container, text='Camera not working',
                font=(FONT_SANS, 30, 'bold'), bg='black', fg=SUBTEXT).pack(expand=True)
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
            img   = Image.fromarray(frame).resize((nw, nh), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._lbl.config(image=photo)
            self._lbl.image = photo
        self.after(40, self._tick)

    def on_hide(self):
        self._running = False
        if self._cap:
            self._cap.release()
            self._cap = None

# ─── Screen 2 : Speaker ────────────────────────────────────────────────────────
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

        self._icon = tk.Label(center, text='\u266a',
            font=(FONT_SANS, 80), bg=BG, fg=SUBTEXT)
        self._icon.pack(pady=(0, 18))

        self._prompt = tk.Label(center,
            text='Press  Space  to Test Speakers',
            font=(FONT_SANS, 26, 'bold'), bg=BG, fg=TEXT)
        self._prompt.pack()

        self._sub = tk.Label(center, text='',
            font=(FONT_SANS, 14), bg=BG, fg=SUBTEXT)
        self._sub.pack(pady=8)

        if not HAS_AUDIO:
            tk.Label(center,
                text='\u26a0  sounddevice not installed \u2014 audio unavailable\n'
                     '(pip install sounddevice numpy)',
                font=(FONT_SANS, 13), bg=BG, fg=WARNING).pack(pady=14)

    def on_show(self):
        self._noise.stop()
        self._prompt.config(text='Press  Space  to Test Speakers', fg=TEXT)
        self._icon.config(fg=SUBTEXT)
        self._sub.config(text='')
        set_volume(40)
        self.app.root.bind('<space>', self._toggle)

    def on_hide(self):
        self._noise.stop()
        try:
            self.app.root.unbind('<space>')
        except:
            pass

    def _toggle(self, _=None):
        if self._noise.is_playing():
            self._noise.stop()
            self._prompt.config(text='Press  Space  to Test Speakers', fg=TEXT)
            self._icon.config(fg=SUBTEXT)
            self._sub.config(text='')
        else:
            self._noise.start(vol=0.4)
            self._prompt.config(text='Playing\u2026', fg=SUCCESS)
            self._icon.config(fg=SUCCESS)
            self._sub.config(text='Press Space again to stop')

# ─── Screen 3 : Keyboard ───────────────────────────────────────────────────────
class KeyboardScreen(BaseScreen):
    GAP   = 4

    # Total keyboard width in layout-units:
    #   main block (number row) = 15 units, gap = 0.8, nav/arrow cluster = 3 units
    _TOTAL_UNITS = sum(e[2] for e in KB_ROWS[1]) + 3.0 + 0.8

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._items   = {}
        self._pressed = set()
        self._done    = set()
        self._canvas  = None
        self._current_key_var = tk.StringVar(value='Current key: None')
        self._current_key_lbl = None

        # Compute UNIT so the keyboard fills ~90 % of screen width,
        # but also cap it so it fits vertically (header ~50, footer ~50,
        # title label ~55, legend label ~25 → ~180 px of chrome).
        sw = app.root.winfo_screenwidth()
        sh = app.root.winfo_screenheight()
        num_rows   = len(KB_ROWS) + 1        # +1 for the arrow/nav rows
        v_budget   = sh - 180                 # px available for the canvas
        unit_w     = int(sw * 0.90 / self._TOTAL_UNITS)
        unit_h     = int(v_budget / (num_rows + 0.5))
        self.UNIT  = max(40, min(unit_w, unit_h))
        self.KEY_H = int(self.UNIT * 0.87)
        self.ROW_H = int(self.UNIT * 1.03)

        self._build()

    def _build(self):
        tk.Label(self, text='KEYBOARD TEST', font=(FONT_SANS, 11, 'bold'),
            bg=BG, fg=SUBTEXT).pack(pady=(14, 3))
        tk.Label(self,
            text='Press every key — white = untested  ·  '
                 'bright green = pressed  ·  '
                 'light green = tested',
            font=(FONT_SANS, 10), bg=BG, fg=SUBTEXT).pack()

        outer = tk.Frame(self, bg=BG)
        outer.pack(expand=True)

        U  = self.UNIT
        cw = int(self._TOTAL_UNITS * U)
        ch = (len(KB_ROWS) + 1) * self.ROW_H + 30

        self._canvas = tk.Canvas(outer, width=cw, height=ch,
            bg=BG, highlightthickness=0)
        self._canvas.pack()
        self._draw_all_keys()

        self._current_key_lbl = tk.Label(
            outer,
            textvariable=self._current_key_var,
            font=(FONT_MONO, 18, 'bold'),
            bg=PANEL,
            fg=ACCENT,
            padx=18,
            pady=8
        )
        self._current_key_lbl.pack(pady=(12, 0))

    def _draw_all_keys(self):
        U = self.UNIT
        KH = self.KEY_H
        RH = self.ROW_H
        G  = self.GAP
        LEFT = 6

        # Main keyboard rows
        # Row 0 (Fn row) gets slightly tighter vertical space
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

        # Arrow keys — to the right of the main block
        main_w   = int(sum(e[2] for e in KB_ROWS[1]) * U) + LEFT
        arrow_x0 = main_w + 8

        # Nav cluster (Ins/Home/PgUp / Del/End/PgDn) — above the arrow keys
        for ni, nrow in enumerate(KB_NAV):
            y  = RH + (len(KB_ROWS) - 4 + ni) * RH + 10
            nx = arrow_x0
            for (ks, lbl, w) in nrow:
                pw = int(w * U) - G
                if ks is None:
                    nx += int(w * U)
                    continue
                self._draw_key(ks, lbl, nx, y, pw, KH)
                nx += int(w * U)

        for ai, arow in enumerate(KB_ARROWS):
            y  = RH + (len(KB_ROWS) - 2 + ai) * RH + 10
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
            fill=KEY_IDLE, outline='#9aa0b8', width=1, tags=('key', ks))
        # Scale font with key size; short labels get a bit larger
        fsz = max(7, int(self.UNIT * (0.20 if len(lbl) <= 5 else 0.17)))
        t = c.create_text(x + w // 2, y + h // 2, text=lbl,
            font=(FONT_SANS, fsz), fill=KEY_TXT_D, tags=('key', ks))
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
            except:
                pass
        return str(resolved)

    def _pretty_key_name(self, ev, resolved):
        if resolved in self._items:
            _, text_id = self._items[resolved]
            try:
                label = self._canvas.itemcget(text_id, 'text')
                if label:
                    return label
            except:
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

        # bind_all handles every key EXCEPT Tab and Space, which need special
        # treatment. Instance-level bindings on the canvas fire before Tk's
        # class-level focus-traversal logic, so returning 'break' here truly
        # suppresses traversal — unlike bind_all which runs too late.
        self.app.root.bind_all('<KeyPress>',   self._key_down)
        self.app.root.bind_all('<KeyRelease>', self._key_up)
        # Explicitly grab F10/media-mute globally so Tk doesn't treat F10 as
        # a menu accelerator and so laptop Fn layers (XF86AudioMute) still
        # highlight the F10 key on tester layouts.
        self.app.root.bind_all('<F10>',                 self._f10_down)
        self.app.root.bind_all('<KeyRelease-F10>',      self._f10_up)
        self.app.root.bind_all('<XF86AudioMute>',       self._f10_down)
        self.app.root.bind_all('<KeyRelease-XF86AudioMute>', self._f10_up)
        self._canvas.bind('<Tab>',          self._block_key)
        self._canvas.bind('<ISO_Left_Tab>', self._block_key)
        self._canvas.bind('<space>',        self._block_key)
        self._canvas.focus_set()

    def _block_key(self, ev):
        """Register the key press visually, suppress Tk's default action."""
        self._key_down(ev)
        return 'break'

    def on_hide(self):
        self.app.kb_block_traversal = False
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
        except:
            pass

    def _f10_down(self, ev):
        self._key_down(ev)
        return 'break'

    def _f10_up(self, ev):
        self._key_up(ev)
        return 'break'

    def _key_down(self, ev):
        print("keysym =", ev.keysym, " keycode =", ev.keycode)

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

# ─── Screen 4 : System Info ────────────────────────────────────────────────────
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

        # vw-style font sizing: scale relative to screen width
        sw = self.app.sw
        def vw(pct): return max(8, int(sw * pct / 100))

        tk.Label(self, text='SYSTEM INFORMATION', font=(FONT_SANS, vw(0.9), 'bold'),
            bg=BG, fg=SUBTEXT).pack(pady=(14, 10))

        info = self.app.system_info
        if not info:
            tk.Label(self, text='Loading system information\u2026',
                font=(FONT_SANS, vw(1.5)), bg=BG, fg=SUBTEXT).pack(expand=True)
            self.after(1500, self._retry)
            return

        cpu_type, cpu_series, cpu_freq = parse_cpu(info.get('cpu_string', 'Unknown'))
        ram_cfg, ram_total, ram_type   = parse_ram(info.get('ram_slots', []))

        scroll_frame = tk.Frame(self, bg=BG)
        scroll_frame.pack(expand=True, fill='both', padx=int(sw * 0.04))

        lbl_font = (FONT_SANS, vw(1.0))
        val_font = (FONT_MONO, vw(1.0))

        def row(label, val, val_color=TEXT):
            f = tk.Frame(scroll_frame, bg=BG)
            f.pack(fill='x', pady=int(sw * 0.002))
            tk.Label(f, text=label + ':', font=lbl_font,
                width=20, anchor='e', bg=BG, fg=SUBTEXT).pack(side='left')
            tk.Label(f, text=val, font=val_font,
                anchor='w', bg=BG, fg=val_color).pack(side='left', padx=(int(sw * 0.01), 0))

        def divider():
            tk.Frame(scroll_frame, bg=BORDER, height=1).pack(fill='x', pady=int(sw * 0.004))

        row('Make',  info.get('make', 'Unknown'))
        row('Model', info.get('model', 'Unknown'))
        row('Serial', info.get('serial', 'Unknown'))
        divider()
        row('CPU', cpu_type + '  ' + cpu_series)
        row('CPU Speed', cpu_freq)
        row('CPU (full string)', info.get('cpu_string', 'Unknown'), SUBTEXT)
        divider()

        row('RAM Config', ram_cfg)
        row('RAM Total',  ram_total)
        row('RAM Type',   ram_type)
        divider()

        bh  = info.get('battery_health',  'Unknown')
        bc  = info.get('battery_current', 'Unknown')
        bcy = info.get('battery_cycles',  'Unknown')
        bh_color  = (SUCCESS if bh != 'Unknown' and float(bh.rstrip('%')) >= 70
                     else (WARNING if bh != 'Unknown' else SUBTEXT))
        row('Battery Health',  bh,  bh_color if bh != 'Unknown' else SUBTEXT)
        row('Battery Charge',  bc)
        row('Battery Cycles',  bcy if bcy not in ('Unknown', '0') else 'N/A')

    def _retry(self):
        if self.app.system_info:
            self._build()

# ─── Screen 5 : Sync ───────────────────────────────────────────────────────────
class SyncScreen(BaseScreen):
    (STEP_IP, STEP_CSAD, STEP_NOTES,
     STEP_GRADE, STEP_SENDING, STEP_DONE) = range(6)

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._cfg   = load_cfg()
        self._step  = self.STEP_IP
        self._ip    = tk.StringVar(value=self._cfg.get('last_ip', ''))
        self._csad  = tk.StringVar()
        self._grade = tk.StringVar()
        self._notes = ''
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

    # Step 1 – IP ----------------------------------------------------------------
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
        self._status('Connecting to ' + ip + ':5050\u2026', WARNING)
        self.update()
        threading.Thread(target=self._test_conn, args=(ip,), daemon=True).start()

    def _test_conn(self, ip):
        ok = False
        msg = ''
        try:
            # Use a raw socket test so we don't depend on the server having
            # a specific /ping route — any open port on 5050 means it's up.
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
            self._status('\u2713 ' + msg, SUCCESS)
            self.after(500, lambda: self._goto(self.STEP_CSAD))
        else:
            self._status('\u2717 ' + msg, ERROR_C)

    # Step 2 – CSAD -------------------------------------------------------------
    def _show_csad(self):
        self._status('')
        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)

        tk.Label(box, text='CSAD Number',
            font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(pady=(0, 6))
        tk.Label(box, text='4\u20135 digits followed by a letter  \u2014  e.g.  12345A',
            font=(FONT_SANS, 12), bg=BG, fg=SUBTEXT).pack(pady=(0, 16))

        ef = tk.Frame(box, bg=BORDER, padx=2, pady=2)
        ef.pack()
        self._csad_entry = tk.Entry(ef, textvariable=self._csad,
            font=(FONT_MONO, 28), width=12, bg=PANEL, fg=ACCENT,
            insertbackground=ACCENT, bd=0, relief='flat', justify='center')
        self._csad_entry.pack(ipadx=12, ipady=10)
        self._csad_entry.focus_set()
        self._csad_entry.bind('<Return>', self._confirm_csad)

        tk.Label(box, text='Press Enter to confirm  \u00b7  Leave empty and press Enter for \u201c*\u201d',
            font=(FONT_SANS, 11), bg=BG, fg=SUBTEXT).pack(pady=(8, 0))

    def _confirm_csad(self, _=None):
        v = self._csad.get().strip().upper()
        if v == '':
            v = '*'
        elif not re.fullmatch(r'\d{4,5}[A-Z]', v):
            self._status(
                'Invalid CSAD \u2014 must be 4 or 5 digits followed by a letter.', ERROR_C)
            return
        self._csad.set(v)
        self._status('')
        self._goto(self.STEP_NOTES)

    # Step 3 – Condition notes --------------------------------------------------
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
        tk.Label(hint, text='Press Enter to continue (leave empty for \u201c*\u201d), or press Next \u25ba',
            font=(FONT_SANS, 11), bg=BG, fg=SUBTEXT).pack(anchor='w')

        # Enter submits notes; Shift+Enter inserts a newline without advancing
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

    # Allow Next button to advance from notes step
    def _on_next_from_notes(self):
        self._notes = self._notes_txt.get('1.0', 'end-1c').strip()

    # Step 4 – Grade ------------------------------------------------------------
    def _show_grade(self):
        self._status('')
        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)

        tk.Label(box, text='Condition Grade',
            font=(FONT_SANS, 18, 'bold'), bg=BG, fg=TEXT).pack(pady=(0, 4))
        tk.Label(box, text='Click a button or press A / B / C / D on your keyboard',
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
            except:
                pass

    def _select_grade(self, letter):
        self._grade.set(letter)
        self._unbind_grade_keys()
        self._goto(self.STEP_SENDING)

    # Step 5 – Sending ----------------------------------------------------------
    def _show_sending(self):
        box = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)
        tk.Label(box, text='\u21ba  Sending to server\u2026',
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
        ip   = self._cfg.get('last_ip', '127.0.0.1')

        payload = {
            'model':           info.get('model', 'Unknown'),
            'serial':          info.get('serial', 'Unknown'),
            'cpu_string':      info.get('cpu_string', 'Unknown'),
            'ram_slots':       info.get('ram_slots', []),
            'battery_health':  info.get('battery_health', 'Unknown'),
            'condition':       self._notes,
            'condition_grade': grade_map.get(self._grade.get(), 'A-Grade (Like-New)'),
            'csad_value':      self._csad.get(),
        }

        ok  = False
        msg = ''
        try:
            if HAS_REQUESTS:
                r = req.post('http://' + ip + ':5050/log', json=payload, timeout=8)
                # Surface HTTP-level errors before trying to parse JSON
                if r.status_code != 200:
                    msg = f'Server returned HTTP {r.status_code}'
                    try:
                        body = r.text.strip()
                        if body:
                            msg += ': ' + body[:200]
                    except:
                        pass
                else:
                    # HTTP 200 means the server accepted the record.
                    # Parse JSON for an optional human-readable message but do
                    # NOT fail the sync based on the status field — different
                    # server versions use different values ("ok", "OK",
                    # "success", "logged", etc.).
                    ok = True
                    try:
                        data = r.json()
                        # Only override ok=True if the server explicitly signals
                        # an error (status field present AND not any ok-ish value)
                        s_val = str(data.get('status', 'ok')).lower()
                        if s_val in ('error', 'fail', 'failed'):
                            ok  = False
                            msg = data.get('message', '') or f'Server reported failure: {s_val}'
                        else:
                            msg = data.get('message', '')
                    except Exception:
                        # Non-JSON 200 response is still a success
                        msg = ''
            else:
                msg = 'requests library not installed — pip install requests'
        except req.exceptions.ConnectionError as e:
            msg = f'Connection error — could not reach {ip}:5050\n{e}'
        except req.exceptions.Timeout:
            msg = f'Request timed out — server at {ip}:5050 did not respond in 8 seconds'
        except Exception as e:
            msg = f'{type(e).__name__}: {e}'

        self.after(0, lambda: self._send_result(ok, msg))

    def _send_result(self, ok, msg):
        # Set these BEFORE _goto, because _goto -> _render -> _show_done reads them
        self._last_ok  = ok
        self._last_msg = msg
        self._goto(self.STEP_DONE)

    # Step 6 – Done (result) ----------------------------------------------------
    def _show_done(self):
        ok  = getattr(self, '_last_ok',  False)
        msg = getattr(self, '_last_msg', '')

        info = self.app.system_info or {}
        box  = tk.Frame(self._content, bg=BG)
        box.pack(expand=True)

        if ok:
            tk.Label(box, text='\u2713',
                font=(FONT_SANS, 72), bg=BG, fg=SUCCESS).pack()
            tk.Label(box, text='Synced Successfully!',
                font=(FONT_SANS, 26, 'bold'), bg=BG, fg=SUCCESS).pack(pady=4)
            tk.Label(box,
                text=info.get('model', 'Unknown') + '  \u00b7  CSAD: ' + self._csad.get(),
                font=(FONT_SANS, 14), bg=BG, fg=SUBTEXT).pack(pady=4)
        else:
            tk.Label(box, text='\u2717',
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

    def _reset(self):
        self._csad.set('')
        self._grade.set('')
        self._notes = ''
        self._goto(self.STEP_IP)

    def _goto(self, step):
        self._step = step
        self._render()

    # Hook called by App.next_screen() when leaving this screen mid-flow
    def handle_next(self):
        """Return True to allow navigation, False to block."""
        if self._step == self.STEP_NOTES:
            # Capture notes before leaving
            try:
                self._notes = self._notes_txt.get('1.0', 'end-1c').strip()
            except:
                pass
        return True

# ─── Main App ──────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('LaptopTester')
        self.root.configure(bg=BG)
        # Fullscreen strategy for PuppyLinux / JWM and other X11 WMs:
        #
        # We use the EWMH _NET_WM_STATE_FULLSCREEN hint via Tk's -fullscreen
        # attribute.  This tells the window manager to give our window the full
        # screen, remove decorations, and slide the taskbar/panel beneath us —
        # even on JWM which honours the hint.  This is more reliable than
        # overrideredirect, which bypasses the WM but leaves the taskbar's own
        # override-redirect window free to paint over us.
        #
        # Belt-and-suspenders: we also set -topmost and re-assert fullscreen
        # after a short delay in case the WM needs a moment to settle.
        self.root.attributes('-fullscreen', True)
        self.root.attributes('-topmost', True)
        self.root.focus_force()

        def _reassert_fullscreen():
            self.root.attributes('-fullscreen', True)
            self.root.lift()
            self.root.focus_force()
        self.root.after(200, _reassert_fullscreen)
        self.root.after(800, _reassert_fullscreen)

        # Store screen dimensions for other widgets to use
        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()

        self.system_info  = {}
        self._idx         = 0
        self._active      = None
        # When True (keyboard screen active), Tab/Space/NextWindow are intercepted
        # at the App level so they never cause focus traversal.
        self.kb_block_traversal = False

        threading.Thread(target=self._load_info, daemon=True).start()

        self._build_ui()
        self._show(0)

        self.root.bind('<Shift-Right>', lambda e: self.next_screen())
        self.root.bind('<Shift-Left>',  lambda e: self.prev_screen())

    def _load_info(self):
        info = get_system_info()
        self.system_info = info
        self.root.after(0, lambda: self._header.set_model(short_model(info)))

    def _build_ui(self):
        tk.Frame(self.root, bg=ACCENT, height=2).pack(fill='x', side='top')

        self._header = HeaderBar(self.root, self)
        self._header.pack(fill='x', side='top')
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill='x', side='top')

        self._footer = FooterBar(self.root, self)
        self._footer.pack(fill='x', side='bottom')
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill='x', side='bottom')

        self._area = tk.Frame(self.root, bg=BG)
        self._area.pack(fill='both', expand=True)

        self._screens = [
            CameraScreen(self._area, self),
            SpeakerScreen(self._area, self),
            KeyboardScreen(self._area, self),
            InfoScreen(self._area, self),
            SyncScreen(self._area, self),
        ]

    def _show(self, idx):
        if self._active:
            self._active.on_hide()
            self._active.pack_forget()
        self._idx = idx
        scr = self._screens[idx]
        scr.pack(fill='both', expand=True)
        scr.on_show()
        self._active = scr
        self._footer.update_nav(idx, len(self._screens))

    def next_screen(self):
        if self._idx >= len(self._screens) - 1:
            return
        # Let sync screen capture notes before leaving
        if hasattr(self._active, 'handle_next'):
            self._active.handle_next()
        self._show(self._idx + 1)

    def prev_screen(self):
        if self._idx > 0:
            self._show(self._idx - 1)

    def quit_app(self):
        if self._active:
            try:
                self._active.on_hide()
            except:
                pass
        self.root.destroy()

    def poweroff(self):
        """Immediate poweroff with several fallbacks for Puppy/Linux systems."""
        commands = [
            ['busybox', 'poweroff', '-f'],
            ['poweroff', '-f'],
            ['/sbin/poweroff', '-f'],
            ['systemctl', 'poweroff', '--force', '--force'],
            ['halt', '-f', '-p'],
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

        # last-resort status message if everything failed
        try:
            print("Poweroff command failed.")
        except:
            pass

    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    App().run()
