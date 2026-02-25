#!/usr/bin/env python3
"""
LaptopSync Server - Run this on your Windows PC.
Receives laptop test data from test laptops and logs to Daily_Log.xlsx.

Requirements:
    pip install flask openpyxl

Folder structure (auto-created in your Documents folder):
    Documents/
    └── LaptopSync/
        ├── Laptop_Templates.xlsx   ← put your template here
        └── Daily_Log.xlsx          ← auto-created
"""

import os
import re
import sys
from datetime import datetime
from collections import Counter
from pathlib import Path

from flask import Flask, request, jsonify
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Alignment

# ---------------------------------------------------
# PATHS
# ---------------------------------------------------

DOCUMENTS_DIR = Path.home() / "Documents" / "LaptopSync"
TEMPLATE_FILE = DOCUMENTS_DIR / "Laptop_Templates.xlsx"
DAILY_FILE    = DOCUMENTS_DIR / "Daily_Log.xlsx"
PORT          = 5050

# ---------------------------------------------------
# INIT
# ---------------------------------------------------

app = Flask(__name__)

def setup_directory():
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE_FILE.exists():
        print(f"\n  ERROR: Template file not found.")
        print(f"  Please place 'Laptop_Templates.xlsx' in:")
        print(f"  {DOCUMENTS_DIR}\n")
        sys.exit(1)
    print(f"  Template : {TEMPLATE_FILE}")
    print(f"  Log file : {DAILY_FILE}")

def initialize_daily_log():
    """Create Daily_Log.xlsx from template headers if it doesn't exist."""
    if DAILY_FILE.exists():
        return

    template_wb = load_workbook(TEMPLATE_FILE)
    template_ws = template_wb.active

    new_wb = Workbook()
    new_ws = new_wb.active

    # Copy header row only
    for col_idx, cell in enumerate(template_ws[1], start=1):
        new_ws.cell(row=1, column=col_idx, value=cell.value)

    new_wb.save(DAILY_FILE)
    print(f"  Created new daily log: {DAILY_FILE}")

# ---------------------------------------------------
# DATA HELPERS
# ---------------------------------------------------

def parse_cpu_info(cpu_string):
    cpu_string    = cpu_string.strip()
    cpu_make_full = "Unknown"

    model_match = re.search(r"(Core\s*i[3579]|Celeron|Pentium|Ryzen\s?\d+)", cpu_string, re.IGNORECASE)
    if model_match:
        part = model_match.group(0).strip()
        if any(x in part for x in ["Core", "Celeron", "Pentium"]):
            cpu_make_full = f"Intel {part.title()}"
        else:
            cpu_make_full = part
    elif "Intel" in cpu_string:
        cpu_make_full = "Intel"
    elif "AMD" in cpu_string:
        cpu_make_full = "AMD"

    number_match     = re.search(
        r"(i[3579]-\d+\w*|[NJM]\d{2,5}|M\d+|[0-9]+[UHG]|Ryzen\s?\d+\s?\d+\w*)",
        cpu_string,
        re.IGNORECASE,
    )
    cpu_model_number = number_match.group(0) if number_match else "Unknown"
    cpu_model_number = re.sub(r"^(i[3579]-|Ryzen\s?\d\s?)", "", cpu_model_number).strip()

    freq_match    = re.search(r"@?\s*([\d\.]+GHz)", cpu_string)
    cpu_frequency = freq_match.group(1) if freq_match else "Unknown"

    return cpu_make_full, cpu_model_number, cpu_frequency


def parse_ram_info(ram_slots):
    sizes    = []
    ram_type = "Unknown"

    for slot in ram_slots:
        if not slot:
            continue
        size_match = re.search(r"(\d+)\s*GB", slot, re.IGNORECASE)
        if size_match:
            sizes.append(int(size_match.group(1)))
        type_match = re.search(r"(DDR\d)", slot, re.IGNORECASE)
        if type_match:
            ram_type = type_match.group(1).upper()

    if not sizes:
        return "Unknown", "0GB", "Unknown"

    size_counts = Counter(sizes)
    if len(set(sizes)) == 1:
        ram_config = f"{len(sizes)}x{sizes[0]}GB"
    else:
        ram_config = " + ".join(f"{qty}x{size}GB" for size, qty in size_counts.items())

    return ram_config, f"{sum(sizes)}GB", ram_type


def clean_cpu_make_full(cpu_full_string):
    s = cpu_full_string.strip()
    if "Intel" in s:
        m = re.search(r"(Core\s*\(TM\)\s*i[3579]|Core\s*i[3579]|Celeron|Pentium)", s, re.IGNORECASE)
        if m:
            return "Intel " + m.group(1).replace("(TM)", "").replace("(R)", "").strip()
        return "Intel"
    elif "AMD" in s:
        m = re.search(r"(AMD\s+Ryzen\s+\d)", s, re.IGNORECASE)
        return m.group(1) if m else "AMD"
    return s


