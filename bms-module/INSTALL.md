# KT BMS Monitor — On-Site Installation Guide

**For the installer at the site.** You do **not** need to install any software.
Everything is done from a phone and a Chrome browser.

Trial site: **gayan-imh (Imbulgoda 3 kW)** · Battery: **JK BMS (Bluetooth)**

What you need:

| Item | Notes |
|---|---|
| ESP32-WROOM-32 board | Supplied. Has WiFi + Bluetooth |
| USB cable (data, not charge-only) | For the one-time flash |
| Laptop with **Chrome** or **Edge** | Firefox and Safari do **not** work for flashing |
| The `.bin` file | Sent to you privately by Gayan — do not share it, it holds a security token |
| Phone with **nRF Connect** app | Free, on Play Store / App Store — used once, in Step 1 |
| A USB phone charger + socket near the battery | The ESP32 stays powered permanently |

---

## Step 1 — Find the battery's Bluetooth address (do this FIRST)

The ESP32 must be told exactly which battery to listen to, and that address has
to be built into the file **before** it is sent to you. So this step happens
*before* Step 2.

1. Stand next to the battery. Open **nRF Connect** on your phone → **Scan**.
2. Look for a device named like `JK-B2A24S`, `JK_BMS`, or similar. Confirm it is the battery by checking the same name shows in the JK BMS phone app.
3. Note the address under the name — 6 pairs like `C8:47:8C:12:34:56`.
4. **Send that address plus a photo of the BMS label to Gayan.**

> ⚠️ Wrong address = the ESP32 powers on and looks fine, but never reads the
> battery. Double-check the characters (0 vs O, 8 vs B).

---

## Step 2 — Flash the ESP32 from your browser

1. Plug the ESP32 into the laptop with the USB cable.
2. Open **https://web.esphome.io** in Chrome or Edge.
3. Click **Connect** → pick the COM port that appears (usually
   "Silicon Labs CP210x" or "CH340"). If no port appears, the cable is
   charge-only or the USB driver is missing — see Troubleshooting.
4. Click **Install** → **Choose file** → select the `.bin` Gayan sent you.
5. Confirm and wait ~2 minutes. Do not unplug during the install.

---

## Step 3 — Put it on the site WiFi

Right after flashing, the page offers **"Connect to Wi-Fi"**:

1. Pick the customer's WiFi network and enter the password.
2. Wait for "Connected" and **write down the IP address** it shows
   (e.g. `192.168.1.45`).

**If that prompt doesn't appear**, use the backup route:

1. Unplug from the laptop, power the ESP32 from the USB charger near the battery.
2. On your phone, join the WiFi network **"KT-BMS Setup"** (password from Gayan).
3. A setup page opens automatically. Choose the customer's WiFi, enter the
   password, save. The hotspot disappears once it connects.

> The WiFi password is stored on the device only. It is never sent to us.

---

## Step 4 — Mount and power it

1. Place the ESP32 **within a few metres of the battery** — Bluetooth range is
   the main limit. Same cupboard or the wall beside it is ideal.
2. Power it from the USB charger. It must stay on permanently.
3. Keep it away from the inverter's hot surfaces and out of direct rain/sun.

---

## Step 5 — Check it works (5 minutes)

1. On a phone/laptop **on the same WiFi**, open `http://<the IP from Step 3>`.
2. You should see live values updating every 10 seconds:
   **Total Voltage, Current, Power, State of Charge, Temperature 1,
   Min/Max/Delta Cell Voltage**, and Charging / Discharging / Balancing.
3. **Compare with the JK BMS phone app** — voltage and SOC should match closely.
   If the page loads but every value is blank or "unknown", the Bluetooth
   address is wrong or the board is too far from the battery.
4. Tell Gayan it's up and send him the IP. He confirms from Sweden that the
   reading arrived in the cloud (within 15 minutes).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No COM port in the browser | Charge-only cable, or missing driver. Try another cable first; then install the CP210x or CH340 driver. |
| "Connect" button greyed out | Wrong browser. Must be Chrome or Edge on a laptop — not a phone, not Firefox/Safari. |
| Web page won't load at the IP | Phone is on mobile data or a different WiFi/guest network. Join the same WiFi as the ESP32. |
| Page loads, values blank | Wrong Bluetooth address, or too far from the battery. Move it closer; re-check Step 1. |
| Values freeze after a while | Usually WiFi or power dropout. Unplug 10 seconds and re-plug. If it repeats, tell Gayan. |
| "KT-BMS Setup" hotspot keeps coming back | Wrong WiFi password, or the router is 5 GHz-only. The ESP32 needs a **2.4 GHz** network. |

Anything unclear — photograph the screen and send it. Do not open the battery
or change any BMS setting; this device only reads.
