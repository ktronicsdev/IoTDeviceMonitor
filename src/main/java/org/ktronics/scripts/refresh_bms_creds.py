#!/usr/bin/env python3
"""Re-bootstrap the BMS refresh credentials from the running PACEEX app and push them to
GitHub secrets. LOCAL-ONLY (needs BlueStacks + frida-server + gh CLI auth).

Normally the cloud workflow self-renews the iotToken every run from the long-lived
refreshToken (~200 h) via /account/checkOrRefreshSession — see README_BMS.md §6. This helper
is the belt-and-suspenders top-up: run it ~weekly (Task Scheduler) so the refreshToken in the
secrets is always fresh, covering the case where it does not roll forward on its own.

It reads the credential straight from the app's memory
(IoTCredentialManageImpl.getInstance(ctx)) — no UI, no network, no native signature — then
updates BMS_IOT_REFRESH / BMS_IOT_IDENTITY / BMS_IOT_TOKEN.

    python refresh_bms_creds.py
    # ADB / BMS_DEVICE env vars override the BlueStacks adb path / device id.
"""
import json, os, re, subprocess, sys, threading, time

ADB = os.environ.get("ADB", r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe")
DEV = os.environ.get("BMS_DEVICE", "emulator-5554")
APP = "com.paicheng.bms"
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harvest_bms_cred.js")


def adb(*a):
    return subprocess.run([ADB, "-s", DEV, *a], capture_output=True, text=True)


def main():
    if not os.path.exists(HOOK):
        print("missing", HOOK); return 1
    # 1) frida-server up + port forwarded
    if not adb("shell", "su", "-c", "pidof frida-server").stdout.strip():
        adb("shell", "su", "-c", "nohup /data/local/tmp/frida-server >/dev/null 2>&1 &"); time.sleep(2)
    adb("forward", "tcp:27042", "tcp:27042")
    # 2) ensure the app is running (credential singleton initialised)
    adb("shell", "am", "start", "-n", f"{APP}/.MainActivity"); time.sleep(3)
    pid = adb("shell", "su", "-c", f"pidof {APP}").stdout.strip().split()
    if not pid:
        print("app not running"); return 1
    pid = pid[0]

    # 3) harvest via frida — the REPL never self-exits, so read until HARVEST then kill it
    proc = subprocess.Popen([sys.executable, "-m", "frida_tools.repl",
                             "-H", "127.0.0.1:27042", "-p", pid, "-l", HOOK],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    threading.Timer(25, proc.kill).start()
    cred = None
    for line in proc.stdout:
        m = re.search(r"HARVEST (\{.*\})", line)
        if m:
            cred = json.loads(m.group(1)); break
        if "HARVEST-ERR" in line:
            print(line.strip()); break
    proc.kill()
    if not cred:
        print("harvest failed"); return 1
    print("harvested id=%s refresh=%s… iot=%s…" % (cred["id"][:12], cred["refresh"][:8], cred["iot"][:8]))

    # 4) push to GitHub secrets
    rc = 0
    for name, val in [("BMS_IOT_REFRESH", cred["refresh"]),
                      ("BMS_IOT_IDENTITY", cred["id"]),
                      ("BMS_IOT_TOKEN", cred["iot"])]:
        r = subprocess.run(["gh", "secret", "set", name], input=val, capture_output=True, text=True)
        ok = r.returncode == 0
        rc |= 0 if ok else 1
        print(("set " if ok else "FAIL ") + name + (("  " + r.stderr.strip()[:80]) if not ok else ""))
    return rc


if __name__ == "__main__":
    sys.exit(main())
