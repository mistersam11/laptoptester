# laptoptester

Laptop hardware test flow (`laptop_tester.py`) plus a separate WiFi background manager (`wifi_manager.py`) and the receiving server (`server.exe`).

## How To

- Run `latop_tester.py` on laptop being tested (optional: create startup script to launch on boot).
- Run `server.exe` on Windows pc as long as testing is going on.
- Ensure `Laptop_Templates.xlsx` is in the ~/Documents/LaptopSync directory

IDK good luck. The tester runs for me on puppylinux and maybe I'll save a clonezilla image to this repository with all my personal data wiped.

## Puppy Linux / Clonezilla boot optimization

Use `scripts/optimize_puppy_image.sh` on a running Puppy install *or* against a mounted rootfs before capturing with Clonezilla.

### Fast path (recommended)

```bash
sudo ./scripts/optimize_puppy_image.sh --rootfs /
```

### Aggressive service trimming (if you do not use print/BT/Samba/Avahi)

```bash
sudo ./scripts/optimize_puppy_image.sh --rootfs / --aggressive
```

### What it changes

- Removes stale udev persistent NIC rules (`70-persistent-net.rules`) to reduce hardware-switch boot churn.
- Installs a non-blocking Puppy startup script at `/root/Startup/00-laptoptester-startup`.
- Optionally disables common non-essential init scripts in aggressive mode.
- Pre-compiles Python files in likely laptoptester locations.

After applying, reboot a few representative laptop models and verify WiFi + tester launch behavior, then capture the new golden image with Clonezilla.

## Editing the Clonezilla tar image (boot optimization)

If you have the split image files (`usbtesterimg.tar.gz.part-*`), combine them first:

```bash
cat usbtesterimg.tar.gz.part-* > usbtesterimg.tar.gz
```

Then run:

```bash
./scripts/optimize_clonezilla_tar.sh \
  --input-tar usbtesterimg.tar.gz \
  --output-tar usbtesterimg.optimized.tar.gz
```

This applies conservative boot-speed tweaks inside the FAT32 partition image and repacks everything into **one** Clonezilla-ready tarball.
