#!/usr/bin/env python3
"""
prepare_image.py — Automate WireGuard image preparation on a KVM host.

Usage:
    python3 prepare_image.py --key <host_key> --vm <vm_name> --script <deploy.sh>

Example:
    python3 prepare_image.py --key ~/.ssh/host_key --vm wireguard01 --script ./wireguard/deploy.sh

Steps:
    1. Get baseImagePath from existing VM data
    2. Delete existing VM
    3. Create image-prep VM (DHCP + HostKey + shareInventoryData)
    4. Wait for cloud-init, add host key, run deploy.sh
    5. Print next steps (prep, shutdown, commit)
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
DEPLOY_USER = "ubuntu"
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10"]


def api(path: str, method: str = "GET", data: dict = None) -> dict:
    """Call the REST API, return parsed JSON."""
    url = f"{HOST}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    body = None
    if data is not None:
        body = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, data=body, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())
    except Exception as e:
        print(f"  [ERROR] API call failed: {e}", file=sys.stderr)
        sys.exit(1)


def ssh(*args) -> tuple:
    """Run ssh, return (returncode, stdout)."""
    cmd = ["ssh"] + SSH_OPTS + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description="WireGuard image preparation")
    parser.add_argument("--key", required=True, help="Path to host private key")
    parser.add_argument("--vm", required=True, help="Source VM name")
    parser.add_argument("--script", required=True, help="Path to deploy.sh")
    args = parser.parse_args()

    key_path = os.path.expanduser(args.key)
    vm_name = args.vm
    deploy_script = os.path.abspath(args.script)

    if not os.path.isfile(key_path):
        print(f"ERROR: host key not found: {key_path}")
        sys.exit(1)
    if not os.path.isfile(deploy_script):
        print(f"ERROR: deploy script not found: {deploy_script}")
        sys.exit(1)

    print("=== WireGuard Image Preparation ===")
    print(f"  Host key:  {key_path}")
    print(f"  Source VM: {vm_name}")
    print(f"  Deploy:    {deploy_script}")
    print()

    # ── 1. Get VM data ───────────────────────────────────────────────────

    print("[1/5] Fetching VM data ...")
    vm_data = api(f"/api/v1/vms/{vm_name}")
    if not vm_data.get("success"):
        print(f"ERROR: VM '{vm_name}' not found")
        sys.exit(1)

    vm_info = vm_data["data"]
    req = vm_info.get("request.json", {})
    os_image = req.get("osImage", "")
    cpu = req.get("cpu", 2)
    ram = req.get("ramInGB", 2)
    bridge = req.get("bridge", "ovs-br0")

    if not os_image:
        print("ERROR: could not determine osImage from VM data")
        sys.exit(1)

    prep_vm = f"{vm_name}-prep"
    print(f"  Image:  {os_image}")
    print(f"  CPU: {cpu}, RAM: {ram}GB, Bridge: {bridge}")
    print(f"  Prep VM: {prep_vm}")

    # ── 2. Delete existing VM ────────────────────────────────────────────

    print()
    print(f"[2/5] Deleting VM '{vm_name}' ...")
    api(f"/api/v1/vms/{vm_name}", method="DELETE")
    print("  Deleted.")

    # ── 3. Create prep VM ────────────────────────────────────────────────

    print()
    print(f"[3/5] Creating prep VM '{prep_vm}' ...")
    req_body = {
        "id": prep_vm,
        "cpu": cpu,
        "ramInGB": ram,
        "vmHostName": prep_vm,
        "osImage": os_image,
        "osVariant": "ubuntu24.04",
        "bridge": bridge,
        "diskSizeGB": 20,
        "useDHCP4": True,
        "authType": "HostKey",
        "shareInventoryData": True,
        "prepareDomainImage": True,
    }
    print(f"  Request: {json.dumps(req_body, indent=4)}")
    api("/api/v1/vms", method="POST", data=req_body)

    time.sleep(3)
    api(f"/api/v1/vms/{prep_vm}/start", method="POST")
    print(f"  VM '{prep_vm}' created and started.")

    # ── 4. Wait for cloud-init, get IP ───────────────────────────────────

    print()
    print("[4/5] Waiting for VM to be ready ...")
    vm_ip = ""
    for i in range(1, 31):
        time.sleep(10)
        try:
            info = api(f"/api/v1/vms/{prep_vm}/inventory/network-info.json")
            if info.get("success"):
                c = info["data"]["content"]
                if isinstance(c, dict) and "data" in c:
                    c = c["data"]
                ifaces = c.get("interfaces", [])
                if ifaces:
                    vm_ip = ifaces[0].get("ip", "")
                    if vm_ip:
                        print(f"  VM ready. IP: {vm_ip} ({i * 10}s)")
                        break
        except Exception:
            pass
        print(".", end="", flush=True)

    if not vm_ip:
        print()
        print("ERROR: timed out waiting for VM to get IP")
        sys.exit(1)

    # ── 5. Add key and deploy ────────────────────────────────────────────

    print()
    print("[5/5] Starting ssh-agent and deploying ...")

    # Start ssh-agent and add key (deploy.sh needs the key in the agent)
    agent_out = subprocess.run(
        ["ssh-agent", "-s"], capture_output=True, text=True, check=True
    ).stdout
    for line in agent_out.strip().split("\n"):
        # Format: "SSH_AUTH_SOCK=/tmp/...; export SSH_AUTH_SOCK;"
        if "=" in line and "export" in line:
            kv = line.split(";")[0].strip()
            k, v = kv.split("=", 1)
            os.environ[k] = v
    subprocess.run(["ssh-add", key_path], check=True)
    print("  Key added to ssh-agent.")

    deploy_dir = os.path.dirname(deploy_script)
    deploy_name = os.path.basename(deploy_script)

    print(f"  Running: cd {deploy_dir} && ./{deploy_name} {vm_ip} {DEPLOY_USER}")
    subprocess.run(
        [f"./{deploy_name}", vm_ip, DEPLOY_USER],
        cwd=deploy_dir,
        check=True,
    )

    print()
    print("=== Done ===")
    print(f"  Prep VM:  {prep_vm} ({vm_ip})")
    print("  Next: SSH in, verify agent, then run:")
    print("    sudo python3 /opt/unitao/domain/wireguard/prep_image_for_commit.py --force")
    print("    sudo python3 /opt/unitao-server-config/prep_image_for_commit.py --force")
    print("    sudo shutdown -h now")
    print("  Then on host:")
    print(f"    curl -X POST {HOST}/api/v1/vms/{prep_vm}/stop")
    print(f"    curl -X POST {HOST}/api/v1/vms/{prep_vm}/commit -H 'Content-Type: application/json' -d '{{}}'")


if __name__ == "__main__":
    main()
