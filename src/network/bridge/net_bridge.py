#!/usr/bin/env python3

#########################################################################################
# Linux Network Bridge utilities
# this will use network bridge json data and use different command base on bridge type to
# - Create/Delete bridge
# - Add/remove network interfaces to/from specified bridge
#########################################################################################

import argparse
import logging
import os

from shared.logger import Log
from shared.utilities import Util


class NetBridge:
    class Keyword:
        BridgeType = "bridgeType"
        Interfaces = "interfaces"
        MacAddress = "macAddress"

        class BridgeTypes:
            LinuxBridge = "linuxBridge"
            OvsBridge   = "ovsBridge"

            @staticmethod
            def list():
                return [
                    NetBridge.Keyword.BridgeTypes.LinuxBridge,
                    NetBridge.Keyword.BridgeTypes.OvsBridge                
                ]
    
    @staticmethod
    def parse_args() -> argparse.Namespace:
        parser = argparse.ArgumentParser(description=f"Linux Network Bridge Operations")
        parser.add_argument("--path", type=str, help=f"Linux Network Bridge Data Path for Vm Operation", required=True)
        args = parser.parse_args()
        return args

    def __init__(self, logger: logging.Logger, data_path: str = None):
        self.log = logger
        if data_path is None:
            args = NetBridge.parse_args()
            data_path = args.path
        self.DataPath = data_path
        if not os.path.exists(self.DataPath):
            raise ValueError(f"Invalid path does not exists.[{self.DataPath}]")
        self.BridgeName = Util.file_data_name(self.DataPath)
        self.BrData = Util.read_json_file(self.DataPath)

    def Validate(self):
        br_type = self.BrData.get(self.Keyword.BridgeType, None)
        if br_type is None:
            raise ValueError(f"Error: Missing field[{self.Keyword.BridgeType}] or value is None")
        if br_type not in self.Keyword.BridgeTypes.list():
            raise ValueError(f"Error: invalid [{self.Keyword.BridgeType}]=[{br_type}], supported values[{self.Keyword.BridgeTypes.list()}]")
        iface_list = self.BrData.get(self.Keyword.Interfaces, None)
        if iface_list is None:
            raise ValueError(f"Error: Missing field[{self.Keyword.Interfaces}] or value is None")
        if not isinstance(iface_list, list):
            raise ValueError(f"Error: field[{self.Keyword.Interfaces}] needs to be a list of interface names")

    def Process(self):
        self.Validate()
        self.Create()

    def Create(self):
        br_type = self.BrData[self.Keyword.BridgeType]
        if self._bridge_exists():
            self.log.info(f"Bridge [{self.BridgeName}] already exists, skip creation")
            self._sync_interfaces()
            return
        self.log.info(f"Create bridge [{self.BridgeName}] type=[{br_type}]")
        if br_type == self.Keyword.BridgeTypes.LinuxBridge:
            self._create_linux_bridge()
        elif br_type == self.Keyword.BridgeTypes.OvsBridge:
            self._create_ovs_bridge()
        self._set_mac_address()
        Util.run_command(f"ip link set dev {self.BridgeName} up")
        self._sync_interfaces()

    def Delete(self):
        br_type = self.BrData[self.Keyword.BridgeType]
        if not self._bridge_exists():
            self.log.info(f"Bridge [{self.BridgeName}] does not exist, skip deletion")
            return
        self.log.info(f"Delete bridge [{self.BridgeName}] type=[{br_type}]")
        Util.run_command(f"ip link set dev {self.BridgeName} down")
        if br_type == self.Keyword.BridgeTypes.LinuxBridge:
            Util.run_command(f"brctl delbr {self.BridgeName}")
        elif br_type == self.Keyword.BridgeTypes.OvsBridge:
            Util.run_command(f"ovs-vsctl del-br {self.BridgeName}")

    def _bridge_exists(self) -> bool:
        br_type = self.BrData[self.Keyword.BridgeType]
        if br_type == self.Keyword.BridgeTypes.LinuxBridge:
            result = Util.run_command("brctl show")
            lines = result.stdout_lines[1:]
            bridges = [line.split()[0] for line in lines if line]
            return self.BridgeName in bridges
        elif br_type == self.Keyword.BridgeTypes.OvsBridge:
            result = Util.run_command("ovs-vsctl list-br")
            return self.BridgeName in result.stdout_lines

    def _create_linux_bridge(self):
        Util.run_command(f"brctl addbr {self.BridgeName}")

    def _create_ovs_bridge(self):
        Util.run_command(f"ovs-vsctl add-br {self.BridgeName}")

    def _set_mac_address(self):
        mac = self.BrData.get(self.Keyword.MacAddress, None)
        if mac is None:
            return
        br_type = self.BrData[self.Keyword.BridgeType]
        if br_type == self.Keyword.BridgeTypes.LinuxBridge:
            Util.run_command(f"ip link set dev {self.BridgeName} address {mac}")
        elif br_type == self.Keyword.BridgeTypes.OvsBridge:
            Util.run_command(f"ovs-vsctl set bridge {self.BridgeName} other-config:hwaddr={mac}")

    def _sync_interfaces(self):
        iface_list = self.BrData.get(self.Keyword.Interfaces, [])
        br_type = self.BrData[self.Keyword.BridgeType]
        current_ifaces = self._list_interfaces()
        for iface in current_ifaces:
            if iface not in iface_list:
                self.log.info(f"Remove interface [{iface}] from bridge [{self.BridgeName}]")
                self._remove_interface(iface)
        for iface in iface_list:
            if iface not in current_ifaces:
                self.log.info(f"Add interface [{iface}] to bridge [{self.BridgeName}]")
                self._add_interface(iface)

    def _list_interfaces(self) -> list:
        br_type = self.BrData[self.Keyword.BridgeType]
        if br_type == self.Keyword.BridgeTypes.LinuxBridge:
            result = Util.run_command(f"brctl show {self.BridgeName}")
            interfaces = []
            for line in result.stdout_lines[1:]:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == self.BridgeName and len(parts) > 3:
                    interfaces.append(parts[3])
                elif parts[0] != self.BridgeName:
                    interfaces.append(parts[0])
            return interfaces
        elif br_type == self.Keyword.BridgeTypes.OvsBridge:
            result = Util.run_command(f"ovs-vsctl list-ports {self.BridgeName}")
            return [line for line in result.stdout_lines if line]

    def _add_interface(self, iface: str):
        br_type = self.BrData[self.Keyword.BridgeType]
        if br_type == self.Keyword.BridgeTypes.LinuxBridge:
            Util.run_command(f"brctl addif {self.BridgeName} {iface}")
        elif br_type == self.Keyword.BridgeTypes.OvsBridge:
            Util.run_command(f"ovs-vsctl add-port {self.BridgeName} {iface}")

    def _remove_interface(self, iface: str):
        br_type = self.BrData[self.Keyword.BridgeType]
        if br_type == self.Keyword.BridgeTypes.LinuxBridge:
            Util.run_command(f"brctl delif {self.BridgeName} {iface}")
        elif br_type == self.Keyword.BridgeTypes.OvsBridge:
            Util.run_command(f"ovs-vsctl del-port {self.BridgeName} {iface}")


if __name__ == "__main__":
    logger = Log.get_logger("NetBridge")
    logger.info("Network Bridge Operation")
    bridge = NetBridge(logger)
    bridge.Process()
