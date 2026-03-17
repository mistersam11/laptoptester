#!/usr/bin/env python3
"""
Windows Laptop / PC Tester — PyQt6 version
Runs from USB, leaves no trace on the laptop.

Dependencies:
    pip install PyQt6 opencv-python sounddevice numpy

Package as EXE:
    pyinstaller --onefile --windowed windows_tester.py
"""

import sys
import threading
import subprocess
import re
from pathlib import Path
from collections import Counter

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QStackedWidget,
    QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFrame,
    QSizePolicy, QScrollArea,
)
from PyQt6.QtCore import (
    Qt, QTimer, QEvent, QRect, QObject, pyqtSignal,
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QPen, QKeySequence,
    QShortcut, QPixmap, QImage,
)

# ── optional deps ────────────────────────────────────────────────────────────────
try:
    import cv2
    HAS_CAMERA = True
except Exception:
    HAS_CAMERA = False

try:
    import sounddevice as sd
    import numpy as np
    HAS_AUDIO = True
except Exception:
    HAS_AUDIO = False

# ── theme ────────────────────────────────────────────────────────────────────────
BG        = '#0c0c14'
PANEL     = '#13131f'
HEADER_BG = '#0a0a12'
ACCENT    = '#3d9be9'
TEXT      = '#e8e8f0'
SUBTEXT   = '#6b6b88'
SUCCESS   = '#2ed573'
ERROR_C   = '#ff4757'
WARNING   = '#ffa502'
BORDER    = '#1e1e30'
KEY_IDLE  = '#dce3f5'
KEY_HELD  = '#1a6b30'
KEY_DONE  = '#6fcf97'
KEY_TXT_D = '#1a1a2e'
KEY_TXT_L = '#ffffff'
F_SANS    = 'Segoe UI'
F_MONO    = 'Consolas'

# ── system info ──────────────────────────────────────────────────────────────────

def script_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def _wmic(args):
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags  = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0   # SW_HIDE
        return subprocess.check_output(
            ['wmic'] + args, text=True, stderr=subprocess.DEVNULL,
            startupinfo=si,
            creationflags=subprocess.CREATE_NO_WINDOW)
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

def _storage_str(b):
    if not b or b <= 0:
        return 'Unknown'
    gb = b / (1024 ** 3)
    if gb < 192: return '128GB NVMe'
    if gb < 384: return '256GB NVMe'
    if gb < 768: return '512GB NVMe'
    return '1TB NVMe'

def detect_machine_mode():
    try:
        txt = _wmic(['path', 'Win32_Battery', 'get', 'Name', '/value'])
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith('Name=') and line.split('=', 1)[1].strip():
                return 'laptop'
    except Exception:
        pass
    return 'desktop'

def get_system_info():
    info = {
        'make': 'Unknown', 'model': 'Unknown', 'serial': 'Unknown',
        'cpu_string': 'Unknown', 'cpu_max_mhz': 0, 'ram_slots': [],
        'storage_size': 'Unknown',
        'battery_health': 'Unknown', 'battery_current': 'Unknown',
        'battery_cycles': 'Unknown',
    }
    # make / model
    try:
        cs = _parse_kv(_wmic(['computersystem', 'get', 'Manufacturer,Model', '/value']))
        make  = cs.get('Manufacturer', '').strip()
        model = cs.get('Model', '').strip()
        if make:
            info['make'] = 'Lenovo' if make.upper() == 'LENOVO' else make
        lenovo_model = ''
        if make.upper().startswith('LENOVO'):
            cp  = _parse_kv(_wmic(['csproduct', 'get', 'Vendor,Name,Version', '/value']))
            bad = {'', 'NONE', 'INVALID', 'TO BE FILLED BY O.E.M.'}
            v   = cp.get('Version', '').strip()
            n   = cp.get('Name', '').strip()
            lenovo_model = v if v.upper() not in bad else (n if n.upper() not in bad else '')
        info['model'] = lenovo_model or model or 'Unknown'
    except Exception:
        pass
    # serial
    try:
        d = _parse_kv(_wmic(['bios', 'get', 'SerialNumber', '/value']))
        info['serial'] = d.get('SerialNumber') or 'Unknown'
    except Exception:
        pass
    # cpu
    try:
        d = _parse_kv(_wmic(['cpu', 'get', 'Name,MaxClockSpeed', '/value']))
        if d.get('Name'):         info['cpu_string']  = d['Name']
        if d.get('MaxClockSpeed'): info['cpu_max_mhz'] = int(d['MaxClockSpeed'])
    except Exception:
        pass
    # ram
    try:
        mem_map = {
            20:'DDR', 21:'DDR2', 22:'DDR2 FB-DIMM', 24:'DDR3',
            26:'DDR4', 27:'LPDDR', 28:'LPDDR2', 29:'LPDDR3',
            30:'LPDDR4', 34:'DDR5', 35:'LPDDR5',
        }
        def vlist(alias, prop):
            raw = _wmic([alias, 'get', prop, '/value'])
            pre = prop + '='
            return [l[len(pre):].strip() for l in raw.splitlines()
                    if l.strip().startswith(pre)]
        caps   = vlist('memorychip', 'Capacity')
        smbios = vlist('memorychip', 'SMBIOSMemoryType')
        mtype  = vlist('memorychip', 'MemoryType')
        for i, cap_raw in enumerate(caps):
            try:
                cap = int(cap_raw)
            except Exception:
                continue
            if cap <= 0:
                continue
            gb       = round(cap / (1024 ** 3))
            ram_type = 'Unknown'
            for rl in (smbios, mtype):
                if i < len(rl):
                    try:
                        ram_type = mem_map.get(int(rl[i]), 'Unknown')
                    except Exception:
                        pass
                if ram_type != 'Unknown':
                    break
            info['ram_slots'].append({'size_gb': gb, 'type': ram_type})
    except Exception:
        pass
    # storage
    try:
        sizes = []
        for line in _wmic(['diskdrive', 'get', 'Size', '/value']).splitlines():
            if line.startswith('Size='):
                try:
                    sizes.append(int(line.split('=', 1)[1]))
                except Exception:
                    pass
        if sizes:
            info['storage_size'] = _storage_str(max(sizes))
    except Exception:
        pass
    # battery current charge
    try:
        d = _parse_kv(_wmic([
            'path', 'Win32_Battery', 'get', 'EstimatedChargeRemaining', '/value']))
        if d.get('EstimatedChargeRemaining'):
            info['battery_current'] = d['EstimatedChargeRemaining'] + '%'
    except Exception:
        pass
    # battery health — wmic only, no PowerShell (avoids console window flashes)
    design = full = 0
    try:
        d = _parse_kv(_wmic([
            'path', 'Win32_Battery', 'get',
            'DesignCapacity,FullChargeCapacity', '/value']))
        design = int(d.get('DesignCapacity', '0') or '0')
        full   = int(d.get('FullChargeCapacity', '0') or '0')
    except Exception:
        pass
    if design > 0 and full > 0:
        info['battery_health'] = f"{int(full / design * 100)}%"
    return info

