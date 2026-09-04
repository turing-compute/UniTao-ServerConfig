# BRCTL Command
operate on linux bridges

### Description
use brctl.py to operate linux bridge utility to create/enable/disable/delete LinuxBridge

### Base on command:
 - brctl addbr {bridge}
 - brctl delbr {bridge}
 - brctl addif {bridge} {device}
 - brctl delif {bridged} {device}

### data schema
```jsonc
{
    "bridgeType": "[linuxBridge, ovsBridge]",   // specify bridge name so different commands will be used for operation
    "macAddress": "d6:60:50:0b:83:10",          // specify mac address for the bridge
    "interfaces": []                            // list of link to be add to this bridge
}
```

### net_bridge_reverse.py (generate data file from a live bridge)

Reverse direction of `net_bridge.py`: read an existing system bridge and produce its JSON
data file, so pre-existing bridges can be adopted into the data-driven model.

Run it manually, one bridge at a time — decide which bridge's data file to generate up
front, then pass its name via `--name`.

```bash
./src/runpy.sh src/network/bridge/net_bridge_reverse.py --name br0                       # print br0.json to stdout
./src/runpy.sh src/network/bridge/net_bridge_reverse.py --name br0 --type ovsBridge      # error out if br0 is detected as a different type
./src/runpy.sh src/network/bridge/net_bridge_reverse.py --name br0 --path data/br0.json   # write the data file to data/br0.json
```

The bridge type is auto-detected from `ovs-vsctl list-br` / `brctl show` (same commands
`net_bridge.py` relies on, installed by `kvm_install.sh`). If a bridge is not visible to
those commands, the tool errors out — the type is never guessed. The bridge name becomes
the JSON file name, matching the repo's file-based identity convention.

