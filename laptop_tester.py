#!/usr/bin/env python3

import os
import re
import sys
import time
import subprocess
from collections import Counter
from datetime import datetime

import cv2
import numpy as np
import psutil
import pygame
import urllib.request
import urllib.error
import json

# ---------------------------------------------------
# INIT
# ---------------------------------------------------

pygame.init()
pygame.mixer.init()
pygame.font.init()

screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
WIDTH, HEIGHT = screen.get_size()
pygame.display.set_caption("Laptop Hardware Tester")

font_large = pygame.font.SysFont("Arial", 42, bold=True)
font       = pygame.font.SysFont("Arial", 28)
font_small = pygame.font.SysFont("Arial", 18)

clock = pygame.time.Clock()

# ---------------------------------------------------
# COLORS
# ---------------------------------------------------

WHITE       = (240, 240, 240)
BLACK       = (20,  20,  20)
GRAY        = (60,  60,  60)
LIGHT_GRAY  = (120, 120, 120)
GREEN       = (0,   170, 0)
LIGHT_GREEN = (120, 255, 120)
BLUE        = (70,  130, 255)
RED         = (200, 40,  40)
ORANGE      = (255, 165, 0)

# ---------------------------------------------------
# CONFIG
# ---------------------------------------------------

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "ip_config.txt")
SERVER_PORT = 5050

# ---------------------------------------------------
# IP CONFIG
# ---------------------------------------------------

def load_saved_ip(default="192.168.3.84"):
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return f.read().strip()
    except:
        pass
    return default


def save_ip(ip_address):
    try:
        with open(CONFIG_FILE, "w") as f:
            f.write(ip_address.strip())
    except Exception as e:
        print("Could not save IP:", e)

# ---------------------------------------------------
# SERVER COMMS
# ---------------------------------------------------

def ping_server(ip):
    """Returns True if the server is reachable."""
    try:
        url = f"http://{ip}:{SERVER_PORT}/ping"
        req = urllib.request.urlopen(url, timeout=3)
        return req.status == 200
    except:
        return False


def post_laptop_data(ip, payload):
    """
    POST laptop data to the server.
    Returns (success: bool, message: str)
    """
    try:
        url  = f"http://{ip}:{SERVER_PORT}/log"
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            return True, body.get("message", "Success")

    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            return False, body.get("message", str(e))
        except:
            return False, f"HTTP {e.code}"

    except Exception as e:
        return False, str(e)

# ---------------------------------------------------
# GLOBAL HELPERS
# ---------------------------------------------------

def quit_program():
    pygame.quit()
    sys.exit()


def draw_exit_button():
    exit_btn = Button("Exit", (WIDTH - 150, 20, 120, 45), RED)
    exit_btn.draw()
    return exit_btn


def handle_keyboard_navigation(event):
    """Global navigation using SHIFT + Arrow keys."""
    if event.type == pygame.KEYDOWN:
        mods = pygame.key.get_mods()
        if mods & pygame.KMOD_SHIFT:
            if event.key == pygame.K_RIGHT: return "next"
            if event.key == pygame.K_LEFT:  return "back"
            if event.key == pygame.K_ESCAPE: return "exit"
    return None

# ---------------------------------------------------
# BUTTON
# ---------------------------------------------------

class Button:
    def __init__(self, text, rect, color=GRAY):
        self.text  = text
        self.rect  = pygame.Rect(rect)
        self.color = color

    def draw(self):
        mouse = pygame.mouse.get_pos()
        color = BLUE if self.rect.collidepoint(mouse) else self.color
        pygame.draw.rect(screen, color, self.rect, border_radius=8)
        label = font.render(self.text, True, WHITE)
        screen.blit(label, (
            self.rect.centerx - label.get_width()  // 2,
            self.rect.centery - label.get_height() // 2
        ))

    def clicked(self, event):
        return event.type == pygame.MOUSEBUTTONDOWN and self.rect.collidepoint(event.pos)

# ---------------------------------------------------
# CAMERA
# ---------------------------------------------------