def parse_cpu(raw, cpu_max_mhz=0):
    s             = raw.strip()
    cpu_type      = 'Unknown'
    cpu_series    = 'Unknown'
    m = re.search(r'Core\s*(?:\(TM\)\s*)?Ultra\s+([579])\s+(?:Pro\s+)?(\d{3}\w*)', s, re.I)
    if m:
        cpu_type, cpu_series = 'Intel Core Ultra ' + m.group(1), m.group(2)
    else:
        m = re.search(r'Core\s*(?:\(TM\)\s*)?(i[3579])-(\d{4,5}\w*)', s, re.I)
        if m:
            cpu_type, cpu_series = 'Intel Core ' + m.group(1).lower(), m.group(2)
    if cpu_type == 'Unknown':
        m = re.search(r'Ryzen\s+([3579])\s+(?:Pro\s+)?(\d{4}\w*)', s, re.I)
        if m:
            cpu_type, cpu_series = 'AMD Ryzen ' + m.group(1), m.group(2)
    freq_m = re.search(r'@\s*([\d.]+)\s*GHz', s, re.I)
    if freq_m:
        try:    cpu_freq = f'{float(freq_m.group(1)):.2f} GHz'
        except: cpu_freq = freq_m.group(1) + ' GHz'
    elif cpu_max_mhz:
        cpu_freq = f'{cpu_max_mhz / 1000.0:.2f} GHz'
    else:
        cpu_freq = 'Unknown'
    return cpu_type, cpu_series, cpu_freq

def parse_ram(slots):
    if not slots:
        return 'Unknown', 'Unknown', 'Unknown'
    sizes, types = [], []
    for slot in slots:
        if isinstance(slot, dict):
            gb = slot.get('size_gb')
            t  = str(slot.get('type', 'Unknown')).upper()
            if isinstance(gb, int) and gb > 0: sizes.append(gb)
            if t and t != 'UNKNOWN':           types.append(t)
    if not sizes:
        return 'Unknown', 'Unknown', 'Unknown'
    sc  = Counter(sizes)
    cfg = ' + '.join(f'{q} x {s}GB' for s, q in sorted(sc.items()))
    return cfg, f'{sum(sizes)}GB', Counter(types).most_common(1)[0][0] if types else 'Unknown'

# ── audio ────────────────────────────────────────────────────────────────────────

class NoisePlayer:
    def __init__(self):
        self._stream           = None
        self._winsound_active  = False

    def start(self, vol=0.4):
        if self.is_playing():
            return
        if HAS_AUDIO:
            try:
                def cb(out, frames, t, status):
                    out[:] = (np.random.randn(frames, 2) * vol).astype(np.float32)
                self._stream = sd.OutputStream(
                    samplerate=44100, channels=2, dtype='float32', callback=cb)
                self._stream.start()
                return
            except Exception:
                self._stream = None
        try:
            import winsound
            self._winsound_active = True
            threading.Thread(
                target=lambda: winsound.PlaySound(
                    'SystemAsterisk',
                    winsound.SND_ALIAS | winsound.SND_LOOP | winsound.SND_ASYNC),
                daemon=True).start()
        except Exception:
            pass

    def stop(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._winsound_active:
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_ASYNC)
            except Exception:
                pass
            self._winsound_active = False

    def is_playing(self):
        return self._stream is not None or self._winsound_active


def set_system_volume_40_percent_best_effort():
    """Unmute and set volume using Win32 mixer API via ctypes.
    No subprocess spawned — silent and instant, no window flash."""
    def _do():
        try:
            import ctypes
            # Set the WaveOut mixer to ~40%.  waveOutSetVolume takes a DWORD
            # with the left channel in the low word and right in the high word.
            vol = 0x6666          # ~40 % of 0xFFFF
            ctypes.windll.winmm.waveOutSetVolume(0, vol | (vol << 16))
        except Exception:
            pass
        try:
            # Also unmute the master mixer line if it exists
            import ctypes
            MMSYSERR_NOERROR = 0
            MIXER_OBJECTF_MIXER = 0
            MIXERLINE_COMPONENTTYPE_DST_SPEAKERS = 4
            MIXERCONTROL_CONTROLTYPE_MUTE = 0x80000002

            class MIXERLINE(ctypes.Structure):
                _fields_ = [
                    ('cbStruct', ctypes.c_uint),
                    ('dwDestination', ctypes.c_uint),
                    ('dwSource', ctypes.c_uint),
                    ('dwLineID', ctypes.c_uint),
                    ('fdwLine', ctypes.c_uint),
                    ('dwUser', ctypes.POINTER(ctypes.c_uint)),
                    ('dwComponentType', ctypes.c_uint),
                    ('cChannels', ctypes.c_uint),
                    ('cConnections', ctypes.c_uint),
                    ('cControls', ctypes.c_uint),
                    ('szShortName', ctypes.c_char * 16),
                    ('szName', ctypes.c_char * 64),
                    ('Target', ctypes.c_byte * 64),
                ]
            # Skip the full mixer API — waveOutSetVolume above is sufficient
            pass
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()

