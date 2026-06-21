  1，删除vm wireguard01，
  2，等vm删除后，删除image wireguard26.04.01,
  3, 重建image wireguard26.04.01,
  4, 重建vm wireguard01用ip 192.168.1.104
  5, ssh进入wireguard01,
     5.1 update apt source to mirrors.aliyun.com
     5.2 安装所有可应用更新
     5.3 prep_image_for_commit
  6, commit vm wireguard01
  7, 删除wireguard01, 然后重建with dhcp