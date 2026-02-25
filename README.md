# laptoptester

Laptop hardware test flow (`laptop_tester.py`) plus a separate WiFi background manager (`wifi_manager.py`).

## Run

Start WiFi manager in the background at startup:

```bash
python3 wifi_manager.py &
```

Then run the tester UI:

```bash
python3 laptop_tester.py
```

The tester no longer performs WiFi connect/reconnect operations in its UI loop.  
It only reads `/tmp/laptoptester_wifi_status.json` and shows status on the final screen.