# ── keyboard layout ──────────────────────────────────────────────────────────────

KB_ROWS = [
    [('Escape','Esc',1.0),(None,'',0.6),
     ('F1','F1',1.0),('F2','F2',1.0),('F3','F3',1.0),('F4','F4',1.0),(None,'',0.3),
     ('F5','F5',1.0),('F6','F6',1.0),('F7','F7',1.0),('F8','F8',1.0),(None,'',0.3),
     ('F9','F9',1.0),('F10','F10',1.0),('F11','F11',1.0),('F12','F12',1.0)],
    [('grave','`',1.0),('1','1',1.0),('2','2',1.0),('3','3',1.0),
     ('4','4',1.0),('5','5',1.0),('6','6',1.0),('7','7',1.0),
     ('8','8',1.0),('9','9',1.0),('0','0',1.0),('minus','-',1.0),
     ('equal','=',1.0),('BackSpace','⌫ Back',2.0)],
    [('Tab','Tab',1.5),('q','Q',1.0),('w','W',1.0),('e','E',1.0),
     ('r','R',1.0),('t','T',1.0),('y','Y',1.0),('u','U',1.0),
     ('i','I',1.0),('o','O',1.0),('p','P',1.0),('bracketleft','[',1.0),
     ('bracketright',']',1.0),('backslash','\\',1.5)],
    [('Caps_Lock','Caps',1.75),('a','A',1.0),('s','S',1.0),('d','D',1.0),
     ('f','F',1.0),('g','G',1.0),('h','H',1.0),('j','J',1.0),
     ('k','K',1.0),('l','L',1.0),('semicolon',';',1.0),('apostrophe',"'",1.0),
     ('Return','Enter',2.25)],
    [('Shift_L','⇧ Shift',2.25),('z','Z',1.0),('x','X',1.0),('c','C',1.0),
     ('v','V',1.0),('b','B',1.0),('n','N',1.0),('m','M',1.0),
     ('comma',',',1.0),('period','.',1.0),('slash','/',1.0),
     ('Shift_R','Shift ⇧',2.75)],
    [('Control_L','Ctrl',1.5),('Super_L','❖',1.25),('Alt_L','Alt',1.25),
     ('space','Space',6.0),
     ('Alt_R','Alt',1.25),('Menu','▤',1.0),('Control_R','Ctrl',1.5)],
]
KB_ARROWS = [
    [(None,'',1.0),('Up','↑',1.0),(None,'',1.0)],
    [('Left','←',1.0),('Down','↓',1.0),('Right','→',1.0)],
]
KB_NAV = [
    [('Insert','Ins',1.0),('Home','Home',1.0),('Prior','PgUp',1.0)],
    [('Delete','Del',1.0),('End','End',1.0),('Next','PgDn',1.0)],
]

# Windows virtual key codes → KB_ROWS keysym
# Include both generic (0x10/0x11/0x12) and left/right specific codes because
# nativeVirtualKey() may return either depending on the Qt version and driver.
# Also include Space (0x20) and other keys whose nativeVirtualKey() may be
# returned instead of a Qt.Key enum on some Windows / driver combinations.
VK_TO_SYM = {
    # Modifiers — generic and L/R specific
    0x10:'Shift_L',   0xA0:'Shift_L',   0xA1:'Shift_R',
    0x11:'Control_L', 0xA2:'Control_L', 0xA3:'Control_R',
    0x12:'Alt_L',     0xA4:'Alt_L',     0xA5:'Alt_R',
    0x5B:'Super_L',   0x5C:'Super_L',
    # Keys that sometimes come through as raw VK codes
    0x20:'space',     # VK_SPACE
    0x08:'BackSpace', # VK_BACK
    0x09:'Tab',       # VK_TAB
    0x0D:'Return',    # VK_RETURN
    0x1B:'Escape',    # VK_ESCAPE
    0x14:'Caps_Lock', # VK_CAPITAL
    0x2D:'Insert',    0x2E:'Delete',
    0x24:'Home',      0x23:'End',
    0x21:'Prior',     0x22:'Next',
    0x25:'Left',      0x26:'Up',
    0x27:'Right',     0x28:'Down',
    0x70:'F1',  0x71:'F2',  0x72:'F3',  0x73:'F4',
    0x74:'F5',  0x75:'F6',  0x76:'F7',  0x77:'F8',
    0x78:'F9',  0x79:'F10', 0x7A:'F11', 0x7B:'F12',
    0x5D:'Menu',
}

