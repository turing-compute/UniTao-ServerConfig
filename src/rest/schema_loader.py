"""Load and apply the REST/data schema files under src/data/schema."""

import json
import os
import shutil

from shared.logger import Log

SCHEMA_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "schema")
)

logger = Log.get_logger("SchemaLoader")


def _read_json(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def load_rest_schemas() -> dict:
    """Return {resource_name: rest.json content} for every resource directory."""
    result = {}
    if not os.path.isdir(SCHEMA_ROOT):
        logger.warning("Schema root not found: %s", SCHEMA_ROOT)
        return result

    for entry in sorted(os.listdir(SCHEMA_ROOT)):
        resource_dir = os.path.join(SCHEMA_ROOT, entry)
        rest_path = os.path.join(resource_dir, "rest.json")
        if not os.path.isdir(resource_dir) or not os.path.isfile(rest_path):
            continue
        try:
            result[entry] = _read_json(rest_path)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Skip invalid schema [%s]: %s", rest_path, e)
    return result


def load_vm_schemas() -> tuple[dict, dict, dict]:
    """Return (rest, rest_map, data_map) for the vms resource."""
    vm_dir = os.path.join(SCHEMA_ROOT, "vms")
    paths = {
        "rest": os.path.join(vm_dir, "rest.json"),
        "rest_map": os.path.join(vm_dir, "rest_map.json"),
        "data_map": os.path.join(vm_dir, "data_map.json"),
    }
    try:
        return (
            _read_json(paths["rest"]),
            _read_json(paths["rest_map"]),
            _read_json(paths["data_map"]),
        )
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Invalid vms schema: {e}")


def check_vm_schema_consistency(rest: dict, rest_map: dict, data_map: dict) -> list:
    """Return a list of human-readable inconsistency warnings."""
    warnings = []
    allowed = set(rest.get("patch", {}).get("allowedFields", {}))
    mapped = set(rest_map.get("fields", {}))

    for field in sorted(allowed - mapped):
        warnings.append(f"PATCH field [{field}] has no rest_map entry")
    for field in sorted(mapped - allowed):
        warnings.append(f"rest_map field [{field}] is not in PATCH allowedFields")

    dependencies = data_map.get("dependencies", {})
    for field, mapping in rest_map.get("fields", {}).items():
        file_key = mapping.get("file")
        targets = {mapping.get("path")}
        derive = mapping.get("derive")
        if derive:
            targets.add(derive.get("path"))
        known = dependencies.get(file_key, {})
        for target in targets:
            if target not in known:
                warnings.append(
                    f"data_map has no artifact mapping for {file_key}.{target}")
    return warnings


def render_template(template: str, variables: dict) -> str:
    """Expand {name} placeholders with values from variables."""
    result = template
    for key, value in variables.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def artifact_paths_for_changes(changed_by_file: dict, data_map: dict,
                               variables: dict) -> list:
    """Resolve the generated artifacts affected by changed data fields.

    changed_by_file: {"vm": ["smp"], "network": ["bridgeName"]}
    """
    artifacts = data_map.get("artifacts", {})
    dependencies = data_map.get("dependencies", {})
    names = []

    for file_key, paths in changed_by_file.items():
        field_deps = dependencies.get(file_key, {})
        for path in paths:
            for name in field_deps.get(path, []):
                if name in names:
                    continue
                if name not in artifacts:
                    logger.warning(
                        "data_map references unknown artifact [%s]", name)
                    continue
                names.append(name)

    return [render_template(artifacts[name], variables) for name in names]


def remove_artifacts(paths: list, vm_path: str) -> tuple[list, list]:
    """Delete generated artifacts, restricted to the VM directory.

    Returns (removed_paths, warnings). Missing files are treated as success.
    """
    removed = []
    warnings = []
    root = os.path.realpath(vm_path)

    for path in paths:
        abs_path = os.path.abspath(path)
        real_path = os.path.realpath(abs_path)

        if not os.path.exists(real_path):
            continue

        try:
            if os.path.commonpath([root, real_path]) != root:
                warnings.append(
                    f"Refusing to delete path outside VM dir: {abs_path}")
                continue
        except ValueError:
            warnings.append(f"Refusing to delete path outside VM dir: {abs_path}")
            continue

        try:
            if os.path.isdir(real_path):
                shutil.rmtree(real_path)
            else:
                os.remove(real_path)
            removed.append(abs_path)
        except OSError as e:
            warnings.append(f"Failed to remove {abs_path}: {e}")

    return removed, warnings
