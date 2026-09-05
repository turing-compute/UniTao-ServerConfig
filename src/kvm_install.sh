#!/bin/bash
set -e

# install all related software
# qemu-utils provides qemu-img (used for local image create/info/commit), which is
# NOT pulled in by qemu-kvm (a transitional package depending on qemu-system-x86).
# libvirt-clients provides virsh; libvirt-daemon-system provides libvirtd. Both are
# listed explicitly on purpose — nothing else in this line pulls them in.
apt-get install -y qemu-kvm qemu-utils libvirt-daemon-system libvirt-clients bridge-utils virt-manager

# enable local user able to manage kvm without sudo
# need reboot to take effect
adduser $USER kvm
adduser $USER libvirt

# install pip and update to latest
apt-get install -y python3-pip

# install genisoimage for Cloud-init install 
apt-get install -y genisoimage

apt-get install -y openvswitch-switch

# Verify every binary UniTao shells out to is now on PATH. The first apt-get line is
# a single transaction: if one package fails, apt installs NONE of them yet the script
# (without this check) would keep going and leave a host that dies later with
# "No such file or directory: virsh" at VM/image creation time.
MISSING=()
for cmd in virsh virt-install qemu-img genisoimage; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        MISSING+=("$cmd")
    fi
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "error: KVM toolchain incomplete after install, missing: ${MISSING[*]}" >&2
    echo "A package above failed to install. Fix apt and re-run this script, e.g.:" >&2
    echo "    sudo apt-get update && sudo $0" >&2
    exit 1
fi
echo "OK: KVM toolchain present (virsh, virt-install, qemu-img, genisoimage)."
