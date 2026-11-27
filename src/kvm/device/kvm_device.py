#!/usr/bin/env python3

#########################################################################################
# Kvm Host Device Discovery
#
# List host devices that can be attached to a VM:
#   - usb:   USB devices (hubs excluded), for USB passthrough (<hostdev type='usb'>)
#   - block: whole disks, for block-device passthrough (<disk type='block'>)
#
# Every device carries `attachable` and, when false, `reason`: a device mounted on
# the host, holding LVM/RAID/crypt, or already attached to a VM must not be handed
# to another VM. Read-only: nothing is attached or changed here.
#########################################################################################

import argparse
import json
import logging
import os
import re
import xml.etree.ElementTree as ET

from shared.utilities import Util
from shared.logger import Log


class KvmDevice:
    class Keyword:
        class Types:
            USB = "usb"
            Block = "block"

            @staticmethod
            def list():
                return [KvmDevice.Keyword.Types.USB, KvmDevice.Keyword.Types.Block]

    SysUsbDir = "/sys/bus/usb/devices"
    SysBlockDir = "/sys/block"
    SysClassBlockDir = "/sys/class/block"
    DevDir = "/dev"
    DiskByIdDir = "/dev/disk/by-id"

    # bDeviceClass / bInterfaceClass codes (USB-IF)
    UsbClassHub = "09"
    UsbClassNames = {
        "01": "audio",
        "02": "communications",
        "03": "hid",
        "05": "physical",
        "06": "image",
        "07": "printer",
        "08": "massStorage",
        "09": "hub",
        "0a": "cdcData",
        "0b": "smartCard",
        "0d": "contentSecurity",
        "0e": "video",
        "0f": "healthcare",
        "10": "audioVideo",
        "dc": "diagnostic",
        "e0": "wireless",
        "ef": "misc",
        "fe": "applicationSpecific",
        "ff": "vendorSpecific",
    }

    # USB device sysfs name: <bus>-<port>[.<port>...], e.g. 2-1, 1-4.2
    _UsbDevName = re.compile(r"^\d+-[\d.]+$")
    # Disks that are never real hardware a VM should get.
    _SkipBlockPrefixes = ("loop", "zram", "ram", "nbd", "sr", "fd", "dm-", "md")
    # by-id link prefixes in order of preference (stable, human-readable first).
    _ByIdPreference = ("usb-", "ata-", "nvme-", "scsi-")
    # lsblk child types that mean the host is using the disk.
    _HostUseTypes = ("lvm", "crypt", "raid", "dmraid", "mpath", "bcache")
    # lsblk fstypes that mean the host (or a stacked device) owns the disk.
    _HostUseFsTypes = ("LVM2_member", "linux_raid_member", "crypto_LUKS", "zfs_member", "bcache")

    @staticmethod
    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(description="List host devices that can be attached to a VM")
        parser.add_argument("--type", type=str, choices=KvmDevice.Keyword.Types.list(),
                            help="Only list this device type (default: all)")
        return parser.parse_args()

    # ───────────────────────────── public API ─────────────────────────────

    @staticmethod
    def discover(device_type: str = None, logger: logging.Logger = None) -> dict:
        """Return {"usb": [...], "block": [...]} (only the requested type when given).

        When lsblk fails the block list is empty and result["errors"]["block"] says
        why; USB devices that carry disks are then not attachable, since their host
        mounts cannot be checked.
        """
        if device_type is not None and device_type not in KvmDevice.Keyword.Types.list():
            raise ValueError(f"Invalid device type [{device_type}], expect {KvmDevice.Keyword.Types.list()}")

        attached = KvmDevice.attached_devices(logger)
        usb_devices = KvmDevice.discover_usb()
        errors = {}
        try:
            tree = KvmDevice._lsblk(logger)
        except SystemError as e:
            tree = None
            errors[KvmDevice.Keyword.Types.Block] = str(e)
        block_devices = KvmDevice.discover_block(logger, tree) if tree is not None else []
        KvmDevice._resolve_state(usb_devices, block_devices, attached,
                                 block_error=errors.get(KvmDevice.Keyword.Types.Block),
                                 other_usage=KvmDevice._other_block_usage(tree or []))

        result = {}
        if device_type in (None, KvmDevice.Keyword.Types.USB):
            result[KvmDevice.Keyword.Types.USB] = usb_devices
        if device_type in (None, KvmDevice.Keyword.Types.Block):
            result[KvmDevice.Keyword.Types.Block] = block_devices
        if errors:
            result["errors"] = errors
        return result

    @staticmethod
    def discover_usb() -> list:
        """USB devices from sysfs; hubs (incl. root hubs) and interfaces are skipped."""
        devices = []
        if not os.path.isdir(KvmDevice.SysUsbDir):
            return devices
        block_map = KvmDevice._usb_block_map()
        for name in sorted(os.listdir(KvmDevice.SysUsbDir)):
            if not KvmDevice._UsbDevName.match(name):
                continue
            dev_dir = os.path.join(KvmDevice.SysUsbDir, name)
            device_class = KvmDevice._read_sys(dev_dir, "bDeviceClass")
            if device_class == KvmDevice.UsbClassHub:
                continue
            vendor_id = KvmDevice._read_sys(dev_dir, "idVendor")
            product_id = KvmDevice._read_sys(dev_dir, "idProduct")
            if vendor_id is None or product_id is None:
                continue
            classes = KvmDevice._usb_interface_classes(dev_dir)
            if classes == [KvmDevice.UsbClassNames[KvmDevice.UsbClassHub]]:
                continue
            busnum = KvmDevice._read_sys(dev_dir, "busnum")
            devnum = KvmDevice._read_sys(dev_dir, "devnum")
            devices.append({
                "id": name,
                "vendorId": vendor_id,
                "productId": product_id,
                "bus": int(busnum) if busnum and busnum.isdigit() else None,
                "device": int(devnum) if devnum and devnum.isdigit() else None,
                "manufacturer": KvmDevice._read_sys(dev_dir, "manufacturer"),
                "product": KvmDevice._read_sys(dev_dir, "product"),
                "serial": KvmDevice._read_sys(dev_dir, "serial"),
                "speedMbps": KvmDevice._to_number(KvmDevice._read_sys(dev_dir, "speed")),
                "classes": classes,
                "blockDevices": sorted(block_map.get(name, [])),
            })
        return devices

    @staticmethod
    def discover_block(logger: logging.Logger = None, tree: list = None) -> list:
        """Whole disks from lsblk, with partitions/children and host mountpoints.

        Raises SystemError when lsblk is missing or fails: an empty list would
        read as "no disks" rather than "unknown". `tree` reuses an lsblk result.
        """
        if tree is None:
            tree = KvmDevice._lsblk(logger)
        mounted_btrfs = set()
        KvmDevice._mounted_btrfs_uuids(tree, mounted_btrfs)
        by_id = KvmDevice._by_id_map()
        usb_of = KvmDevice._block_usb_parent()
        devices = []
        for node in tree:
            name = node.get("name")
            if not name or node.get("type") != "disk":
                continue
            if name.startswith(KvmDevice._SkipBlockPrefixes):
                continue
            mountpoints = []
            host_uses = []
            KvmDevice._collect_usage(node, mountpoints, host_uses, mounted_btrfs)
            host_uses.extend(KvmDevice._holders(name, [c.get("name") for c in node.get("children", []) or []]))
            path = os.path.join(KvmDevice.DevDir, name)
            devices.append({
                "id": name,
                "path": path,
                "byIdPath": KvmDevice._pick_by_id(by_id.get(os.path.realpath(path), [])),
                "sizeBytes": KvmDevice._to_int(node.get("size")),
                "model": (node.get("model") or "").strip() or None,
                "serial": (node.get("serial") or "").strip() or None,
                "transport": node.get("tran"),
                "removable": KvmDevice._to_bool(node.get("rm")),
                "usbDevice": usb_of.get(name),
                "partitions": [
                    {
                        "name": child.get("name"),
                        "type": child.get("type"),
                        "fsType": child.get("fstype"),
                        "sizeBytes": KvmDevice._to_int(child.get("size")),
                        "mountpoints": KvmDevice._node_mountpoints(child),
                    }
                    for child in node.get("children", []) or []
                ],
                "mountpoints": mountpoints,
                "hostUse": sorted(set(host_uses)),
            })
        return devices

    @staticmethod
    def attached_devices(logger: logging.Logger = None) -> dict:
        """Devices already attached to libvirt domains (running or defined).

        Returns {"usb": [{vendorId, productId, bus, device, vm}], "block": {realpath: vm}}.
        Returns empty maps when virsh is unavailable.
        """
        attached = {"usb": [], "block": {}}
        try:
            domains = [d for d in Util.run_command("virsh list --all --name").stdout_lines if d.strip()]
        except Exception as e:
            if logger:
                logger.warning(f"Cannot list libvirt domains, attachment state unknown: {e}")
            return attached
        for vm in domains:
            try:
                xml_text = "\n".join(Util.run_command(f"virsh dumpxml {vm}").stdout_lines)
                root = ET.fromstring(xml_text)
            except Exception as e:
                if logger:
                    logger.warning(f"Cannot read definition of VM [{vm}]: {e}")
                continue
            for hostdev in root.findall("./devices/hostdev[@type='usb']"):
                source = hostdev.find("source")
                if source is None:
                    continue
                vendor = source.find("vendor")
                product = source.find("product")
                address = source.find("address")
                attached["usb"].append({
                    "vendorId": KvmDevice._norm_hex(vendor.get("id")) if vendor is not None else None,
                    "productId": KvmDevice._norm_hex(product.get("id")) if product is not None else None,
                    "bus": KvmDevice._to_int(address.get("bus")) if address is not None else None,
                    "device": KvmDevice._to_int(address.get("device")) if address is not None else None,
                    "vm": vm,
                })
            for disk in root.findall("./devices/disk[@type='block']"):
                source = disk.find("source")
                if source is None or not source.get("dev"):
                    continue
                real = os.path.realpath(source.get("dev"))
                attached["block"][real] = vm
                # A partition passed through takes its whole disk too.
                parent = KvmDevice._partition_parent(os.path.basename(real))
                if parent is not None:
                    attached["block"].setdefault(os.path.join(KvmDevice.DevDir, parent), vm)
        return attached

    @staticmethod
    def _partition_parent(name: str):
        """Kernel name of the disk holding partition `name` (sdb1 → sdb), else None."""
        entry = os.path.join(KvmDevice.SysClassBlockDir, name)
        if not os.path.exists(os.path.join(entry, "partition")):
            return None
        return os.path.basename(os.path.dirname(os.path.realpath(entry)))

    # ───────────────────────────── state resolution ─────────────────────────────

    @staticmethod
    def _resolve_state(usb_devices: list, block_devices: list, attached: dict, block_error: str = None,
                       other_usage: dict = None):
        """Fill attachedTo / attachable / reason on both lists, cross-linking USB disks.

        other_usage: {name: reason} host use of block devices left out of the disk
        list (e.g. a USB optical drive's sr0), still checked for their USB parent.
        """
        other_usage = other_usage or {}
        block_by_name = {b["id"]: b for b in block_devices}

        for usb in usb_devices:
            usb["attachedTo"] = KvmDevice._match_usb_attachment(usb, attached["usb"])
        usb_by_id = {u["id"]: u for u in usb_devices}

        for blk in block_devices:
            vm = attached["block"].get(os.path.realpath(blk["path"]))
            if vm is None and blk["usbDevice"] in usb_by_id:
                vm = usb_by_id[blk["usbDevice"]]["attachedTo"]
            blk["attachedTo"] = vm
            reason = None
            if vm is not None:
                reason = f"attached to VM [{vm}]"
            elif blk["mountpoints"]:
                reason = f"mounted on host: {', '.join(blk['mountpoints'])}"
            elif blk["hostUse"]:
                reason = f"in use by host: {', '.join(blk['hostUse'])}"
            blk["attachable"] = reason is None
            blk["reason"] = reason

        for usb in usb_devices:
            reason = None
            if usb["attachedTo"] is None:
                # A USB disk passed through as a block device is still taken.
                for name in usb["blockDevices"]:
                    blk = block_by_name.get(name)
                    if blk is not None and blk["attachedTo"] is not None:
                        usb["attachedTo"] = blk["attachedTo"]
                        break
            if usb["attachedTo"] is not None:
                reason = f"attached to VM [{usb['attachedTo']}]"
            elif block_error is not None and usb["blockDevices"]:
                reason = f"cannot check host use of {', '.join(usb['blockDevices'])}: lsblk failed"
            else:
                for name in usb["blockDevices"]:
                    blk = block_by_name.get(name)
                    if blk is not None and not blk["attachable"]:
                        reason = f"{name} {blk['reason']}"
                        break
                    if blk is None and other_usage.get(name):
                        reason = f"{name} {other_usage[name]}"
                        break
            usb["attachable"] = reason is None
            usb["reason"] = reason

    @staticmethod
    def _match_usb_attachment(usb: dict, attached_usb: list):
        for item in attached_usb:
            if item["vendorId"] is not None and item["vendorId"] != usb["vendorId"]:
                continue
            if item["productId"] is not None and item["productId"] != usb["productId"]:
                continue
            if item["bus"] is not None and item["bus"] != usb["bus"]:
                continue
            if item["device"] is not None and item["device"] != usb["device"]:
                continue
            if item["vendorId"] is None and item["bus"] is None:
                continue
            return item["vm"]
        return None

    # ───────────────────────────── sysfs / lsblk helpers ─────────────────────────────

    @staticmethod
    def _read_sys(dev_dir: str, attr: str):
        try:
            with open(os.path.join(dev_dir, attr), "r") as fp:
                value = fp.read().strip()
            return value or None
        except OSError:
            return None

    @staticmethod
    def _usb_interface_classes(dev_dir: str) -> list:
        """Names of the interface classes of a USB device (e.g. ["massStorage"])."""
        classes = []
        base = os.path.basename(dev_dir)
        try:
            entries = sorted(os.listdir(dev_dir))
        except OSError:
            return classes
        for entry in entries:
            if not entry.startswith(base + ":"):
                continue
            code = KvmDevice._read_sys(os.path.join(dev_dir, entry), "bInterfaceClass")
            if code is None:
                continue
            name = KvmDevice.UsbClassNames.get(code.lower(), code.lower())
            if name not in classes:
                classes.append(name)
        return classes

    @staticmethod
    def _block_usb_parent() -> dict:
        """{block name: USB device sysfs name} for disks that sit on a USB device."""
        result = {}
        if not os.path.isdir(KvmDevice.SysBlockDir):
            return result
        for name in os.listdir(KvmDevice.SysBlockDir):
            real = os.path.realpath(os.path.join(KvmDevice.SysBlockDir, name, "device"))
            if "/usb" not in real:
                continue
            usb_name = None
            for part in real.split("/"):
                if KvmDevice._UsbDevName.match(part):
                    usb_name = part  # the deepest one is the device itself
            if usb_name is not None:
                result[name] = usb_name
        return result

    @staticmethod
    def _usb_block_map() -> dict:
        """{USB device sysfs name: [block names]}."""
        result = {}
        for blk, usb in KvmDevice._block_usb_parent().items():
            result.setdefault(usb, []).append(blk)
        return result

    @staticmethod
    def _lsblk(logger: logging.Logger = None) -> list:
        """lsblk tree; raises SystemError (after a warning) when lsblk is missing or fails.

        No PATH column (util-linux >= 2.33 only): the path is <DevDir>/<NAME>.
        """
        columns = "NAME,TYPE,SIZE,MODEL,SERIAL,TRAN,RM,FSTYPE,UUID"
        errors = []
        # MOUNTPOINTS (util-linux >= 2.37) lists every mount; fall back to MOUNTPOINT.
        for mount_col in ("MOUNTPOINTS", "MOUNTPOINT"):
            try:
                result = Util.run_command(f"lsblk -J -b -o {columns},{mount_col}")
                data = json.loads("\n".join(result.stdout_lines))
                return data.get("blockdevices", []) or []
            except (SystemError, OSError, ValueError) as e:
                errors.append(str(e))
        if logger:
            logger.warning(f"lsblk failed, block devices unknown: {errors[-1]}")
        raise SystemError(f"lsblk failed: {errors[-1]}")

    @staticmethod
    def _node_mountpoints(node: dict) -> list:
        points = node.get("mountpoints")
        if points is None:
            points = [node.get("mountpoint")]
        return [p for p in points if p]

    @staticmethod
    def _collect_usage(node: dict, mountpoints: list, host_uses: list, mounted_btrfs: set = frozenset()):
        points = KvmDevice._node_mountpoints(node)
        for point in points:
            if point not in mountpoints:
                mountpoints.append(point)
        node_type = node.get("type") or ""
        if node_type.startswith(KvmDevice._HostUseTypes):
            host_uses.append(node_type)
        fstype = node.get("fstype") or ""
        if fstype in KvmDevice._HostUseFsTypes:
            host_uses.append(fstype)
        # Multi-device btrfs shows its mountpoint on one member only.
        if fstype == "btrfs" and not points and node.get("uuid") in mounted_btrfs:
            host_uses.append("btrfs (member of a mounted filesystem)")
        for child in node.get("children", []) or []:
            KvmDevice._collect_usage(child, mountpoints, host_uses, mounted_btrfs)

    @staticmethod
    def _other_block_usage(tree: list) -> dict:
        """{name: reason} for top-level lsblk devices that discover_block leaves out
        (sr, loop, ...) but that are in use on the host."""
        result = {}
        for node in tree:
            name = node.get("name")
            if not name or (node.get("type") == "disk" and not name.startswith(KvmDevice._SkipBlockPrefixes)):
                continue
            mountpoints, host_uses = [], []
            KvmDevice._collect_usage(node, mountpoints, host_uses)
            if mountpoints:
                result[name] = f"mounted on host: {', '.join(mountpoints)}"
            elif host_uses:
                result[name] = f"in use by host: {', '.join(sorted(set(host_uses)))}"
        return result

    @staticmethod
    def _mounted_btrfs_uuids(nodes: list, result: set):
        for node in nodes or []:
            if node.get("fstype") == "btrfs" and node.get("uuid") and KvmDevice._node_mountpoints(node):
                result.add(node["uuid"])
            KvmDevice._mounted_btrfs_uuids(node.get("children", []), result)

    @staticmethod
    def _holders(disk: str, partitions: list) -> list:
        """Kernel holders (dm/md/bcache/...) stacked on the disk or its partitions.

        Catches host use that lsblk types/fstypes miss, e.g. bcache backing devices.
        """
        uses = []
        for part in [None] + [p for p in partitions if p]:
            base = os.path.join(KvmDevice.SysBlockDir, disk) if part is None \
                else os.path.join(KvmDevice.SysBlockDir, disk, part)
            try:
                holders = sorted(os.listdir(os.path.join(base, "holders")))
            except OSError:
                continue
            if holders:
                uses.append(f"holder of {part or disk}: {', '.join(holders)}")
        return uses

    @staticmethod
    def _by_id_map() -> dict:
        """{/dev/<name>: [by-id link names]}."""
        result = {}
        if not os.path.isdir(KvmDevice.DiskByIdDir):
            return result
        for link in os.listdir(KvmDevice.DiskByIdDir):
            target = os.path.realpath(os.path.join(KvmDevice.DiskByIdDir, link))
            result.setdefault(target, []).append(link)
        return result

    @staticmethod
    def _pick_by_id(links: list):
        if not links:
            return None
        for prefix in KvmDevice._ByIdPreference:
            for link in sorted(links):
                if link.startswith(prefix):
                    return os.path.join(KvmDevice.DiskByIdDir, link)
        return os.path.join(KvmDevice.DiskByIdDir, sorted(links)[0])

    @staticmethod
    def _norm_hex(value):
        if value is None:
            return None
        value = value.lower()
        if value.startswith("0x"):
            value = value[2:]
        return value.zfill(4)

    @staticmethod
    def _to_int(value):
        """Decimal ('005' → 5) or 0x-prefixed hex string, or a number; else None."""
        if value is None:
            return None
        try:
            if isinstance(value, str):
                value = value.strip()
                return int(value, 16) if value.lower().startswith("0x") else int(value, 10)
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_number(value):
        """'480' → 480, '1.5' → 1.5, anything else → None."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return int(number) if number.is_integer() else number

    @staticmethod
    def _to_bool(value):
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        return str(value) in ("1", "true", "True")


def main():
    args = KvmDevice.parse_args()
    logger = Log.get_logger("KvmDevice")
    print(json.dumps(KvmDevice.discover(args.type, logger), indent=4))


if __name__ == "__main__":
    main()