def camera_screen():
    cap = cv2.VideoCapture(0)

    while True:
        screen.fill(BLACK)

        exit_btn = draw_exit_button()
        prev_btn = Button("Previous", (40, HEIGHT - 70, 180, 50))
        next_btn = Button("Continue", (WIDTH - 240, HEIGHT - 70, 180, 50))

        for event in pygame.event.get():
            nav = handle_keyboard_navigation(event)
            if nav == "exit":   quit_program()
            elif nav == "back": cap.release(); return "back"
            elif nav == "next": cap.release(); return "next"

            if exit_btn.clicked(event): cap.release(); quit_program()
            if prev_btn.clicked(event): cap.release(); return "back"
            if next_btn.clicked(event): cap.release(); return "next"

        ret, frame = cap.read()
        if ret:
            frame    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, _  = frame.shape
            aspect   = w / h
            max_w    = WIDTH  - 300
            max_h    = HEIGHT - 200
            target_w = min(max_w, int(max_h * aspect))
            target_h = int(target_w / aspect)
            frame    = cv2.resize(frame, (target_w, target_h))
            frame    = np.rot90(frame)
            surface  = pygame.surfarray.make_surface(frame)
            screen.blit(surface, (WIDTH // 2 - target_w // 2, HEIGHT // 2 - target_h // 2))

        title = font_large.render("Camera Test", True, WHITE)
        screen.blit(title, (40, 30))
        prev_btn.draw()
        next_btn.draw()

        pygame.display.flip()
        clock.tick(30)

# ---------------------------------------------------
# WIFI
# ---------------------------------------------------

def get_wifi_interface():
    net_path = "/sys/class/net"
    if not os.path.exists(net_path):
        return None
    for iface in os.listdir(net_path):
        if os.path.exists(os.path.join(net_path, iface, "wireless")):
            return iface
    return None


def connect_wifi_wpa(interface, ssid, password):
    try:
        config_path = "/tmp/wpa_supplicant.conf"
        with open(config_path, "w") as f:
            subprocess.run(["wpa_passphrase", ssid, password], stdout=f, check=True)
        subprocess.run(["pkill", "-f", f"wpa_supplicant.*{interface}"], check=False)
        subprocess.run(["wpa_supplicant", "-B", "-i", interface, "-c", config_path], check=True)
        subprocess.run(["dhclient", interface], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print("Connection failed:", e)
        return False


def disconnect_wifi(interface):
    try:
        subprocess.run(["dhclient", "-r", interface], check=False)
        subprocess.run(["pkill", "-f", f"wpa_supplicant.*{interface}"], check=False)
        return True
    except Exception as e:
        print("Disconnect failed:", e)
        return False


def get_wifi_info_wpa(interface, target_ssid):
    try:
        ip_result = subprocess.run(
            ["ip", "-4", "addr", "show", interface], capture_output=True, text=True
        )
        ip = None
        for line in ip_result.stdout.splitlines():
            if line.strip().startswith("inet "):
                ip = line.split()[1].split("/")[0]
                break

        ssid_result = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True)
        ssid = ssid_result.stdout.strip()

        signal_result = subprocess.run(["iwconfig", interface], capture_output=True, text=True)
        signal = None
        for line in signal_result.stdout.splitlines():
            if "Signal level" in line:
                signal = line.strip()
                break

        connected = bool(ip and ssid == target_ssid)
        return connected, ssid if connected else None, signal, ip

    except Exception as e:
        print("WiFi check failed:", e)
        return False, None, None, None


def wifi_screen():
    # Ask for SSID and password each session, defaulting to the hardcoded values
    WIFI_SSID     = "Engineering"
    WIFI_PASSWORD = "Csad!123"

    status_text = "Checking WiFi..."
    color       = ORANGE

    interface = None
    connected = False
    ssid = signal = ip = None

    wifi_check_interval = 1.0
    last_wifi_check     = 0

    while True:
        current_time = time.time()

        exit_btn       = draw_exit_button()
        prev_btn       = Button("Previous",   (40, HEIGHT - 70, 180, 50))
        next_btn       = Button("Continue",   (WIDTH - 240, HEIGHT - 70, 180, 50))
        connect_btn    = Button("Connect",    (WIDTH // 2 - 220, HEIGHT // 2 + 140, 180, 50), BLUE)
        disconnect_btn = Button("Disconnect", (WIDTH // 2 + 40,  HEIGHT // 2 + 140, 180, 50), RED)

        for event in pygame.event.get():
            if event.type == pygame.QUIT: quit_program()

            nav = handle_keyboard_navigation(event)
            if nav == "exit":   quit_program()
            elif nav == "back": return "back"
            elif nav == "next": return "next"

            if event.type == pygame.MOUSEBUTTONDOWN:
                pos = pygame.mouse.get_pos()
                if exit_btn.rect.collidepoint(pos):    quit_program()
                if prev_btn.rect.collidepoint(pos):    return "back"
                if next_btn.rect.collidepoint(pos):    return "next"
                if not connected and connect_btn.rect.collidepoint(pos):
                    if interface:
                        status_text = "Connecting..."
                        color = ORANGE
                        connect_wifi_wpa(interface, WIFI_SSID, WIFI_PASSWORD)
                if connected and disconnect_btn.rect.collidepoint(pos):
                    if interface:
                        disconnect_wifi(interface)

        screen.fill(BLACK)

        title = font_large.render("WiFi Check", True, WHITE)
        screen.blit(title, (40, 30))

        status_surf = font_large.render(status_text, True, color)
        screen.blit(status_surf, (WIDTH // 2 - status_surf.get_width() // 2, HEIGHT // 2 - 80))

        if connected:
            y = HEIGHT // 2
            for line in [f"SSID: {ssid}", f"Signal: {signal or 'N/A'}", f"IP: {ip or 'No IP'}"]:
                t = font.render(line, True, WHITE)
                screen.blit(t, (WIDTH // 2 - t.get_width() // 2, y))
                y += 40

        (connect_btn if not connected else disconnect_btn).draw()
        prev_btn.draw()
        next_btn.draw()

        pygame.display.flip()
        clock.tick(30)

        if current_time - last_wifi_check > wifi_check_interval:
            last_wifi_check = current_time
            interface = get_wifi_interface()

            if not interface:
                status_text = "No WiFi Adapter Detected"
                color = RED
                connected = False
                continue

            connected, ssid, signal, ip = get_wifi_info_wpa(interface, WIFI_SSID)
            if connected:
                status_text = f"Connected to {ssid}"
                color = GREEN
            else:
                status_text = "Not Connected"
                color = RED

# ---------------------------------------------------
# SPEAKER
# ---------------------------------------------------

def generate_white_noise():
    sample_rate = 44100
    duration    = 3
    noise       = np.random.uniform(-1, 1, (sample_rate * duration, 2))
    return pygame.sndarray.make_sound((noise * 32767).astype(np.int16))


def speaker_screen():
    try:
        subprocess.call(["amixer", "sset", "Master", "unmute"])
        subprocess.call(["amixer", "sset", "Master", "40%"])
    except:
        pass

    sound   = generate_white_noise()
    playing = False

    while True:
        screen.fill(BLACK)

        exit_btn = draw_exit_button()
        prev_btn = Button("Previous", (40, HEIGHT - 70, 180, 50))
        next_btn = Button("Continue", (WIDTH - 240, HEIGHT - 70, 180, 50))
        exit_btn.draw()
        prev_btn.draw()
        next_btn.draw()

        for event in pygame.event.get():
            nav = handle_keyboard_navigation(event)
            if nav == "exit":   quit_program()
            elif nav == "back": sound.stop(); return "back"
            elif nav == "next": sound.stop(); return "next"

            if exit_btn.clicked(event): sound.stop(); quit_program()
            if prev_btn.clicked(event): sound.stop(); return "back"
            if next_btn.clicked(event): sound.stop(); return "next"

            if event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                if not playing: sound.play(-1); playing = True
                else:           sound.stop();   playing = False

        title = font_large.render("Speaker Test", True, WHITE)
        screen.blit(title, (40, 30))

        status_text = "Playing sound" if playing else "Press SPACEBAR to test speakers"
        t = font.render(status_text, True, WHITE)
        screen.blit(t, (WIDTH // 2 - t.get_width() // 2, HEIGHT // 2))

        pygame.display.flip()
        clock.tick(30)

# ---------------------------------------------------
# KEYBOARD
# ---------------------------------------------------

def keyboard_screen():
    key_states = {}
    last_key   = None

    continue_btn = Button("Continue", (WIDTH - 240, HEIGHT - 70, 180, 50))
    prev_btn     = Button("Previous", (40, HEIGHT - 70, 180, 50))

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

    base_key_w, base_key_h = 70, 65
    base_gap     = 8
    num_rows     = 1 + len(main_rows) + 1 + 3
    total_height = num_rows * base_key_h + (num_rows + 1) * base_gap
    scale        = min((HEIGHT * 0.75) / total_height, WIDTH / 1280, 1.0)
    key_w        = int(base_key_w * scale)
    key_h        = int(base_key_h * scale)
    gap          = int(base_gap * scale)
    key_font     = pygame.font.SysFont("Arial", max(int(22 * scale), 16))
    title_font   = pygame.font.SysFont("Arial", max(int(42 * scale), 28), bold=True)

    key_width_map = {
        "TAB": 1.5, "CAPS": 1.5, "ENTER": 1.5,
        "SHIFT": 2,  "BACKSPACE": 1.8
    }

    key_name_map = {
        "RETURN":"ENTER","CAPS LOCK":"CAPS",
        "LEFT SHIFT":"SHIFT","RIGHT SHIFT":"SHIFT",
        "LEFT CTRL":"CTRL","RIGHT CTRL":"CTRL",
        "LEFT ALT":"ALT","RIGHT ALT":"ALT",
        "DELETE":"DEL","INSERT":"INS","PAGE UP":"PGUP",
        "PAGE DOWN":"PGDN","BACKSLASH":"\\","ESCAPE":"ESC",
        "UP":"UP","DOWN":"DOWN","LEFT":"LEFT","RIGHT":"RIGHT"
    }

    def draw_key(x, y, w, h, label):
        pressed = label in key_states
        color   = LIGHT_GREEN if label == last_key else GREEN if pressed else LIGHT_GRAY
        pygame.draw.rect(screen, color, (x, y, w, h), border_radius=6)
        pygame.draw.rect(screen, BLACK, (x, y, w, h), 2, border_radius=6)
        text = key_font.render(label, True, BLACK)
        screen.blit(text, (x + w // 2 - text.get_width() // 2,
                           y + h // 2 - text.get_height() // 2))

    while True:
        screen.fill(BLACK)

        exit_btn = draw_exit_button()
        exit_btn.draw()
        prev_btn.draw()
        continue_btn.draw()

        for event in pygame.event.get():
            nav = handle_keyboard_navigation(event)
            if nav == "exit":   quit_program()
            elif nav == "back": return "back"
            elif nav == "next": return "next"

            if exit_btn.clicked(event):     quit_program()
            if prev_btn.clicked(event):     return "back"
            if continue_btn.clicked(event): return "next"

            if event.type == pygame.KEYDOWN:
                if pygame.K_F1 <= event.key <= pygame.K_F12:
                    name = f"F{event.key - pygame.K_F1 + 1}"
                else:
                    name = pygame.key.name(event.key).upper()
                    name = key_name_map.get(name, name)
                key_states[name] = True
                last_key = name

        title = title_font.render("Keyboard Test", True, WHITE)
        screen.blit(title, (40, 30))

        keyboard_width = max(15 * (key_w + gap), WIDTH // 2)
        start_x = (WIDTH - keyboard_width) // 2
        start_y = int(HEIGHT * 0.12)

        x, y = start_x, start_y
        for key in f_keys:
            draw_key(x, y, key_w, key_h, key)
            x += key_w + gap
        y += key_h + gap * 2

        for row in main_rows:
            x = start_x
            for key in row:
                w = int(key_w * key_width_map.get(key, 1))
                draw_key(x, y, w, key_h, key)
                x += w + gap
            y += key_h + gap

        x = start_x
        y += gap
        for key in bottom_row:
            w = int(6 * key_w) if key == "SPACE" else key_w
            draw_key(x, y, w, key_h, key)
            x += w + gap
        y += key_h + gap * 2

        nav_x = start_x + key_w * 12
        for i, key in enumerate(nav_top):
            draw_key(nav_x + i * (key_w + gap), y, key_w, key_h, key)
        for i, key in enumerate(nav_bottom):
            draw_key(nav_x + i * (key_w + gap), y + key_h + gap, key_w, key_h, key)

        arrow_y = y + 2 * (key_h + gap)
        draw_key(nav_x + key_w,             arrow_y,               key_w, key_h, "UP")
        draw_key(nav_x,                     arrow_y + key_h + gap, key_w, key_h, "LEFT")
        draw_key(nav_x + key_w,             arrow_y + key_h + gap, key_w, key_h, "DOWN")
        draw_key(nav_x + 2 * key_w + gap,   arrow_y + key_h + gap, key_w, key_h, "RIGHT")

        pygame.display.flip()
        clock.tick(30)

# ---------------------------------------------------
# SYSTEM INFO
# ---------------------------------------------------

def get_system_info():
    def read_dmi(field):
        path = f"/sys/class/dmi/id/{field}"
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return f.read().strip()
            except:
                return "Unavailable"
        return "Unavailable"

    manufacturer = read_dmi("sys_vendor")
    model        = read_dmi("product_name")
    serial       = read_dmi("product_serial")

    if manufacturer and model.lower().startswith(manufacturer.lower()):
        model = model[len(manufacturer):].strip()

    return manufacturer, model, serial


def get_cpu_info():
    model   = "Unavailable"
    cores   = psutil.cpu_count(logical=False)
    threads = psutil.cpu_count(logical=True)
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    model = line.split(":")[1].strip()
                    break
    except:
        pass
    return model, cores, threads


def get_ram_info():
    total_ram = round(psutil.virtual_memory().total / (1024 ** 3), 2)
    ram_slots = []
    try:
        output  = subprocess.check_output(
            ["dmidecode", "--type", "17"], text=True, stderr=subprocess.DEVNULL
        )
        devices = output.split("Memory Device")
        for device in devices:
            if "Size:" in device and "No Module Installed" not in device:
                size = type_ = speed = locator = "Unknown"
                for line in device.splitlines():
                    line = line.strip()
                    if   line.startswith("Size:"):    size    = line.split(":")[1].strip()
                    elif line.startswith("Type:"):    type_   = line.split(":")[1].strip()
                    elif line.startswith("Speed:"):   speed   = line.split(":")[1].strip()
                    elif line.startswith("Locator:"): locator = line.split(":")[1].strip()
                ram_slots.append(f"{locator} - {size} - {type_} - {speed}")
    except:
        ram_slots.append("Run as root for detailed RAM info")
    return total_ram, ram_slots


def get_battery_info():
    percent = None
    health  = "Unavailable"
    cycles  = "Unavailable"

    try:
        battery = psutil.sensors_battery()
        if battery:
            percent = battery.percent
    except:
        pass

    try:
        base_path = "/sys/class/power_supply"
        bat_dirs  = [d for d in os.listdir(base_path) if d.startswith("BAT")]
        if not bat_dirs:
            return percent, health, cycles

        bat_path = os.path.join(base_path, bat_dirs[0])

        def read_value(name):
            path = os.path.join(bat_path, name)
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read().strip()
            return None

        full   = read_value("energy_full")        or read_value("charge_full")
        design = read_value("energy_full_design") or read_value("charge_full_design")

        if full and design:
            full, design = float(full), float(design)
            if design > 0:
                health = f"{round((full / design) * 100)}%"

        cycle = read_value("cycle_count")
        if cycle:
            cycles = cycle

        if percent is None:
            now = read_value("energy_now") or read_value("charge_now")
            if now and full:
                percent = round((float(now) / float(full)) * 100)

    except Exception:
        pass

    return percent, health, cycles

# ---------------------------------------------------
# INFO SCREEN
# ---------------------------------------------------

def battery_screen():
    percent, health, cycles     = get_battery_info()
    manufacturer, model, serial = get_system_info()
    cpu_model, cores, threads   = get_cpu_info()
    total_ram, ram_slots        = get_ram_info()

    while True:
        screen.fill(BLACK)

        exit_btn = draw_exit_button()
        prev_btn = Button("Previous", (40, HEIGHT - 70, 180, 50))
        next_btn = Button("Continue", (WIDTH - 240, HEIGHT - 70, 180, 50))
        prev_btn.draw()
        next_btn.draw()

        for event in pygame.event.get():
            nav = handle_keyboard_navigation(event)
            if nav == "exit":   quit_program()
            elif nav == "back": return "back"
            elif nav == "next": return "next"

            if exit_btn.clicked(event): quit_program()
            if prev_btn.clicked(event): return "back"
            if next_btn.clicked(event): return "next"

        title = font_large.render("Full System Information", True, WHITE)
        screen.blit(title, (40, 30))

        y            = 120
        line_spacing = 35

        for line in [
            "===== SYSTEM =====",
            f"Manufacturer: {manufacturer}",
            f"Model: {model}",
            f"Serial: {serial}",
            "",
            "===== CPU =====",
            f"Model: {cpu_model}",
            f"Cores: {cores} | Threads: {threads}",
            "",
            "===== RAM =====",
            f"Total RAM: {total_ram} GB",
        ]:
            screen.blit(font.render(line, True, WHITE), (80, y))
            y += line_spacing

        for slot in ram_slots:
            screen.blit(font_small.render(slot, True, LIGHT_GRAY), (100, y))
            y += 25

        y += 20
        for line in [
            "===== BATTERY =====",
            f"Charge: {percent if percent else 'N/A'}%",
            f"Health: {health}",
            f"Cycle Count: {cycles}"
        ]:
            screen.blit(font.render(line, True, WHITE), (80, y))
            y += line_spacing

        pygame.display.flip()
        clock.tick(30)

# ---------------------------------------------------
# FINAL SCREEN
# ---------------------------------------------------

def final_screen():
    last_ip = load_saved_ip()

    server_status   = "idle"   # idle | checking | ok | error
    sync_status     = ""
    sync_color      = WHITE

    entering_ip        = False
    entering_csad      = False
    entering_condition = False

    ip_input        = last_ip
    csad_input      = ""
    condition_input = ""

    cursor_visible = True
    cursor_timer   = time.time()

    while True:
        screen.fill(BLACK)

        exit_btn  = draw_exit_button()
        prev_btn  = Button("Previous",  (40, HEIGHT - 70, 180, 50))
        power_btn = Button("Power Off", (WIDTH - 240, HEIGHT - 70, 180, 50))

        # Connect button cycles: idle → check → ok/error
        if server_status == "idle":
            connect_btn = Button("Connect to PC", (WIDTH // 2 - 260, HEIGHT - 70, 220, 50), BLUE)
        elif server_status == "checking":
            connect_btn = Button("Checking...",   (WIDTH // 2 - 260, HEIGHT - 70, 220, 50), ORANGE)
        elif server_status == "ok":
            connect_btn = Button("Connected ✓",   (WIDTH // 2 - 260, HEIGHT - 70, 220, 50), GREEN)
        else:
            connect_btn = Button("Retry Connect", (WIDTH // 2 - 260, HEIGHT - 70, 220, 50), RED)

        sync_btn = Button(
            "Sync to Log",
            (WIDTH // 2 + 40, HEIGHT - 70, 220, 50),
            GREEN if server_status == "ok" else LIGHT_GRAY
        )

        for event in pygame.event.get():
            if event.type == pygame.QUIT: quit_program()

            nav = handle_keyboard_navigation(event)
            if nav == "exit":   quit_program()
            elif nav == "back": return "back"

            if exit_btn.clicked(event):   quit_program()
            if prev_btn.clicked(event):   return "back"
            if power_btn.clicked(event):
                subprocess.call(["sync"])
                subprocess.call(["busybox", "poweroff", "-f"])

            # Connect button — open IP input or re-ping
            if connect_btn.clicked(event):
                entering_ip   = True
                ip_input      = last_ip
                server_status = "idle"

            # Sync button
            if sync_btn.clicked(event):
                if server_status != "ok":
                    sync_status = "Connect to PC first"
                    sync_color  = RED
                else:
                    entering_csad   = True
                    csad_input      = ""
                    condition_input = ""

            # ---- CSAD Input ----
            if entering_csad and event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    entering_csad = False
                elif event.key == pygame.K_RETURN:
                    val = csad_input.strip()
                    if val == "" or re.fullmatch(r"\d{5}[A-Za-z]", val):
                        csad_input         = val.upper()
                        entering_csad      = False
                        entering_condition = True
                    else:
                        sync_status = "CSAD: 5 digits + letter, or leave empty"
                        sync_color  = RED
                elif event.key == pygame.K_BACKSPACE:
                    csad_input = csad_input[:-1]
                else:
                    csad_input += event.unicode

            # ---- Condition Input ----
            elif entering_condition and event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    entering_condition = False
                elif event.key == pygame.K_RETURN:
                    # Gather all data and POST
                    try:
                        _, model, serial       = get_system_info()
                        cpu_string, _, _       = get_cpu_info()
                        _, ram_slots           = get_ram_info()
                        _, battery_health, _   = get_battery_info()

                        payload = {
                            "model":          model,
                            "serial":         serial,
                            "cpu_string":     cpu_string,
                            "ram_slots":      ram_slots,
                            "battery_health": battery_health,
                            "condition":      condition_input.strip(),
                            "csad_value":     csad_input.strip(),
                        }

                        success, message = post_laptop_data(last_ip, payload)

                        if success:
                            sync_status = "SYNC SUCCESSFUL"
                            sync_color  = GREEN
                        else:
                            sync_status = f"SYNC FAILED: {message}"
                            sync_color  = RED

                    except Exception as e:
                        sync_status = f"ERROR: {str(e)}"
                        sync_color  = RED

                    entering_condition = False

                elif event.key == pygame.K_BACKSPACE:
                    condition_input = condition_input[:-1]
                else:
                    condition_input += event.unicode

            # ---- IP Input ----
            if entering_ip and event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    entering_ip = False
                elif event.key == pygame.K_RETURN:
                    last_ip       = ip_input.strip()
                    save_ip(last_ip)
                    entering_ip   = False
                    server_status = "checking"
                elif event.key == pygame.K_BACKSPACE:
                    ip_input = ip_input[:-1]
                else:
                    ip_input += event.unicode

        # ---- Ping server (outside event loop so it runs after IP is confirmed) ----
        if server_status == "checking":
            reachable     = ping_server(last_ip)
            server_status = "ok" if reachable else "error"
            if reachable:
                sync_status = f"Connected to {last_ip}"
                sync_color  = GREEN
            else:
                sync_status = f"Could not reach {last_ip}:{SERVER_PORT}"
                sync_color  = RED

        # ---- Draw UI ----
        title = font_large.render("Finished", True, WHITE)
        screen.blit(title, (WIDTH // 2 - title.get_width() // 2, 120))

        # Show current server IP quietly below title
        ip_label = font_small.render(f"Server: {last_ip}:{SERVER_PORT}", True, LIGHT_GRAY)
        screen.blit(ip_label, (WIDTH // 2 - ip_label.get_width() // 2, 180))

        # Cursor blink
        if time.time() - cursor_timer > 0.5:
            cursor_visible = not cursor_visible
            cursor_timer   = time.time()
        cursor_char = "|" if cursor_visible else ""

        # ---- IP Popup ----
        if entering_ip:
            popup = pygame.Rect(WIDTH // 2 - 300, HEIGHT // 2 - 80, 600, 160)
            pygame.draw.rect(screen, GRAY, popup, border_radius=10)
            screen.blit(font.render("Enter PC IP Address (ESC to cancel)", True, WHITE), (popup.x + 20, popup.y + 20))
            screen.blit(font_large.render(ip_input + cursor_char, True, LIGHT_GREEN), (popup.x + 20, popup.y + 70))

        # ---- CSAD Popup ----
        elif entering_csad:
            popup = pygame.Rect(WIDTH // 2 - 300, HEIGHT // 2 - 100, 600, 200)
            pygame.draw.rect(screen, GRAY, popup, border_radius=10)
            screen.blit(font.render("Enter CSAD (5 digits + letter) or leave empty", True, WHITE), (popup.x + 20, popup.y + 20))
            screen.blit(font_small.render(f"{len(csad_input)}/6", True, LIGHT_GRAY), (popup.right - 80, popup.y + 20))
            screen.blit(font_large.render(csad_input + cursor_char, True, LIGHT_GREEN), (popup.x + 20, popup.y + 100))

        # ---- Condition Popup ----
        elif entering_condition:
            popup = pygame.Rect(WIDTH // 2 - 350, HEIGHT // 2 - 120, 700, 240)
            pygame.draw.rect(screen, GRAY, popup, border_radius=10)
            screen.blit(font.render("Enter Condition → Enter to sync", True, WHITE), (popup.x + 20, popup.y + 20))
            screen.blit(font.render(condition_input + cursor_char, True, LIGHT_GREEN), (popup.x + 20, popup.y + 100))

        # ---- Status ----
        if sync_status:
            s_text = font.render(sync_status, True, sync_color)
            screen.blit(s_text, (WIDTH // 2 - s_text.get_width() // 2, HEIGHT // 2))

        prev_btn.draw()
        power_btn.draw()
        connect_btn.draw()
        sync_btn.draw()
        exit_btn.draw()

        pygame.display.flip()
        clock.tick(30)

# ---------------------------------------------------
# MAIN FLOW
# ---------------------------------------------------

current = 0

while True:
    if current == 0:
        r = camera_screen()
        current = 1 if r == "next" else 0

    elif current == 1:
        r = wifi_screen()
        current = 0 if r == "back" else 2

    elif current == 2:
        r = speaker_screen()
        current = 1 if r == "back" else 3

    elif current == 3:
        r = keyboard_screen()
        current = 2 if r == "back" else 4

    elif current == 4:
        r = battery_screen()
        current = 3 if r == "back" else 5

    elif current == 5:
        r = final_screen()
        current = 4
