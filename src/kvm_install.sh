#!/bin/bash

# install all related software
# qemu-utils provides qemu-img (used for local image create/info/commit), which is
# NOT pulled in by qemu-kvm (a transitional package depending on qemu-system-x86).
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
