#!/usr/bin/env python3
"""
commit_image.py — Commit a prepared VM image on a KVM host.

Usage:
    python3 commit_image.py --domain <name> --vm <vm_name>

Example:
    python3 commit_image.py --domain wireguard --vm wireguard01-prep

Steps:
    1. Get VM IP from inventory
    2. SSH: run domain prep_image_for_commit.py --force
    3. SSH: run unitao-server-config prep_image_for_commit.py --force
    4. REST API: stop VM, wait for shutOff
    5. REST API: commit, delete VM
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

HOST = "http://localhost:5000"
SSH_OPTS = ["-t", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10"]
SSH_USER = "ubuntu"
SSH_KEY = None  # set from --key argument

DOMAIN_PREP_PATH = "/opt/unitao/domain/{domain}/prep_image_for_commit.py"
VM_PREP_PATH = "/opt/unitao-server-config/prep_image_for_commit.py"


def api(path: str, method: str = "GET", data: dict = None) -> dict:
    url = f"{HOST}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    body = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(req, data=body, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
    except Exception as e:
        print(f"  [ERROR] API call failed: {e}", file=sys.stderr)
        sys.exit(1)


def ssh(host: str, *args, retries: int = 3) -> tuple:
    """SSH with retry. Returns (returncode, stdout, stderr)."""
    cmd = ["ssh"] + SSH_OPTS
    if SSH_KEY:
        cmd += ["-i", SSH_KEY]
    cmd += [f"{SSH_USER}@{host}"] + list(args)
    for attempt in range(1, retries + 1):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return r.returncode, r.stdout, r.stderr
        if attempt < retries:
            print(f"  SSH failed (rc={r.returncode}), retry {attempt}/{retries} ...")
            time.sleep(5)
    return r.returncode, r.stdout, r.stderr


def get_vm_ip(name: str) -> str:
    info = api(f"/api/v1/vms/{name}/inventory/network-info.json")
    if not info.get("success"):
        print(f"ERROR: cannot get network info for VM '{name}'")
        sys.exit(1)
    c = info["data"]["content"]
    if isinstance(c, dict) and "data" in c:
        c = c["data"]
    for iface in c.get("interfaces", []):
        ip = iface.get("ip", "")
        if ip:
            return ip
    print(f"ERROR: no IP found for VM '{name}'")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Commit a prepared VM image")
    parser.add_argument("--domain", required=True, help="Domain name (e.g. wireguard)")
    parser.add_argument("--vm", required=True, help="VM name to commit")
    parser.add_argument("--key", default=None, help="Path to SSH private key")
    parser.add_argument("--user", default="ubuntu", help="SSH user (default: ubuntu)")
    args = parser.parse_args()

    global SSH_KEY, SSH_USER
    SSH_KEY = args.key
    SSH_USER = args.user
    domain = args.domain
    vm_name = args.vm

    domain_prep = DOMAIN_PREP_PATH.format(domain=domain)

    print("=== Commit Image ===")
    print(f"  Domain:    {domain}")
    print(f"  VM:        {vm_name}")
    print(f"  Prep:      {domain_prep}")
    print()

    # ── 1. Get VM IP ─────────────────────────────────────────────────────

    print("[1/5] Getting VM IP ...")
    vm_ip = get_vm_ip(vm_name)
    print(f"  IP: {vm_ip}")

    # ── 2. Run domain prep ───────────────────────────────────────────────

    print()
    print(f"[2/5] Running domain prep ({domain}) ...")
    rc, out, err = ssh(vm_ip, "sudo", "python3", domain_prep, "--force")
    print(out if out else "  done.")
    if err:
        print(f"  stderr: {err.strip()}", file=sys.stderr)
    if rc != 0:
        print(f"  WARNING: domain prep exited with code {rc}", file=sys.stderr)

    # ── 3. Run VM-level prep ─────────────────────────────────────────────

    print()
    print("[3/5] Running VM prep ...")
    rc, out, err = ssh(vm_ip, "sudo", "python3", VM_PREP_PATH, "--force")
    print(out if out else "  done.")
    if err:
        print(f"  stderr: {err.strip()}", file=sys.stderr)
    if rc != 0:
        print(f"  WARNING: VM prep exited with code {rc}", file=sys.stderr)

    # ── 4. Stop VM via REST API ──────────────────────────────────────────

    print()
    print("[4/5] Stopping VM ...")
    api(f"/api/v1/vms/{vm_name}/stop", method="POST")

    # Wait for virsh state to go to shutOff / notExists
    for i in range(1, 31):
        time.sleep(5)
        vm_data = api(f"/api/v1/vms/{vm_name}")
        state = vm_data.get("data", {}).get("virshState", "")
        if state in ("notExists", "shutOff"):
            print(f"  VM stopped. ({i * 5}s)")
            break
        print(".", end="", flush=True)

    # ── 5. Commit and delete ─────────────────────────────────────────────

    print("  Committing image ...")
    result = api(f"/api/v1/vms/{vm_name}/commit", method="POST", data={})
    msg = result.get("data", {}).get("message", str(result))
    print(f"  {msg}")

    print(f"  Deleting VM '{vm_name}' ...")
    api(f"/api/v1/vms/{vm_name}", method="DELETE")
    print("  Deleted.")

    print()
    print("=== Done ===")
    print(f"  Image committed. VM '{vm_name}' removed.")


if __name__ == "__main__":
    main()
