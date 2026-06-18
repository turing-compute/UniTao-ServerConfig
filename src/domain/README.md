# domain specific server config tool

Since UniTAO Server Config is for configuration of vms.
for vm of specific domain purpose. it require communication through host rest API and special action.

for custom purpose, usually, it should be built with the vm image.

but here we use wireguard as sample purpose to show how the communication is used.

## domain tool - wireguard

 - To help comunicate through host rest service
   - post wireguard public key for client to consume
   - receive assigned wireguard VPN network settings
   - receive peer info.
    - peer public key
    - peer psk(PreShared Key)
    - peer route
    - assign ip to peer
 - Setup NAT and route according to given route instruction





