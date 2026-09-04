#!/usr/bin/env python3

#########################################################################################
# Reverse Network Bridge utilities
#
# The reverse direction of NetBridge: instead of consuming a bridge JSON data file to
# build system state, read an existing live system bridge and generate its JSON data
# file. This "imports" a bridge that already exists on a host into the data-driven model
# (like a Terraform import), producing the same shape the REST API auto-creates for
# unmanaged bridges (see rest/api_bridge.py).
#
# This tool is meant to be run manually, one bridge at a time: decide up front which
# bridge's data file to generate, then pass its name via --name.
#
# Usage:
#   ./src/runpy.sh src/network/bridge/net_bridge_reverse.py --name br0
#   ./src/runpy.sh src/network/bridge/net_bridge_reverse.py --name br0 --type ovsBridge   # error out if br0 is actually a linux bridge
#   ./src/runpy.sh src/network/bridge/net_bridge_reverse.py --name br0 --path data/br0.json
#
# Data schema emitted (same as net_bridge.py consumes / README.md documents):
#   {
#       "bridgeType": "[linuxBridge, ovsBridge]",
#       "interfaces": ["eth0", ...],
#       "macAddress": "d6:60:50:0b:83:10",   // optional — omitted when not resolvable
#   }
# The entity name is the JSON file name (bridge name), matching the file-based identity
# convention used across the repo.
#########################################################################################

import argparse
import json
import os
import sys

from network.bridge.net_bridge import NetBridge


class ReverseNetwork:
    @staticmethod
    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description="Generate a bridge JSON data file from a live system bridge "
                        "(reverse of NetBridge)")
        parser.add_argument("--name", type=str, required=True,
                           help="bridge name to reverse, e.g. br0")
        parser.add_argument("--type", type=str,
                           choices=NetBridge.Keyword.BridgeTypes.list(),
                           help="expected bridge type; errors out when it does not match "
                                "the type auto-detected from the system")
        parser.add_argument("--path", type=str, default=None,
                           help="output JSON data file path for the bridge, e.g. "
                                "/opt/kvm/network/br0.json "
                                "(default: print JSON to stdout)")
        return parser.parse_args()

    @staticmethod
    def resolve_target(name: str, bridge_type: str) -> dict:
        """Look up one live system bridge by name.

        Returns a dict shaped like NetBridge.discover_system_bridges():
            {name, bridgeType, interfaces, macAddress (optional)}
        Raises ValueError when the bridge is not visible to the system commands, so the
        type is never guessed.
        """
        discovered = NetBridge.discover_system_bridges(None)
        by_name = {br["name"]: br for br in discovered}

        if name not in by_name:
            available = ", ".join(sorted(by_name)) or "(none)"
            raise ValueError(
                f"Bridge [{name}] not found on the system (queried via "
                f"ovs-vsctl/brctl). Discovered bridges: [{available}].")

        br = by_name[name]
        if bridge_type and br["bridgeType"] != bridge_type:
            raise ValueError(
                f"Bridge [{name}] is type [{br['bridgeType']}] on the system, "
                f"but --type [{bridge_type}] was given")
        return br

    @staticmethod
    def to_data(br: dict) -> dict:
        """Project a discovered bridge onto the data file schema.

        The bridge name is encoded by the file name, not stored as a field, matching
        rest/service.write_entity_data() and rest/api_bridge._auto_create_bridge_json().
        """
        data = {
            NetBridge.Keyword.BridgeType: br["bridgeType"],
            NetBridge.Keyword.Interfaces: br.get("interfaces", []),
        }
        mac = br.get("macAddress")
        if mac:
            data[NetBridge.Keyword.MacAddress] = mac
        return data


def main():
    args = ReverseNetwork.parse_args()

    if args.path and not args.path.endswith(".json"):
        print(f"error: --path should point to a .json data file, got [{args.path}]",
              file=sys.stderr)
        sys.exit(1)

    try:
        br = ReverseNetwork.resolve_target(args.name, args.type)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    data = ReverseNetwork.to_data(br)
    payload = json.dumps(data, indent=4)
    if args.path:
        parent = os.path.dirname(args.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.path, "w") as fp:
            fp.write(payload + "\n")
        print(f"wrote bridge [{br['name']}] -> {args.path}", file=sys.stderr)
    else:
        # stdout carries exactly one JSON document, so it stays pipe-able.
        print(payload)


if __name__ == "__main__":
    main()