def clean_cpu_model(cpu_model_number):
    m = re.search(r"(?:i[3579]-|Celeron\s|Pentium\s|Ryzen\s\d\s)?([A-Za-z0-9]+)", cpu_model_number, re.IGNORECASE)
    return m.group(1) if m else cpu_model_number


def clean_total_ram(ram_config):
    total = sum(int(qty) * int(size) for qty, size in re.findall(r"(\d+)x(\d+)GB", ram_config))
    return f"{total}GB"


def append_to_log(data):
    """Find matching template row and append to daily log."""
    template_wb = load_workbook(TEMPLATE_FILE)
    template_ws = template_wb.active

    daily_wb = load_workbook(DAILY_FILE)
    daily_ws = daily_wb.active

    model = data.get("model", "")

    # Find matching template row
    template_row = None
    for row in template_ws.iter_rows(min_row=2):
        template_model = str(row[5].value).strip()
        if model.lower() in template_model.lower():
            template_row = row
            break

    if not template_row:
        raise ValueError(f"Model '{model}' not found in template sheet")

    # Build new row from template
    new_row = [cell.value for cell in template_row]
    while len(new_row) < 42:
        new_row.append(None)

    # Parse and clean data
    cpu_string                                       = data.get("cpu_string", "")
    cpu_make_full, cpu_model_number_clean, cpu_freq  = parse_cpu_info(cpu_string)
    cpu_make_clean                                   = clean_cpu_make_full(cpu_string)
    cpu_model_clean                                  = clean_cpu_model(cpu_model_number_clean)

    ram_slots                                        = data.get("ram_slots", [])
    ram_config, _, ram_type                          = parse_ram_info(ram_slots)
    total_ram_clean                                  = clean_total_ram(ram_config)

    now      = datetime.now()
    date_str = f"{now.month}/{now.day:02d}/{now.year}"

    battery_health = str(data.get("battery_health", "")).strip()
    condition      = str(data.get("condition", "")).strip()
    condition_grade = str(data.get("condition_grade", "A-Grade (Like-New)")).strip()

    battery_health_lower = battery_health.lower()
    battery_unknown = (
        battery_health_lower in {"", "none", "n/a"}
        or "unknown" in battery_health_lower
        or "unavailable" in battery_health_lower
    )

    if battery_unknown:
        battery_prefix = "Battery dead."
    else:
        battery_prefix = f"Battery Health: {battery_health}."

    battery_text = f"{battery_prefix} {condition}".strip()

    csad_value = data.get("csad_value", "").strip()

    # Write to columns
    new_row[0]  = csad_value if csad_value else "*"   # A
    new_row[1]  = date_str                            # B
    new_row[7]  = data.get("serial", "")             # H
    new_row[10] = battery_text                        # K
    new_row[11] = condition_grade                     # L
    new_row[18] = cpu_model_clean                     # S
    new_row[27] = ram_config                          # AB
    new_row[28] = total_ram_clean                     # AC
    new_row[29] = ram_type                            # AD
    new_row[40] = cpu_freq                            # AO
    new_row[41] = cpu_make_clean                      # AP

    daily_ws.append(new_row)

    # Ensure long condition notes in column K wrap instead of overflowing.
    row_idx = daily_ws.max_row
    daily_ws.cell(row=row_idx, column=11).alignment = Alignment(wrap_text=True)

    daily_wb.save(DAILY_FILE)

# ---------------------------------------------------
# ROUTES
# ---------------------------------------------------

@app.route("/ping", methods=["GET"])
def ping():
    """Test connectivity — laptop uses this to verify server is reachable."""
    return jsonify({"status": "ok"}), 200


@app.route("/log", methods=["POST"])
def log_laptop():
    """Receive laptop data and append to Excel log."""
    data = request.get_json()

    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    required = ["model", "serial", "cpu_string", "ram_slots", "battery_health"]
    missing  = [f for f in required if f not in data]
    if missing:
        return jsonify({"status": "error", "message": f"Missing fields: {missing}"}), 400

    try:
        append_to_log(data)
        model = data.get("model", "unknown")
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Logged: {model} (CSAD: {data.get('csad_value', 'none')})")
        return jsonify({"status": "ok", "message": "Logged successfully"}), 200

    except ValueError as e:
        # Model not found in template
        return jsonify({"status": "error", "message": str(e)}), 404

    except Exception as e:
        print(f"  ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ---------------------------------------------------
# MAIN
# ---------------------------------------------------

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  LaptopSync Server")
    print("="*50)

    setup_directory()
    initialize_daily_log()

    print(f"\n  Server running on port {PORT}")
    print(f"  Laptops should point to this PC's IP address")
    print(f"  Press Ctrl+C to stop\n")
    print("="*50 + "\n")

    app.run(host="0.0.0.0", port=PORT, debug=False)
