# VM utility for KVM
Create vm using qemu utilities base on given json model attributes.

### Description
This utilities will use all the resource and attribute given by json file and create a VM using qemu tool.

### schema
 ```jsonc
{vm_name}.json
{
    "vmPath": "{path}",             // path to store VM data and file
    "smp": 2,                       // number of CPUs assigned to this VM, [Symmetric Multi-Processing]
    "ramInGB": 2,                   // number GB of memory to be assigned to this VM
    "disks": [],                    // list of path to vm disk definition json file, path can use {vmPath} as reference for relative path
    "networks": [],                 // list of path to vm network definition json file, path can use {vmPath} as reference for relative path
    "vmState": "running",           // desired vm state. running, stopped, notExists
    "useCloudInit": true,           // define how the VM will prepare itself
    "ciIsoPath": "{path}",          // path to Cloud Init ISO image file
    "login": "host_key|{pwd}",      // auto-generated: "host_key" when Host key injected, or random password
    "vmHostName": "{hostname}",     // setup vm hostname when create
    "hostCPU": true                 // optional, define if the VM will use --cpu host to set VM use CPU-Pass-Through Mode
}

example.json
{
    "vmPath": "../",
    "smp": 2,
    "ramInGB": 2,
    "disks":[
       "{vmPath}/data/wireguard_os.json"
    ],
    "networks": [
       "{vmPath}/data/local_bridge.json"
    ],
    "vmState": "running",
    "useCloudInit": true,
    "ciIsoPath": "{vmPath}/cloud_init.iso",
    "vmHostName": "test-01",
    "osType": "linux",                                  // operating system types[linux, windows], 
                                                        // for now, we only support linux
    "osVariant": "ubuntu24.04",                         // exact operating system name with version.
    "hostCPU": true                                     // define VM to use CPU Pass Through Mode
}


// The disk link json file is just a link. 
// For physically generate the image, we still need to use kvm_image.py
{disk_name}.json
{
    "diskPath": "path"      // path that link to the disk image file. path can be relative path to the disk data file
}

{network_name}.json
{
    "ifaceType": "bridge",                  // VM net interface type. bridge/macvtap
    "BridgeName": "ext_net",                // bridge the vm net interface to connect with
    "macAddress": "{mac_address_str}",      // mac address to be assign to the network interface
    "useDHCP4": true,                          // if the interface to use dhcp service to get ipv4 address
    "ip4": "ip v4 address{x.x.x.x/x}",      // if not dhcp, the IP address to be assigned to the interface
    "gateway4": "gateway ipv4. {x.x.x.x}"   // if this is the one connect to external network, the gateway setting
    
}

{
    "ifaceType": "macvtap",                 // VM net interface type. bridge/macvtap
    "TapSource": "{ext iface/bridge name}", // the network interface on host for this vm interface to tap into
    "TapMode": "{tap mode value}",          // define how vm interface is tapping into host net interface
                                            // possible values [bridge, private, vepa, passthru]
    "macAddress": "{mac_address_str}",      // mac address to be assign to the network interface
    "dhcp4": true,                          // if the interface to use dhcp service to get ipv4 address
    "ip4": "ip v4 address{x.x.x.x/x}",      // if not dhcp, the IP address to be assigned to the interface
    "gateway4": "gateway ipv4. {x.x.x.x}"   // if this is the one connect to external network, the gateway setting
}

 ```