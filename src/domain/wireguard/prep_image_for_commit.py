#!/usr/bin/env python3

"""WireGuard-specific pre-commit cleanup for image preparation.

Removes WireGuard keys, generated configs, and agent runtime state
so the committed base image starts clean on each cloned VM.

Usage:
    python3 prep_image.py --network wg-mesh [--force]
"""

import argparse
import os
import shutil
import sys

WIREGUARD_DIR = "/etc/wireguard"
AGENT_DIR = "/opt/unitao"


def remove_path(path: str, dry_run: bool = False):
    if not os.path.exists(path):
        return
    if dry_run:
        print(f"  [dry-run] would remove: {path}")
        return
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"  removed dir:  {path}")
        else:
            os.remove(path)
            print(f"  removed file: {path}")
    except Exception as e:
        print(f"  WARNING: failed to remove {path}: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="WireGuard pre-commit cleanup for image preparation")
    parser.add_argument("--network", type=str, required=True,
                        help="WireGuard network name (e.g. wg-mesh)")
    parser.add_argument("--force", action="store_true",
                        help="Actually remove files (without this flag, dry-run only)")
    args = parser.parse_args()

    network = args.network
    dry_run = not args.force

    if dry_run:
        print("=== DRY RUN (use --force to actually remove files) ===\n")
    else:
        print("=== WireGuard pre-commit cleanup ===\n")

    # 1. WireGuard keys and generated config.
    wg_key_dir = os.path.join(WIREGUARD_DIR, network)
    wg_conf = os.path.join(WIREGUARD_DIR, f"{network}.conf")

    print(f"[1/2] WireGuard keys and config (network: {network}):")
    remove_path(wg_conf, dry_run)
    if os.path.isdir(wg_key_dir):
        for f in os.listdir(wg_key_dir):
            remove_path(os.path.join(wg_key_dir, f), dry_run)
        remove_path(wg_key_dir, dry_run)

    # 2. Agent runtime state (generated conf, lock files, etc.).
    # Keep *.json network configs — those are part of the image.
    print(f"\n[2/2] Agent runtime state:")
    if os.path.isdir(AGENT_DIR):
        for f in os.listdir(AGENT_DIR):
            if f.endswith(".conf") or f.endswith(".state") or f.endswith(".lock"):
                remove_path(os.path.join(AGENT_DIR, f), dry_run)

    if dry_run:
        print("\n=== Dry run complete. Run with --force to apply. ===")
    else:
        print("\n=== WireGuard cleanup complete. ===")


if __name__ == "__main__":
    main()