# Qt.Key → KB_ROWS keysym (fallback when nativeVirtualKey gives nothing useful)
QT_TO_SYM = {
    Qt.Key.Key_Escape:'Escape',
    Qt.Key.Key_F1:'F1', Qt.Key.Key_F2:'F2', Qt.Key.Key_F3:'F3',
    Qt.Key.Key_F4:'F4', Qt.Key.Key_F5:'F5', Qt.Key.Key_F6:'F6',
    Qt.Key.Key_F7:'F7', Qt.Key.Key_F8:'F8', Qt.Key.Key_F9:'F9',
    Qt.Key.Key_F10:'F10', Qt.Key.Key_F11:'F11', Qt.Key.Key_F12:'F12',
    Qt.Key.Key_QuoteLeft:'grave',
    Qt.Key.Key_1:'1', Qt.Key.Key_2:'2', Qt.Key.Key_3:'3', Qt.Key.Key_4:'4',
    Qt.Key.Key_5:'5', Qt.Key.Key_6:'6', Qt.Key.Key_7:'7', Qt.Key.Key_8:'8',
    Qt.Key.Key_9:'9', Qt.Key.Key_0:'0',
    Qt.Key.Key_Minus:'minus', Qt.Key.Key_Equal:'equal',
    Qt.Key.Key_Backspace:'BackSpace',
    Qt.Key.Key_Tab:'Tab', Qt.Key.Key_Backtab:'Tab',
    Qt.Key.Key_Q:'q', Qt.Key.Key_W:'w', Qt.Key.Key_E:'e', Qt.Key.Key_R:'r',
    Qt.Key.Key_T:'t', Qt.Key.Key_Y:'y', Qt.Key.Key_U:'u', Qt.Key.Key_I:'i',
    Qt.Key.Key_O:'o', Qt.Key.Key_P:'p',
    Qt.Key.Key_BracketLeft:'bracketleft', Qt.Key.Key_BracketRight:'bracketright',
    Qt.Key.Key_Backslash:'backslash',
    Qt.Key.Key_CapsLock:'Caps_Lock',
    Qt.Key.Key_A:'a', Qt.Key.Key_S:'s', Qt.Key.Key_D:'d', Qt.Key.Key_F:'f',
    Qt.Key.Key_G:'g', Qt.Key.Key_H:'h', Qt.Key.Key_J:'j', Qt.Key.Key_K:'k',
    Qt.Key.Key_L:'l',
    Qt.Key.Key_Semicolon:'semicolon', Qt.Key.Key_Apostrophe:'apostrophe',
    Qt.Key.Key_Return:'Return', Qt.Key.Key_Enter:'Return',
    Qt.Key.Key_Z:'z', Qt.Key.Key_X:'x', Qt.Key.Key_C:'c', Qt.Key.Key_V:'v',
    Qt.Key.Key_B:'b', Qt.Key.Key_N:'n', Qt.Key.Key_M:'m',
    Qt.Key.Key_Comma:'comma', Qt.Key.Key_Period:'period', Qt.Key.Key_Slash:'slash',
    Qt.Key.Key_Space:'space',
    Qt.Key.Key_Menu:'Menu',
    Qt.Key.Key_Insert:'Insert', Qt.Key.Key_Home:'Home', Qt.Key.Key_PageUp:'Prior',
    Qt.Key.Key_Delete:'Delete', Qt.Key.Key_End:'End', Qt.Key.Key_PageDown:'Next',
    Qt.Key.Key_Up:'Up', Qt.Key.Key_Down:'Down',
    Qt.Key.Key_Left:'Left', Qt.Key.Key_Right:'Right',
    Qt.Key.Key_Print:'F12', Qt.Key.Key_ScrollLock:'F12', Qt.Key.Key_Pause:'F12',
    # Modifier key fallbacks — used when nativeVirtualKey() returns 0
    Qt.Key.Key_Shift:   'Shift_L',
    Qt.Key.Key_Control: 'Control_L',
    Qt.Key.Key_Alt:     'Alt_L',
    Qt.Key.Key_AltGr:   'Alt_R',
    Qt.Key.Key_Meta:    'Super_L',
}

# ── styling helpers ──────────────────────────────────────────────────────────────

def lbl(text, size=12, bold=False, color=TEXT, mono=False):
    w = QLabel(text)
    f = QFont(F_MONO if mono else F_SANS, size)
    if bold: f.setBold(True)
    w.setFont(f)
    w.setStyleSheet(f'color: {color}; background: transparent;')
    return w

def btn(text, size=13, bg=PANEL, fg=TEXT, hover=ACCENT, bold=False):
    w = QPushButton(text)
    f = QFont(F_SANS, size)
    if bold: f.setBold(True)
    w.setFont(f)
    w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    _btn_style(w, bg, fg, hover)
    return w

def _btn_style(w, bg, fg, hover):
    w.setStyleSheet(f"""
        QPushButton {{
            background-color: {bg}; color: {fg};
            border: none; padding: 10px 22px;
        }}
        QPushButton:hover {{ background-color: {hover}; color: white; }}
        QPushButton:disabled {{ background-color: {SUBTEXT}; color: {BG}; }}
    """)

def hline():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f'background-color: {BORDER}; max-height: 1px; border: none;')
    return f

# ── info loader (thread-safe signal) ─────────────────────────────────────────────

class InfoLoader(QObject):
    finished = pyqtSignal(dict)

    def run(self):
        self.finished.emit(get_system_info())

# ── base screen ──────────────────────────────────────────────────────────────────

class BaseScreen(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setStyleSheet(f'background-color: {BG};')

    def on_show(self): pass
    def on_hide(self): pass

# ── screen 1: camera ─────────────────────────────────────────────────────────────

class CameraScreen(BaseScreen):
    def __init__(self, app):
        super().__init__(app)
        self._cap   = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 14, 0, 20)
        lo.setSpacing(0)
        lo.addWidget(lbl('CAMERA TEST', 11, bold=True, color=SUBTEXT),
                     alignment=Qt.AlignmentFlag.AlignHCenter)
        lo.addSpacing(6)
        self._view = QLabel()
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view.setStyleSheet('background-color: black;')
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lo.addWidget(self._view, 1)

    def on_show(self):
        self._view.setPixmap(QPixmap())
        self._view.setText('')
        self._view.setFont(QFont(F_SANS, 10))
        if not HAS_CAMERA:
            self._view.setText('Camera not working\n(OpenCV not available)')
            self._view.setStyleSheet(f'background-color: black; color: {SUBTEXT};')
            return
        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            self._view.setText('Camera not working')
            self._view.setFont(QFont(F_SANS, 28, QFont.Weight.Bold))
            self._view.setStyleSheet(f'background-color: black; color: {SUBTEXT};')
            self._cap = None
            return
        self._view.setStyleSheet('background-color: black;')
        self._timer.start(40)

    def on_hide(self):
        self._timer.stop()
        if self._cap:
            self._cap.release()
            self._cap = None

    def _tick(self):
        if not self._cap:
            return
        ok, frame = self._cap.read()
        if not ok:
            return
        try:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w  = frame.shape[:2]
            cw    = max(self._view.width(),  100)
            ch    = max(self._view.height(), 100)
            scale = min(cw / w, ch / h)
            nw, nh = int(w * scale), int(h * scale)
            frame  = cv2.resize(frame, (nw, nh))
            img    = QImage(frame.data, nw, nh, nw * 3, QImage.Format.Format_RGB888)
            self._view.setPixmap(QPixmap.fromImage(img))
        except Exception:
            pass

