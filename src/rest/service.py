import json
import os

from shared.utilities import Util

# Maps data_type to config key for the data definition JSON directory.
_DATA_DIR_KEY = {
    "vm":     "vmDataDir",
    "image":  "imageDataDir",
    "bridge": "bridgeDataDir",
}

DATA_TYPES = list(_DATA_DIR_KEY.keys())


def _get_config(app) -> dict:
    return app.config["CONFIG"]


def get_data_dir(app, data_type: str) -> str:
    """Return the absolute path to the data directory for a type."""
    key = _DATA_DIR_KEY.get(data_type, None)
    if key is None:
        raise ValueError(f"Unknown data type: {data_type}, expect one of {DATA_TYPES}")
    return _get_config(app)[key]


def get_image_file_dir(app) -> str:
    """Return the absolute path where disk image files are stored."""
    return _get_config(app)["imageFileDir"]


# ── VM paths (nested: {vmDataDir}/{name}/data/{name}.json) ──

def vm_dir(app, name: str) -> str:
    """Return the VM root directory: {vmDataDir}/{name}/"""
    return os.path.join(get_data_dir(app, "vm"), name)


def vm_data_dir(app, name: str) -> str:
    """Return the VM data directory: {vmDataDir}/{name}/data/"""
    return os.path.join(vm_dir(app, name), "data")


def vm_json_path(app, name: str) -> str:
    """Return the VM JSON definition file path: {vmDataDir}/{name}/data/vm-{name}.json"""
    return os.path.join(vm_data_dir(app, name), f"vm-{name}.json")


def list_entities(app, data_type: str) -> list:
    """List all entity names for a type."""
    if data_type == "vm":
        return _list_vms(app)
    return _list_flat(app, data_type)


def _list_vms(app) -> list:
    """List VM names: each subdirectory containing data/vm-{name}.json is a VM."""
    parent = get_data_dir(app, "vm")
    if not os.path.isdir(parent):
        return []
    names = []
    for entry in os.listdir(parent):
        entry_path = os.path.join(parent, entry)
        if not os.path.isdir(entry_path):
            continue
        vm_file = os.path.join(entry_path, "data", f"vm-{entry}.json")
        if os.path.isfile(vm_file):
            names.append(entry)
    return sorted(names)


def _list_flat(app, data_type: str) -> list:
    """List entity names from a flat directory of JSON files."""
    data_dir = get_data_dir(app, data_type)
    if not os.path.isdir(data_dir):
        return []
    names = []
    for f in os.listdir(data_dir):
        if f.endswith(".json"):
            names.append(f[:-5])
    return sorted(names)


def get_entity_path(app, data_type: str, name: str) -> str:
    """Get the full path to an entity's JSON definition file."""
    if data_type == "vm":
        return vm_json_path(app, name)
    return os.path.join(get_data_dir(app, data_type), f"{name}.json")


def read_entity_data(app, data_type: str, name: str) -> dict:
    """Read and return an entity's JSON data. Returns None if not found."""
    path = get_entity_path(app, data_type, name)
    if not os.path.exists(path):
        return None
    return Util.read_json_file(path)


def write_entity_data(app, data_type: str, name: str, data: dict):
    """Write JSON data for an entity, creating directories as needed."""
    path = get_entity_path(app, data_type, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


def delete_entity(app, data_type: str, name: str) -> bool:
    """Delete an entity's JSON file. Returns True if found, False if not."""
    path = get_entity_path(app, data_type, name)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
