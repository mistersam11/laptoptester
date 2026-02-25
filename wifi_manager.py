#!/usr/bin/env python3

import json
import os
import subprocess
import time

WIFI_SSID = "Engineering"
WIFI_PASSWORD = "Csad!123"
WIFI_STATUS_FILE = "/tmp/laptoptester_wifi_status.json"
CHECK_INTERVAL = 3.0
RETRY_INTERVAL = 10.0


def write_status(status, **extra):
    payload = {
        "status": status,
        "target_ssid": WIFI_SSID,
        "timestamp": time.time(),
    }
    payload.update(extra)

    tmp = WIFI_STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, WIFI_STATUS_FILE)


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
    except subprocess.CalledProcessError:
        return False


def get_wifi_info_wpa(interface, target_ssid):
    ip_result = subprocess.run(["ip", "-4", "addr", "show", interface], capture_output=True, text=True)
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


def main():
    last_connect_attempt = 0.0

    while True:
        interface = get_wifi_interface()
        if not interface:
            write_status("no_adapter")
            time.sleep(CHECK_INTERVAL)
            continue

        try:
            connected, ssid, signal, ip = get_wifi_info_wpa(interface, WIFI_SSID)
            if connected:
                write_status("connected", ssid=ssid, signal=signal, ip=ip)
            else:
                write_status("disconnected", ssid=WIFI_SSID)
                now = time.time()
                if now - last_connect_attempt >= RETRY_INTERVAL:
                    last_connect_attempt = now
                    write_status("connecting", ssid=WIFI_SSID)
                    ok = connect_wifi_wpa(interface, WIFI_SSID, WIFI_PASSWORD)
                    if not ok:
                        write_status("error", message="connect_failed", ssid=WIFI_SSID)
        except Exception as e:
            write_status("error", message=str(e))

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