# ── screen 2: speaker ────────────────────────────────────────────────────────────

class SpeakerScreen(BaseScreen):
    def __init__(self, app):
        super().__init__(app)
        self._noise = NoisePlayer()

        lo = QVBoxLayout(self)
        lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(lbl('SPEAKER TEST', 11, bold=True, color=SUBTEXT),
                     alignment=Qt.AlignmentFlag.AlignHCenter)
        lo.addSpacing(20)
        self._icon = lbl('♪', 80, color=SUBTEXT)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(self._icon)
        self._prompt = lbl(
            'Press  Spacebar  or click the button to test speakers', 22, bold=True)
        self._prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(self._prompt)
        self._sub = lbl('', 14, color=SUBTEXT)
        self._sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(self._sub)
        lo.addSpacing(8)
        self._toggle_btn = btn('▶  Play White Noise', size=16, bg=ACCENT,
                                fg='white', hover='#58aef0', bold=True)
        self._toggle_btn.setFixedHeight(52)
        self._toggle_btn.clicked.connect(self.toggle)
        lo.addWidget(self._toggle_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        if not HAS_AUDIO:
            lo.addSpacing(10)
            lo.addWidget(
                lbl('⚠ sounddevice unavailable – system-sound fallback active',
                    12, color=WARNING),
                alignment=Qt.AlignmentFlag.AlignHCenter)

    def on_show(self):
        self._noise.stop()
        self._reset_ui()
        set_system_volume_40_percent_best_effort()

    def on_hide(self):
        self._noise.stop()
        self._reset_ui()

    def _reset_ui(self):
        self._prompt.setText(
            'Press  Spacebar  or click the button to test speakers')
        self._prompt.setStyleSheet(f'color: {TEXT}; background: transparent;')
        self._icon.setStyleSheet(f'color: {SUBTEXT}; background: transparent;')
        self._sub.setText('')
        self._toggle_btn.setText('▶  Play White Noise')
        _btn_style(self._toggle_btn, ACCENT, 'white', '#58aef0')

    def toggle(self):
        """Called by button click or Space shortcut."""
        if self._noise.is_playing():
            self._noise.stop()
            self._reset_ui()
        else:
            self._noise.start(vol=0.4)
            self._prompt.setText('Playing...')
            self._prompt.setStyleSheet(f'color: {SUCCESS}; background: transparent;')
            self._icon.setStyleSheet(f'color: {SUCCESS}; background: transparent;')
            self._sub.setText('Press Spacebar or click again to stop')
            self._toggle_btn.setText('■  Stop')
            _btn_style(self._toggle_btn, '#2f7d32', 'white', '#3f9a43')

# ── screen 3: keyboard ───────────────────────────────────────────────────────────

class KeyCanvas(QWidget):
    """Custom-painted keyboard; no focus policy, pure display + data."""
    TOTAL_UNITS = sum(e[2] for e in KB_ROWS[1]) + 3.0 + 0.8
    GAP         = 4

    def __init__(self, sw, sh, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._items   = {}   # keysym → (QRect, label)
        self._pressed = set()
        self._done    = set()

        v_budget = sh - 200
        unit_w   = int(sw * 0.90 / self.TOTAL_UNITS)
        unit_h   = int(v_budget / (len(KB_ROWS) + 1.5))
        self.U   = max(40, min(unit_w, unit_h))
        self.KH  = int(self.U * 0.87)
        self.RH  = int(self.U * 1.03)

        cw = int(self.TOTAL_UNITS * self.U)
        ch = (len(KB_ROWS) + 1) * self.RH + 30
        self.setFixedSize(cw, ch)
        self._build_rects()

    def _build_rects(self):
        U, KH, RH, G, LEFT = self.U, self.KH, self.RH, self.GAP, 6
        for ri, row in enumerate(KB_ROWS):
            y = 4 if ri == 0 else RH + (ri - 1) * RH + 10
            x = LEFT
            for ks, lab, w in row:
                pw = int(w * U) - G
                if ks is None:
                    x += int(w * U)
                    continue
                self._items[ks] = (QRect(x, y, pw, KH), lab)
                x += int(w * U)
        main_w = int(sum(e[2] for e in KB_ROWS[1]) * U) + LEFT
        ax0    = main_w + 8
        for ni, nrow in enumerate(KB_NAV):
            y  = RH + (len(KB_ROWS) - 4 + ni) * RH + 10
            nx = ax0
            for ks, lab, w in nrow:
                if ks is None: nx += int(w * U); continue
                self._items[ks] = (QRect(nx, y, int(w * U) - G, KH), lab)
                nx += int(w * U)
        for ai, arow in enumerate(KB_ARROWS):
            y  = RH + (len(KB_ROWS) - 2 + ai) * RH + 10
            ax = ax0
            for ks, lab, w in arow:
                if ks is None: ax += int(w * U); continue
                self._items[ks] = (QRect(ax, y, int(w * U) - G, KH), lab)
                ax += int(w * U)

    def press(self, ks):
        if ks in self._items:
            self._pressed.add(ks)
            self._done.add(ks)
            self.update()

    def release(self, ks):
        if ks in self._items:
            self._pressed.discard(ks)
            self.update()

    def clear_pressed(self):
        """Move all stuck held keys to done on screen re-entry."""
        self._pressed.clear()
        self.update()

    def label_for(self, ks):
        if ks and ks in self._items:
            return self._items[ks][1]
        return ks or '?'

    def paintEvent(self, _event):
        p   = QPainter(self)
        fsz = max(7, int(self.U * 0.18))
        p.setFont(QFont(F_SANS, fsz))
        for ks, (rect, lab) in self._items.items():
            if ks in self._pressed:
                fill, txt = QColor(KEY_HELD), QColor(KEY_TXT_L)
            elif ks in self._done:
                fill, txt = QColor(KEY_DONE), QColor(KEY_TXT_D)
            else:
                fill, txt = QColor(KEY_IDLE), QColor(KEY_TXT_D)
            p.fillRect(rect, fill)
            p.setPen(QPen(QColor('#9aa0b8'), 1))
            p.drawRect(rect)
            p.setPen(txt)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, lab)


class KeyboardScreen(BaseScreen):
    def __init__(self, app):
        super().__init__(app)
        self._pressed = set()

        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        lo.addWidget(lbl('KEYBOARD TEST', 11, bold=True, color=SUBTEXT),
                     alignment=Qt.AlignmentFlag.AlignHCenter)
        lo.addSpacing(3)
        lo.addWidget(
            lbl('Press every key — white = untested  ·  '
                'bright green = pressed  ·  light green = tested',
                10, color=SUBTEXT),
            alignment=Qt.AlignmentFlag.AlignHCenter)

        outer = QWidget()
        outer.setStyleSheet(f'background-color: {BG};')
        ol = QVBoxLayout(outer)
        ol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ol.setContentsMargins(0, 0, 0, 0)

        screen = QApplication.primaryScreen().geometry()
        self._canvas = KeyCanvas(screen.width(), screen.height())
        ol.addWidget(self._canvas, alignment=Qt.AlignmentFlag.AlignCenter)

        self._cur_lbl = lbl('Current key: None', 18, bold=True, color=ACCENT, mono=True)
        self._cur_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cur_lbl.setStyleSheet(
            f'color: {ACCENT}; background-color: {PANEL}; padding: 8px 18px;')
        ol.addSpacing(12)
        ol.addWidget(self._cur_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(outer, 1)

    def on_show(self):
        self._pressed.clear()
        self._canvas.clear_pressed()
        # Intercept ALL key events at the application level — no focus needed.
        QApplication.instance().installEventFilter(self)

    def on_hide(self):
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, _obj, event):
        t = event.type()
        if t == QEvent.Type.KeyPress:
            self._do_press(event)
            # Let Shift+Right / Shift+Left / Shift+Escape propagate so
            # the QShortcut-based navigation / quit still fires.
            mods = event.modifiers()
            key  = event.key()
            if (mods == Qt.KeyboardModifier.ShiftModifier and
                    key in (Qt.Key.Key_Right, Qt.Key.Key_Left, Qt.Key.Key_Escape)):
                return False
            return True   # consume everything else
        if t == QEvent.Type.KeyRelease:
            self._do_release(event)
            return True
        return False

    def _resolve(self, event):
        nv = event.nativeVirtualKey()
        if nv in VK_TO_SYM:
            return VK_TO_SYM[nv]
        return QT_TO_SYM.get(event.key())

    def _do_press(self, event):
        ks = self._resolve(event)
        if not ks:
            return
        self._pressed.add(ks)
        self._canvas.press(ks)
        self._cur_lbl.setText('Current key: ' + self._canvas.label_for(ks))

    def _do_release(self, event):
        ks = self._resolve(event)
        if not ks:
            return
        self._pressed.discard(ks)
        self._canvas.release(ks)
        if self._pressed:
            last = sorted(self._pressed)[-1]
            self._cur_lbl.setText(
                'Current key: ' + self._canvas.label_for(last))
        else:
            self._cur_lbl.setText('Current key: None')

# ── screen 4: system info ─────────────────────────────────────────────────────────

class InfoScreen(BaseScreen):
    def __init__(self, app):
        super().__init__(app)
        self._has_real_info = False
        # Poll every 500 ms while showing "Loading..." until info arrives
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._check_info)
        self._lo = QVBoxLayout(self)
        self._lo.setContentsMargins(0, 14, 0, 0)
        self._lo.setSpacing(0)

    def on_show(self):
        self._build()
        if not self._has_real_info:
            self._poll.start()

    def on_hide(self):
        self._poll.stop()

    def _check_info(self):
        if self.app.system_info:
            self._poll.stop()
            self._build()

    def _clear(self):
        while self._lo.count():
            item = self._lo.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

    def _build(self):
        self._clear()
        sw  = QApplication.primaryScreen().geometry().width()
        tsz = max(8, int(sw * 0.009))
        rsz = max(8, int(sw * 0.010))

        title = lbl('SYSTEM INFORMATION', tsz, bold=True, color=SUBTEXT)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._lo.addWidget(title)
        self._lo.addSpacing(10)

        info = self.app.system_info
        if not info:
            self._has_real_info = False
            w = lbl('Loading system information…',
                    max(8, int(sw * 0.015)), color=SUBTEXT)
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._lo.addWidget(w, 1)
            return

        self._has_real_info = True
        self._poll.stop()

        cpu_type, cpu_series, cpu_freq = parse_cpu(
            info.get('cpu_string', 'Unknown'), info.get('cpu_max_mhz', 0))
        ram_cfg, ram_total, ram_type = parse_ram(info.get('ram_slots', []))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f'background-color: {BG}; border: none;')
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setStyleSheet(f'background-color: {BG};')
        il    = QVBoxLayout(inner)
        pad   = int(sw * 0.04)
        il.setContentsMargins(pad, 0, pad, 20)
        il.setSpacing(0)
        lw    = int(sw * 0.18)
        vpad  = int(sw * 0.002)

        def row(label, val, color=TEXT):
            f  = QWidget()
            f.setStyleSheet(f'background-color: {BG};')
            rl = QHBoxLayout(f)
            rl.setContentsMargins(0, vpad, 0, vpad)
            ll = lbl(label + ':', rsz, color=SUBTEXT)
            ll.setFixedWidth(lw)
            ll.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(ll)
            rl.addSpacing(int(sw * 0.01))
            vl = lbl(val, rsz, mono=True, color=color)
            vl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(vl, 1)
            il.addWidget(f)

        def div():
            sp = int(sw * 0.003)
            il.addSpacing(sp)
            il.addWidget(hline())
            il.addSpacing(sp)

        row('Make',   info.get('make',  'Unknown'))
        row('Model',  info.get('model', 'Unknown'))
        row('Serial', info.get('serial','Unknown'))
        div()
        row('CPU',        cpu_type + ' ' + cpu_series)
        row('CPU Speed',  cpu_freq)
        row('CPU (full)', info.get('cpu_string', 'Unknown'), SUBTEXT)
        div()
        row('RAM Config', ram_cfg)
        row('RAM Total',  ram_total)
        row('RAM Type',   ram_type)
        div()
        if self.app.mode != 'desktop':
            row('Battery Health', info.get('battery_health', 'Unknown'))
            row('Battery Charge', info.get('battery_current','Unknown'))
            row('Battery Cycles', info.get('battery_cycles', 'Unknown'))
            div()
        row('Storage Size (AA)', info.get('storage_size', 'Unknown'))
        il.addStretch(1)

        scroll.setWidget(inner)
        self._lo.addWidget(scroll, 1)

        br  = QWidget()
        br.setStyleSheet(f'background-color: {BG};')
        brl = QHBoxLayout(br)
        brl.setContentsMargins(14, 10, 14, 10)
        brl.addStretch()
        eb = btn('Exit  ✕', size=12, bg='#c0392b', fg='white',
                 hover='#e74c3c', bold=True)
        eb.clicked.connect(self.app.quit_app)
        brl.addWidget(eb)
        self._lo.addWidget(br)

