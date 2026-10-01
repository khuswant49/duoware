"""A stand-in for `adb` in the adb-reverse runner tests (never touches a real device).

Environment:
  FAKE_ADB_DEVICES  comma list of serial:state, e.g. "A1:device,B2:unauthorized" (default: none)
  FAKE_ADB_LOG      file to which every call's arguments are appended, one line per call
  FAKE_ADB_HANG     "reverse" or "devices": that command sleeps far past any timeout
  FAKE_ADB_FAIL     "reverse": that command exits with status 1
"""

import os
import sys
import time

args = sys.argv[1:]
log = os.environ.get("FAKE_ADB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(" ".join(args) + "\n")
command = next((a for a in args if a in ("devices", "reverse")), "")
if os.environ.get("FAKE_ADB_HANG") == command:
    time.sleep(60)
if os.environ.get("FAKE_ADB_FAIL") == command:
    print("error: simulated failure", file=sys.stderr)
    sys.exit(1)
if command == "devices":
    print("List of devices attached")
    for item in filter(None, os.environ.get("FAKE_ADB_DEVICES", "").split(",")):
        serial, state = item.split(":")
        print(f"{serial}\t{state}")
    print()
elif command == "reverse":
    print(args[-1].split(":")[1])                               # adb prints the port it bound
