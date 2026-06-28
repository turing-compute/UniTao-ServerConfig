#!/usr/bin/env python3

"""WireGuard-specific pre-commit cleanup for image preparation.

Removes WireGuard keys, generated configs, and agent runtime state
so the committed base image starts clean on each cloned VM.

Auto-detects: if /opt/unitao/wireguard_network.json exists, cleans up wg0.
Otherwise skips WireGuard cleanup.

Usage:
    python3 prep_image_for_commit.py [--force]
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


NETWORK_CONFIG = os.path.join(AGENT_DIR, "wireguard_network.json")
NETWORK_NAME = "wg0"


def main():
    parser = argparse.ArgumentParser(
        description="WireGuard pre-commit cleanup for image preparation")
    parser.add_argument("--force", action="store_true",
                        help="Actually remove files (without this flag, dry-run only)")
    args = parser.parse_args()

    dry_run = not args.force

    if dry_run:
        print("=== DRY RUN (use --force to actually remove files) ===\n")
    else:
        print("=== WireGuard pre-commit cleanup ===\n")

    # 1. WireGuard keys and generated config.
    # Only clean up if wireguard_network.json exists (agent was deployed).
    if os.path.isfile(NETWORK_CONFIG):
        wg_key_dir = os.path.join(WIREGUARD_DIR, NETWORK_NAME)
        wg_conf = os.path.join(WIREGUARD_DIR, f"{NETWORK_NAME}.conf")

        print(f"[1/2] WireGuard keys and config (network: {NETWORK_NAME}):")
        remove_path(wg_conf, dry_run)
        if os.path.isdir(wg_key_dir):
            for f in os.listdir(wg_key_dir):
                remove_path(os.path.join(wg_key_dir, f), dry_run)
            remove_path(wg_key_dir, dry_run)
        # Remove network config so each clone starts fresh
        # (agent will recreate with new keys, Orchestrator assigns fresh IP)
        remove_path(NETWORK_CONFIG, dry_run)
        inv_path = os.path.join(AGENT_DIR, "wireguard_network_inv.json")
        remove_path(inv_path, dry_run)
    else:
        print(f"[1/2] No {NETWORK_CONFIG} found, skipping WireGuard cleanup.")

    # 2. Agent runtime state (generated conf, lock files, etc.).
    # Keep wg_agent.conf (install config) and *.json — those are part of the image.
    print(f"\n[2/2] Agent runtime state:")
    if os.path.isdir(AGENT_DIR):
        for f in os.listdir(AGENT_DIR):
            if f == "wg_agent.conf":
                continue
            if f.endswith(".conf") or f.endswith(".state") or f.endswith(".lock"):
                remove_path(os.path.join(AGENT_DIR, f), dry_run)

    if dry_run:
        print("\n=== Dry run complete. Run with --force to apply. ===")
    else:
        print("\n=== WireGuard cleanup complete. ===")


if __name__ == "__main__":
    main()