# ── screen 5: MAR ────────────────────────────────────────────────────────────────

class MARScreen(BaseScreen):
    def __init__(self, app):
        super().__init__(app)
        lo = QVBoxLayout(self)
        lo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lo.addWidget(lbl('MICROSOFT AUTHORIZATION (MAR)', 11, bold=True, color=SUBTEXT),
                     alignment=Qt.AlignmentFlag.AlignHCenter)
        lo.addSpacing(8)
        inst = lbl('Click the button below to run Install_DPK.bat.\n'
                   'When finished, click MAR Done.', 14, color=TEXT)
        inst.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lo.addWidget(inst)
        lo.addSpacing(10)
        self._run_btn = btn('Run Install_DPK.bat', size=16, bg=ACCENT,
                             fg='white', hover='#58aef0')
        self._run_btn.setFixedHeight(52)
        self._run_btn.clicked.connect(self._launch)
        lo.addWidget(self._run_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        lo.addSpacing(6)
        self._status = lbl('', 13, color=SUBTEXT)
        self._status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._status.setWordWrap(True)
        lo.addWidget(self._status)

    def on_show(self):
        self._status.setText('')
        self._status.setStyleSheet(f'color: {SUBTEXT}; background: transparent;')
        self._run_btn.setEnabled(True)
        self._run_btn.setText('Run Install_DPK.bat')
        _btn_style(self._run_btn, ACCENT, 'white', '#58aef0')

    def _launch(self):
        bat = script_dir() / 'MARTOOLS' / 'Install_DPK.bat'
        if not bat.is_file():
            self._status.setText(f'Install_DPK.bat not found at:\n{bat}')
            self._status.setStyleSheet(f'color: {ERROR_C}; background: transparent;')
            return
        try:
            subprocess.Popen(
                ['cmd', '/c', str(bat)],
                cwd=str(bat.parent),
                creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        except Exception as e:
            self._status.setText(f'Could not run bat: {e}')
            self._status.setStyleSheet(f'color: {ERROR_C}; background: transparent;')
            return

        self._run_btn.setEnabled(False)
        self._run_btn.setText('Running…')
        self.app.win.shrink_for_mar(self._mar_done)

    def _mar_done(self):
        next_idx = min(self.app.win._idx + 1, len(self.app.win._screens) - 1)
        self.app.win.restore_from_mar(jump_to=next_idx)

# ── app handle (thin proxy passed to screens) ─────────────────────────────────────

class AppHandle:
    def __init__(self, win):
        self.win = win

    @property
    def system_info(self): return self.win.system_info
    @property
    def mode(self):        return self.win.mode
    def quit_app(self):    self.win.close()

# ── main window ──────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Windows Laptop / PC Tester')
        self.setStyleSheet(f'background-color: {BG};')
        self.system_info  = {}
        self.mode         = detect_machine_mode()
        self._idx         = 0
        self._active      = None
        self._screens     = []
        self._titles      = []
        self._app         = AppHandle(self)

        # Navigation & quit shortcuts — ApplicationShortcut fires regardless
        # of which widget has focus. This is the key advantage over tkinter.
        # NOTE: Space is intentionally NOT a QShortcut — ApplicationShortcut
        # shortcuts are matched before the keyboard screen's event filter in
        # Qt6, which would prevent Space from registering on the keyboard test.
        # Instead, Space is handled in keyPressEvent below.
        for seq, slot in [
            ('Shift+Right',  self.next_screen),
            ('Shift+Left',   self.prev_screen),
            ('Shift+Escape', self.close),
        ]:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(slot)

        self._build()
        # Stay on top so Explorer and other windows can't steal the foreground
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.showFullScreen()

        # Load system info on a background thread; signal back to main thread
        self._loader = InfoLoader()
        self._loader.finished.connect(self._on_info_loaded)
        threading.Thread(target=self._loader.run, daemon=True).start()

        self._show(0)

    def keyPressEvent(self, event):
        # Space toggles the speaker test. This only fires when the keyboard
        # screen is NOT active (its event filter would consume Space first).
        if event.key() == Qt.Key.Key_Space and isinstance(self._active, SpeakerScreen):
            self._active.toggle()
        else:
            super().keyPressEvent(event)

    def _on_info_loaded(self, info):
        self.system_info = info
        # InfoScreen polls via its own timer and will self-update

    # ── UI construction ──────────────────────────────────────────────────────────

    def _build(self):
        central = QWidget()
        central.setStyleSheet(f'background-color: {BG};')
        self.setCentralWidget(central)
        ml = QVBoxLayout(central)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        # Header
        header = QWidget()
        header.setFixedHeight(44)
        header.setStyleSheet(f'background-color: {HEADER_BG};')
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 0, 10, 0)
        self._hdr_lbl = lbl('Windows Laptop / PC Tester', 12, bold=True)
        hl.addWidget(self._hdr_lbl)
        hl.addStretch()
        xb = btn('✕  EXIT', size=11, bg='#2a0808', fg=ERROR_C,
                  hover='#4a1010', bold=True)
        xb.clicked.connect(self.close)
        hl.addWidget(xb)
        ml.addWidget(header)
        ml.addWidget(hline())

        # Screen stack
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f'background-color: {BG};')

        if self.mode == 'desktop':
            self._screens = [
                SpeakerScreen(self._app),
                MARScreen(self._app),
                InfoScreen(self._app),
            ]
            self._titles = ['Speaker Test', 'MAR', 'System Info']
        else:
            self._screens = [
                CameraScreen(self._app),
                SpeakerScreen(self._app),
                KeyboardScreen(self._app),
                MARScreen(self._app),
                InfoScreen(self._app),
            ]
            self._titles = ['Camera Test', 'Speaker Test', 'Keyboard Test',
                             'MAR', 'System Info']

        for s in self._screens:
            self._stack.addWidget(s)
        ml.addWidget(self._stack, 1)
        ml.addWidget(hline())

        # Footer
        footer = QWidget()
        footer.setFixedHeight(50)
        footer.setStyleSheet(f'background-color: {HEADER_BG};')
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(12, 8, 12, 8)
        self._prev_btn = btn('◀  Previous', size=12)
        self._prev_btn.clicked.connect(self.prev_screen)
        fl.addWidget(self._prev_btn)
        fl.addStretch()
        self._nav_lbl = lbl('', 11, color=SUBTEXT)
        fl.addWidget(self._nav_lbl)
        fl.addStretch()
        self._next_btn = btn('Next  ▶', size=12)
        self._next_btn.clicked.connect(self.next_screen)
        fl.addWidget(self._next_btn)
        ml.addWidget(footer)

    def _show(self, idx):
        if self._active:
            self._active.on_hide()
        self._idx    = idx
        self._active = self._screens[idx]
        self._stack.setCurrentIndex(idx)
        self._active.on_show()

        title = self._titles[idx] if idx < len(self._titles) else ''
        self._nav_lbl.setText(f'{title}  ·  {idx + 1} / {len(self._screens)}')
        self._prev_btn.setEnabled(idx > 0)
        fg = TEXT if idx > 0 else SUBTEXT
        self._prev_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PANEL}; color: {fg};
                border: none; padding: 10px 22px;
            }}
            QPushButton:hover {{
                background-color: {'#3d9be9' if idx > 0 else PANEL};
                color: {'white' if idx > 0 else SUBTEXT};
            }}
            QPushButton:disabled {{ background-color: {PANEL}; color: {SUBTEXT}; }}
        """)

    def next_screen(self):
        if self._idx < len(self._screens) - 1:
            self._show(self._idx + 1)

    def prev_screen(self):
        if self._idx > 0:
            self._show(self._idx - 1)

    # ── MAR overlay ──────────────────────────────────────────────────────────────

    def shrink_for_mar(self, done_callback):
        """Hide the main window and show a small green MAR Done button window."""
        sg     = self.screen().geometry()
        ww, wh = 320, 120
        x = sg.width()  - ww - 20
        y = sg.height() - wh - 60

        # Build a standalone frameless window — avoids fighting the app
        # stylesheet and setCentralWidget signal/redraw issues.
        self._mar_win = QWidget(None,
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint)
        self._mar_win.setGeometry(x, y, ww, wh)
        # Override app-level stylesheet explicitly so green actually shows
        self._mar_win.setStyleSheet('background-color: #27ae60;')

        lo = QVBoxLayout(self._mar_win)
        lo.setContentsMargins(0, 0, 0, 0)

        db = QPushButton('MAR Done')
        db.setFont(QFont(F_SANS, 18, QFont.Weight.Bold))
        db.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        db.setStyleSheet("""
            QPushButton {
                background-color: #27ae60; color: white; border: none;
            }
            QPushButton:hover { background-color: #2ecc71; }
        """)
        db.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        db.clicked.connect(done_callback)
        lo.addWidget(db)

        self.hide()
        self._mar_win.show()

    def restore_from_mar(self, jump_to=None):
        """Close the MAR Done window and restore fullscreen."""
        if hasattr(self, '_mar_win') and self._mar_win:
            self._mar_win.close()
            self._mar_win = None
        idx = jump_to if jump_to is not None else self._idx
        self._active = None
        self._build()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.showFullScreen()
        self._show(idx)

    # ── cleanup ──────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._active:
            try:
                self._active.on_hide()
            except Exception:
                pass
        event.accept()

# ── entry point ──────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(f"""
        QWidget {{
            background-color: {BG};
            color: {TEXT};
            font-family: '{F_SANS}';
        }}
        QScrollBar:vertical {{
            background: {PANEL}; width: 8px; border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {SUBTEXT}; min-height: 20px; border-radius: 4px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
    """)
    win = MainWindow()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()