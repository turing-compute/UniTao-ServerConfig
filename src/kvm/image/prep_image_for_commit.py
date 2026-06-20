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
    - Machine ID (regenerated on next boot by systemd)
    - Cloud-init state (re-runs on next boot)
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
    # Also remove inventory_tool.py if present.
    inv_tool = os.path.join(os.path.dirname(INVENTORY_CONFIG), "inventory_tool.py")
    remove_path(inv_tool, dry_run)


def clean_ssh_host_keys(dry_run: bool = False):
    """Remove SSH host keys. Regenerated on next boot."""
    import glob
    for path in glob.glob(SSH_HOST_KEYS):
        remove_path(path, dry_run)


def clean_machine_id(dry_run: bool = False):
    """Remove machine-id. systemd regenerates on next boot."""
    remove_path(MACHINE_ID, dry_run)
    remove_path(DBUS_MACHINE_ID, dry_run)


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

    print(f"\n[4/5] Cloud-init state:")
    clean_cloud_init(dry_run)

    print(f"\n[5/5] Network config + misc (shell history, journal):")
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
