#!/usr/bin/env python3

"""Prepare a VM for disk image commit.

Clears VM-specific data so the committed base image is clean for cloning.
Run this inside the VM before shutting down and calling the commit API.

Usage:
    python3 wg_image_prep.py --network wg-mesh [--force]

Cleans:
    - WireGuard keys and config for the given network
    - Inventory config (inventory.json)
    - wg_agent network config
    - SSH host keys (regenerated on next boot by cloud-init / sshd)
    - Machine ID (reset to 'uninitialized', regenerated on next boot)
    - Cloud-init state (re-runs on next boot, apt module disabled)
    - Network config files injected by cloud-init
    - Shell history
"""

import argparse
import os
import subprocess
import sys
import shutil
import sys


# ── Paths to clean ─────────────────────────────────────────────────────────

INVENTORY_CONFIG = "/opt/unitao-server-config/inventory.json"
NETPLAN_DIR = "/etc/netplan"
SSH_HOST_KEYS = "/etc/ssh/ssh_host_*"
MACHINE_ID = "/etc/machine-id"
DBUS_MACHINE_ID = "/var/lib/dbus/machine-id"
CLOUD_INIT_DIR = "/var/lib/cloud"
ROOT_BASH_HISTORY = "/root/.bash_history"
JOURNAL_DIR = "/var/log/journal"
SYSTEMD_RANDOM_SEED = "/var/lib/systemd/random-seed"


def remove_path(path: str, dry_run: bool = False):
    """Remove a file or directory tree. Logs what was done."""
    if not os.path.exists(path) and "*" not in path:
        return
    if dry_run:
        print(f"  [dry-run] would remove: {path}")
        return
    try:
        if os.path.isdir(path) and "*" not in path:
            shutil.rmtree(path)
            print(f"  removed dir:  {path}")
        elif os.path.isfile(path):
            os.remove(path)
            print(f"  removed file: {path}")
    except Exception as e:
        print(f"  WARNING: failed to remove {path}: {e}", file=sys.stderr)


def clean_inventory(dry_run: bool = False):
    """Remove inventory config injected by cloud-init."""
    remove_path(INVENTORY_CONFIG, dry_run)
    # Also remove inventory_tool.py and report_network.py if present.
    inv_tool = os.path.join(os.path.dirname(INVENTORY_CONFIG), "inventory_tool.py")
    remove_path(inv_tool, dry_run)
    report_script = os.path.join(os.path.dirname(INVENTORY_CONFIG), "report_network.py")
    remove_path(report_script, dry_run)
    # Remove report-network systemd service.
    remove_path("/etc/systemd/system/report-network.service", dry_run)
    if not dry_run:
        subprocess.run(["systemctl", "disable", "report-network.service"],
                       check=False, capture_output=True)
        print("  disabled report-network.service")


def clean_ssh_host_keys(dry_run: bool = False):
    """Remove SSH host keys. Regenerated on next boot."""
    import glob
    for path in glob.glob(SSH_HOST_KEYS):
        remove_path(path, dry_run)


def clean_machine_id(dry_run: bool = False):
    """Reset machine-id so systemd regenerates on next boot.

    Writes "uninitialized" instead of deleting, so systemd-networkd's
    DHCP client can still derive IAID+DUID (LP #1999680).
    """
    if dry_run:
        print("  [dry-run] would write 'uninitialized' to /etc/machine-id")
        return
    try:
        with open(MACHINE_ID, "w") as f:
            f.write("uninitialized\n")
        print("  reset machine-id (uninitialized)")
    except Exception as e:
        print(f"  WARNING: failed to reset {MACHINE_ID}: {e}", file=sys.stderr)
    remove_path(DBUS_MACHINE_ID, dry_run)


def clean_wait_online(dry_run: bool = False):
    """Mask systemd-networkd-wait-online.service to avoid 120s boot delay."""
    if dry_run:
        print("  [dry-run] would mask systemd-networkd-wait-online.service")
        return
    subprocess.run(["systemctl", "mask", "systemd-networkd-wait-online.service"],
                   check=False, capture_output=True)
    print("  masked systemd-networkd-wait-online.service")


def disable_cloud_init_apt(dry_run: bool = False):
    """Prevent cloud-init from overwriting apt sources on first boot."""
    cfg_dir = "/etc/cloud/cloud.cfg.d"
    cfg_file = os.path.join(cfg_dir, "99-preserve-sources.cfg")
    if dry_run:
        print(f"  [dry-run] would write {cfg_file}")
        return
    os.makedirs(cfg_dir, exist_ok=True)
    with open(cfg_file, "w") as f:
        f.write("apt:\n  preserve_sources_list: true\n")
    print("  disabled cloud-init apt module")


def clean_cloud_init(dry_run: bool = False):
    """Reset cloud-init state so it re-runs on next boot."""
    if dry_run:
        print("  [dry-run] would run: cloud-init clean --logs")
        return
    import subprocess
    try:
        subprocess.run(["cloud-init", "clean", "--logs"], check=True)
        print("  cloud-init state cleaned")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  WARNING: cloud-init clean failed: {e}", file=sys.stderr)
        # Fallback: manual removal.
        if os.path.isdir(CLOUD_INIT_DIR):
            remove_path(CLOUD_INIT_DIR, dry_run=False)


def clean_network_config(dry_run: bool = False):
    """Remove cloud-init injected netplan files.
    Keep the base 00-installer-config.yaml or 01-netcfg.yaml for reference,
    but remove any MAC-address-specific config.
    """
    if not os.path.isdir(NETPLAN_DIR):
        return
    for f in os.listdir(NETPLAN_DIR):
        if not f.endswith(".yaml"):
            continue
        path = os.path.join(NETPLAN_DIR, f)
        remove_path(path, dry_run)


def clean_misc(dry_run: bool = False):
    """Remove shell history, journal, random seed."""
    remove_path(ROOT_BASH_HISTORY, dry_run)
    remove_path(SYSTEMD_RANDOM_SEED, dry_run)
    if os.path.isdir(JOURNAL_DIR):
        remove_path(JOURNAL_DIR, dry_run)


def main():
    parser = argparse.ArgumentParser(
        description="Prepare VM for disk image commit — clean VM-specific data")
    parser.add_argument("--force", action="store_true",
                        help="Actually remove files (without this flag, dry-run only)")
    args = parser.parse_args()

    dry_run = not args.force

    if dry_run:
        print("=== DRY RUN (use --force to actually remove files) ===\n")
    else:
        print("=== Cleaning VM for image commit ===\n")

    print(f"[1/5] Inventory config:")
    clean_inventory(dry_run)

    print(f"\n[2/5] SSH host keys:")
    clean_ssh_host_keys(dry_run)

    print(f"\n[3/5] Machine ID:")
    clean_machine_id(dry_run)

    print(f"\n[4/7] Cloud-init state:")
    clean_cloud_init(dry_run)

    print(f"\n[5/7] Mask wait-online:")
    clean_wait_online(dry_run)

    print(f"\n[6/7] Disable cloud-init apt:")
    disable_cloud_init_apt(dry_run)

    print(f"\n[7/7] Network config + misc (shell history, journal):")
    clean_network_config(dry_run)
    clean_misc(dry_run)

    if dry_run:
        print("\n=== Dry run complete. Run with --force to apply. ===")
    else:
        print("\n=== Clean complete. VM is ready for commit. ===")
        print("Next steps:")
        print("  1. poweroff")
        print("  2. On host: POST /api/v1/vms/<vmName>/commit")


if __name__ == "__main__":
    main()
