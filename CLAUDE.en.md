# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

UniTao-ServerConfig is a data-driven server configuration automation tool. It separates **configuration data** (JSON files defining desired state) from **execution logic** (Python scripts that translate data into system commands). The philosophy is closer to Terraform (desired-state reconciliation) than Ansible (procedural task lists).

The primary domain is **KVM virtualization** — creating VMs, disk images, and network bridges on a Linux KVM host.

## How to Run

```bash
# Install Python dependencies (wget) to src/extlib/
./src/req_install.sh

# Install system packages for KVM (qemu-kvm, libvirt, genisoimage, openvswitch, etc.)
./src/kvm_install.sh

# Run any component — runpy.sh sets PYTHONPATH=src/ then executes python3 with your args
./src/runpy.sh src/kvm/vm/kvm_vm.py --path src/kvm/vm/example/vm.json
./src/runpy.sh src/kvm/image/kvm_image.py --path <path-to-disk.json>
./src/runpy.sh src/network/bridge/net_bridge.py --path <path-to-bridge.json>

# Generate a random MAC address
./src/runpy.sh src/network/bridge/generate_mac.py
```

There are **no tests, no linting configuration, and no build step** in this repository.

## Architecture

### Core Pattern: JSON Data → Python → System Commands

Every component follows the same pattern:
1. Accept a `--path` argument pointing to a **JSON data file** that describes desired state.
2. The JSON filename (without `.json`) becomes the **entity name** (VM name, image name, bridge name) via `Util.file_data_name()`.
3. Validate the JSON structure and values.
4. Translate the validated data into shell commands (`virsh`, `virt-install`, `qemu-img`, `brctl`, `ip`, `genisoimage`, etc.) executed via `Util.run_command()`.

### Shared Library (`src/shared/`)

- **`utilities.py`** — `Util` class with static methods: `read_json_file()`, `run_command()` (subprocess wrapper that raises `SystemError` on non-zero exit), `abs_path()`, `file_data_name()` (extracts entity name from file path), `write_file()`, `compare_dict()`, `is_int_str()`, `parse_mac_address()`.
- **`logger.py`** — `Log.get_logger(name, log_file, level)` returns a configured `logging.Logger` with console output plus optional file output. Clears duplicate handlers on re-invocation.

### KVM VM Management (`src/kvm/vm/kvm_vm.py`)

The most substantial module. Contains two classes:

- **`KvmVm`** — Main VM lifecycle manager. Orchestrates:
  1. Validates the VM JSON definition (vCPU, RAM, disks, networks, OS type/variant, desired state, Cloud-Init settings).
  2. Resolves relative paths — supports `{vmPath}` placeholder that gets replaced with the actual VM directory path at validation time.
  3. `create_vm()`: Generates a `virt-install --print-xml` command, writes the XML definition file, then calls `virsh create`.
  4. `sync_vm_state()`: Reconciles running/stopped state via `virsh destroy` / `virsh start`.
  5. `delete_vm()`: Calls `virsh destroy` if the VM exists.
  6. Cloud-Init support: Generates `user-data`, `meta-data`, and `network-config` YAML files, packages them into a `cidata` ISO via `genisoimage`, and attaches as a cdrom. Includes the VM JSON and network JSONs inside the ISO for self-documentation.
  7. `hostCPU` keyword: When present and `true`, adds `--cpu host` to virt-install for CPU passthrough mode.

- **`KvmNetwork`** — Network interface configuration for VMs. Supports two interface types:
  - `bridge`: Connects to a Linux bridge or OVS bridge. OVS bridges add `virtualport_type=openvswitch`.
  - `macvtap`: Direct interface tap in bridge mode.
  - Generates Cloud-Init `network-config` v2 YAML with static IP, gateway, optional route metric, and DNS servers.

### KVM Image Management (`src/kvm/image/kvm_image.py`)

- **`KvmImage`** — Creates disk images from two sources:
  - `remote`: Downloads via `wget` from a `downloadLink`.
  - `local`: Creates with `qemu-img create` (supports `qcow2` and `raw`/`img` formats), optionally backed by a base image (`-b`/`-F` flags), optionally with a specified size.
- Images are idempotent — if the file already exists at `imagePath`, creation is skipped.

### Network Bridge (`src/network/bridge/`)

- **`net_bridge.py`** — Data model and validation for network bridges. Supports `linuxBridge` and `ovsBridge` types with associated interface lists. **Note:** This module only validates the JSON; it does not currently execute bridge creation commands (unlike `kvm_vm.py`).
- **`generate_mac.py`** — Prints a random locally-administered MAC address (OUI prefix `0E:`).

### Archive (`src/Archive/`)

Legacy / earlier iteration code:
- **`shared/entity.py`** — An Entity framework (`Entity`, `EntityOp`, `DataProvider`) for a current-vs-desired state reconciliation loop. This was the intended architecture but is not used by the current active modules.
- **`network/brctl/brctl.py`** — Full bridge lifecycle operations using the Entity framework (create, delete, add/remove interfaces, set MAC address).
- **`network/veth/veth.py`** — Virtual Ethernet pair creation/deletion.

### Key Design Decisions

- **No config management framework** — Raw Python scripts call system binaries directly. No Ansible, Salt, or Terraform provider.
- **File-based entity identity** — The JSON filename IS the entity identity. Renaming the file changes the entity name.
- **`{vmPath}` placeholder** — VM JSON can reference paths relative to the VM directory using `{vmPath}` syntax, resolved at validation time.
- **No Python packaging** — Scripts are run directly. `runpy.sh` sets `PYTHONPATH` so imports work from `src/`. External libs are installed to `src/extlib/` via `pip install --target`.
- **`Util.run_command()` always raises on non-zero exit** — there is no error recovery; failures propagate as `SystemError`.
