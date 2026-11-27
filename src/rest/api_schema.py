"""REST API schema — self-documenting endpoint metadata."""

from extlib.flask import Blueprint, jsonify

from rest.schema_loader import load_rest_schemas

schema_bp = Blueprint("schema", __name__)

_API_SCHEMA = {
    "service": "UniTao KVM Host",
    "version": "1.0",
    "basePath": "/api/v1",
    "schemaUrl": "/api/v1/schema",
    "resources": {
        "vms": {
            "description": "Virtual machine lifecycle management",
            "endpoints": {
                "GET /api/v1/vms": "List all VMs (managed + virsh-only)",
                "POST /api/v1/vms": "Create or update a VM. Body: id*, cpu*, ramInGB*, vmHostName* (only when useCloudInit=true), osImage* (unless installIso), osVariant*, bridge*; optional: installIso (kvm_iso name mounted as installer cdrom; without osImage the system disk is a standalone blank disk and diskSizeGB is required, with osImage the disk is an overlay of that image so the install can be committed back; useCloudInit defaults to false and true is rejected), useCloudInit (default true), ipv4, gateway4, diskSizeGB (defaults to the osImage sizeInGB; smaller than it is rejected), authType, customerPWD, customerKeys, shareInventoryData, prepareDomainImage. With useCloudInit=false, vmHostName/ipv4/gateway4/authType/customerPWD/customerKeys/shareInventoryData/prepareDomainImage are rejected",
                "GET /api/v1/vms/<name>": "Get VM details (virsh state, disk/net JSONs, inventory with timestamps). Unmanaged VMs return minimal info.",
                "PATCH /api/v1/vms/<name>": "Partially update a stopped VM. Allowed fields: cpu, ramInGB, bridge. See /api/v1/schema for the full PATCH schema.",
                "DELETE /api/v1/vms/<name>": "Delete VM (managed: destroy+remove dir; unmanaged: virsh destroy+undefine)",
                "POST /api/v1/vms/<name>/start": "Start VM (set vmState=running). data.warnings lists passthrough devices skipped by the start-time check and ignored malformed usbDevices/blockDevices entries",
                "POST /api/v1/vms/<name>/stop": "Stop VM (set vmState=stopped)",
                "POST /api/v1/vms/<name>/eject-iso": "Detach the install ISO (removes installIso from the VM JSON). VM must be stopped. Idempotent.",
                "POST /api/v1/vms/<name>/commit": "Commit qcow2 disk changes to backing image. Body: disk (int|string, optional). VM must be stopped+destroyed.",
                "GET /api/v1/vms/<name>/console": "Web VNC console page (noVNC). Open in a browser; the VM must be running.",
                "POST /api/v1/vms/<name>/console": "Start a one-shot websockify for a console page. Returns port (websockify, in consolePortMin-consolePortMax) and vncPort. The process proxies one WebSocket connection and exits when it closes, or after connectTimeoutSec if nobody connects. VM stop/delete closes it.",
                "GET /api/v1/vms/<name>/usb": "List the VM's USB passthrough devices (usbDevices) with host presence and live attachment",
                "POST /api/v1/vms/<name>/usb": "Pass a host USB device through, from the next start. Body: device (id from GET /api/v1/devices?type=usb) or vendorId+productId (hex), description (optional). VM must be stopped (no hot-plug)",
                "DELETE /api/v1/vms/<name>/usb/<vendorId>:<productId>": "Remove a USB passthrough device. VM must be stopped",
                "GET /api/v1/vms/<name>/block": "List the VM's whole-disk passthrough devices (blockDevices) with host presence and live attachment",
                "POST /api/v1/vms/<name>/block": "Pass a whole host disk through (guest sees /dev/vdX), from the next start. Body: device (id from GET /api/v1/devices?type=block) or path (/dev/disk/by-id/<name>), description (optional). VM must be stopped (no hot-plug)",
                "DELETE /api/v1/vms/<name>/block/<by-id name>": "Remove a passthrough disk. VM must be stopped",
                "GET /api/v1/vms/<name>/inventory": "List inventory file names",
                "GET /api/v1/vms/<name>/inventory/<f>": "Get inventory file with content and timestamp",
                "POST /api/v1/vms/<name>/inventory": "Post to inventory. Body.name → {name}.json (overwrite); no name → {timestamp}.json",
                "PATCH /api/v1/vms/<name>/inventory/<file>": "Patch an inventory file (JSON deep-merge). Only updates specified fields.",
            },
        },
        "images": {
            "description": "Disk image management",
            "endpoints": {
                "GET /api/v1/images": "List all images",
                "POST /api/v1/images/<name>": "Create/download image (async)",
                "GET /api/v1/images/<name>": "Get image details (downloadState)",
                "DELETE /api/v1/images/<name>": "Delete image and disk file",
            },
        },
        "kvm_iso": {
            "description": "ISO installation media (read-only, mounted as cdrom; never used as a disk)",
            "endpoints": {
                "GET /api/v1/kvm_iso": "List ISOs (managed + unmanaged .iso files in isoFileDir)",
                "GET /api/v1/kvm_iso/<name>": "Get ISO details (isoSource, downloadState, sizeInBytes, sha256)",
                "POST /api/v1/kvm_iso/<name>": "Download ISO from URL (async). Body: downloadLink*, sha256 (optional, verified after download)",
                "PUT /api/v1/kvm_iso/<name>": "Upload ISO as raw body (Content-Type: application/octet-stream, Content-Length required). Query: sha256 (optional, verified)",
                "DELETE /api/v1/kvm_iso/<name>": "Delete ISO definition and file (409 while transferring or while a VM still references it via installIso)",
            },
        },
        "devices": {
            "description": "Host devices that can be passed through to a VM (read-only discovery)",
            "endpoints": {
                "GET /api/v1/devices": "List host USB devices and whole disks with attachable/reason, attachedTo (libvirt domain) and configuredOn (managed VM JSONs). Query: type (usb|block), attachable=true (only attachable ones). errors lists discovery failures (block, attached, claims)",
            },
        },
        "bridges": {
            "description": "Network bridge management",
            "endpoints": {
                "GET /api/v1/bridges": "List bridges",
                "POST /api/v1/bridges/<name>": "Create/update bridge",
                "GET /api/v1/bridges/<name>": "Get bridge details",
                "DELETE /api/v1/bridges/<name>": "Delete bridge",
            },
        },
        "utils": {
            "endpoints": {
                "GET /api/v1/utils/health": "Health check",
                "GET /api/v1/utils/mac": "Generate random MAC address",
            },
        },
        "schema": {
            "endpoints": {
                "GET /api/v1/schema": "Aggregate all resource rest.json schemas",
            },
        },
    },
    "common": {
        "success": {"success": True, "data": "{...}"},
        "error": {"success": False, "error": {"code": "ERROR_CODE", "message": "..."}},
        "errorCodes": {
            "BAD_REQUEST": "400 — invalid input",
            "VALIDATION_ERROR": "400 — field validation failed",
            "EMPTY_PATCH": "400 — PATCH body has no fields",
            "INVALID_PATCH_FIELD": "400 — PATCH field is not in the allowed list",
            "NOT_FOUND": "404 — resource not found",
            "CONFLICT": "409 — resource exists or is busy (e.g. ISO already ready or still transferring)",
            "VM_RUNNING": "409 — VM must be stopped (PATCH, passthrough device changes)",
            "INVALID_VM_DATA": "409 — VM JSON field has the wrong type (e.g. usbDevices is not a list); fix the VM JSON",
            "DEVICE_NOT_FOUND": "404 — host device not found",
            "DEVICE_UNAVAILABLE": "409 — device cannot be attached (in use by host, attached to or configured on another VM, or state unknown)",
            "AMBIGUOUS_DEVICE": "409 — several host USB devices share vendor:product; passthrough cannot tell them apart",
            "NO_STABLE_PATH": "409 — disk has no usable /dev/disk/by-id link to record",
            "VM_NOT_EXISTS": "409 — VM is marked notExists",
            "VM_NOT_RUNNING": "409 — VM is not running (e.g. console needs a running VM)",
            "CONSOLE_UNAVAILABLE": "503 — websockify/noVNC not installed or no free console port",
            "SYSTEM_COMMAND_FAILED": "500 — host command failed",
            "INTERNAL_ERROR": "500 — unexpected error",
            "NOT_IMPLEMENTED": "501 — operation not implemented (e.g. multi-network PATCH)",
        },
    },
}


@schema_bp.route("")
@schema_bp.route("/")
def schema_index():
    return jsonify({"success": True, "data": _API_SCHEMA})


@schema_bp.route("/schema")
def schema_all():
    """Return every resource schema (src/data/schema/*/rest.json)."""
    return jsonify({"success": True, "data": load_rest_schemas()})
