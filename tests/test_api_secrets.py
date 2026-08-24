#!/usr/bin/env python3
"""A credential must not travel in argv (#14).

Everything after the program name sits in /proc/<pid>/cmdline, which every account on the
box can read for as long as the child lives — and `ps` prints it. These were live
credentials: the GOG login code, the Microsoft auth code, the Xbox device_code, the PSN
npsso, and the ENTIRE pasted Nintendo cookie jar (`args=["--cookie", str(raw)]`).
devices.py did the same to every device password: `["sshpass", "-p", dev["password"]]`,
in front of an rsync that can run for 3600s.

The fetcher scripts belong to another agent, so the fix cannot depend on them growing a
new flag: the secret is handed over on STDIN by a two-line shim that sets `sys.argv` in
the child process and then runs the script unchanged. A Python list is not in
/proc/cmdline. sshpass has read the password from $SSHPASS with `-e` since forever, and
/proc/<pid>/environ is readable only by the process owner.

Offline. The child here is a fixture script that reports its own argv AND its own
/proc/self/cmdline, so the assertion is made against the kernel's copy, not a promise.
"""
import json
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-api-secret-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import devices                                                 # noqa: E402
from server import app                                         # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


FIXTURE = os.path.join(DATA, "echo_argv.py")
with open(FIXTURE, "w", encoding="utf-8") as f:
    f.write(
        "import json, sys\n"
        "cmd = open('/proc/self/cmdline','rb').read().decode('utf-8','replace')\n"
        "print(json.dumps({'argv': sys.argv, 'cmdline': cmd}))\n"
        "sys.exit(0)\n")

SECRET = "npsso-4f2a-THIS-IS-THE-CREDENTIAL"


def main():
    print("credentials go over stdin and in the environment, never in argv")

    r = app._run_secret(FIXTURE, ["--npsso"], SECRET, timeout=30)
    check("the child ran", r.returncode == 0)
    got = json.loads(r.stdout.strip().splitlines()[-1])
    check("the child sees the script as argv[0]", got["argv"][0] == FIXTURE)
    check("the flag still arrives", got["argv"][1] == "--npsso")
    check("and so does the secret, as the last argument", got["argv"][2] == SECRET)
    check("but the secret is NOT in /proc/<pid>/cmdline",
          SECRET not in got["cmdline"])
    check("the fetcher name still is, so `ps` stays readable",
          "echo_argv.py" in got["cmdline"])

    # a secret with newlines (the Nintendo cookie jar is a pasted blob) survives
    blob = "a=1; b=2\nc=3; d=4"
    r = app._run_secret(FIXTURE, ["--cookie"], blob, timeout=30)
    got = json.loads(r.stdout.strip().splitlines()[-1])
    check("a multi-line paste arrives intact", got["argv"][2] == blob)
    check("and is not in the command line", "c=3" not in got["cmdline"])

    # _run_script's secret path (the Nintendo endpoint uses it)
    saved_sp = app._script_path
    app._script_path = lambda name: FIXTURE
    try:
        ok, err = app._run_script("nintendo_owned.py", args=["--cookie"],
                                  secret=SECRET, timeout=30)
        check("_run_script accepts a secret and the child exits clean", ok)
    finally:
        app._script_path = saved_sp

    # ---- the endpoints ------------------------------------------------------- #
    src = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    for fn, secret_var in (("gog_connect", "code"), ("psn_connect", "npsso"),
                           ("xbox_connect", "code"), ("xbox_device_poll", "code"),
                           ("nintendo_connect", "raw")):
        body = src.split("def %s(" % fn, 1)[1].split("\n@app.", 1)[0].split("\ndef ", 1)[0]
        check("%s hands its credential over stdin" % fn,
              "_run_secret(" in body or "secret=" in body)
        check("%s does not put it in argv" % fn,
              (", %s]" % secret_var) not in body
              and (", str(%s)]" % secret_var) not in body
              and ('"%s"' % secret_var) not in body.split("args=")[-1][:80])

    # ---- device passwords ---------------------------------------------------- #
    dev = {"id": 1, "auth": "password", "password": "s3cr3t-device-pw",
           "username": "deck", "host": "h", "port": 22}
    argv = devices._wrap_pw(dev, ["ssh", "host", "ls"])
    check("sshpass reads the password from the environment", argv[:2] == ["sshpass", "-e"])
    check("the password is not an argument", "s3cr3t-device-pw" not in argv)
    env = devices._pw_env(dev)
    check("it is in SSHPASS instead", env and env.get("SSHPASS") == "s3cr3t-device-pw")
    check("a key-auth device gets no wrapper and no env",
          devices._wrap_pw({"auth": "key"}, ["ssh"]) == ["ssh"]
          and devices._pw_env({"auth": "key"}) is None)

    seen = {}

    def rec(argv, timeout=120, env=None):
        seen["argv"], seen["env"] = argv, env

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    saved_run, saved_dev = devices._run, devices._device
    devices._run = rec
    devices._device = lambda did: dev
    try:
        devices._ssh(dev, "ls")
        check("_ssh passes the password through the environment",
              (seen.get("env") or {}).get("SSHPASS") == "s3cr3t-device-pw")
        check("and not in the command line",
              "s3cr3t-device-pw" not in " ".join(seen.get("argv") or []))
        seen.clear()
        devices._rsync("a", "b", dev)
        check("rsync too — the longest-lived child of the lot",
              (seen.get("env") or {}).get("SSHPASS") == "s3cr3t-device-pw"
              and "s3cr3t-device-pw" not in " ".join(seen.get("argv") or []))
    finally:
        devices._run, devices._device = saved_run, saved_dev

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
