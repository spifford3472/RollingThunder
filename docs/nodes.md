# RollingThunder – Node Baseline

This document captures the baseline state of each Raspberry Pi
immediately after OS installation and minimal role-based prep.
No RollingThunder services are installed at this stage.

---

## rt-controller

**Role:** Controller / Core services  
**Hardware:** Raspberry Pi 4  
**OS:** Raspberry Pi OS Lite (64-bit) – Bookworm  

### System

Linux rt-controller 6.12.47+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.12.47-1+rpt1~bookworm (2025-09-16) aarch64 GNU/Linux
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION="12 (bookworm)"
VERSION_CODENAME=bookworm
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"

### Networking

**Hostname:** rt-controller
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host noprefixroute 
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default qlen 1000
    link/ether e4:5f:01:82:df:ff brd ff:ff:ff:ff:ff:ff
    inet 192.168.4.67/22 brd 192.168.7.255 scope global dynamic noprefixroute eth0
       valid_lft 14268sec preferred_lft 14268sec
    inet6 fd79:b451:4635:1:7bbc:cbdd:33e7:f08f/64 scope global dynamic noprefixroute 
       valid_lft 2591871sec preferred_lft 604671sec
    inet6 fe80::abf4:a78e:4af6:f2c/64 scope link noprefixroute 
       valid_lft forever preferred_lft forever
3: docker0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN group default 
    link/ether 02:42:63:78:c3:3f brd ff:ff:ff:ff:ff:ff
    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0
       valid_lft forever preferred_lft forever

### Interfaces

Serial enabled: YES
I²C enabled: YES
Wi-Fi: Enabled for setup (to be disabled in vehicle)
Bluetooth: Disabled (or enabled, note which)

### Installed baseline packages

docker.io
mosquitto-clients
redis-tools

## rt-radio

**Role:** FT-891 Radio Interface
**Hardware:** Raspberry Pi Zero 2 W
**OS:** Raspberry Pi OS Lite (32-bit) – Bookworm

### System

Linux rt-radio 6.12.47+rpt-rpi-v7 #1 SMP Raspbian 1:6.12.47-1+rpt1~bookworm (2025-09-16) armv7l GNU/Linux
PRETTY_NAME="Raspbian GNU/Linux 12 (bookworm)"
NAME="Raspbian GNU/Linux"
VERSION_ID="12"
VERSION="12 (bookworm)"
VERSION_CODENAME=bookworm
ID=raspbian
ID_LIKE=debian
HOME_URL="http://www.raspbian.org/"
SUPPORT_URL="http://www.raspbian.org/RaspbianForums"
BUG_REPORT_URL="http://www.raspbian.org/RaspbianBugs"

### Networking

**Hostname:** rt-radio
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host noprefixroute 
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    link/ether a0:ce:c8:5c:8c:07 brd ff:ff:ff:ff:ff:ff
    inet 192.168.4.66/22 brd 192.168.7.255 scope global dynamic noprefixroute eth0
       valid_lft 13797sec preferred_lft 13797sec
    inet6 fd79:b451:4635:1:f13:c857:3520:2c4b/64 scope global dynamic noprefixroute 
       valid_lft 2591975sec preferred_lft 604775sec
    inet6 fe80::c6e3:cac1:3ee1:220d/64 scope link noprefixroute 
       valid_lft forever preferred_lft forever

### Interfaces

USB host mode: YES
Serial enabled: YES (no login shell)
Wi-Fi: Enabled for setup only
Bluetooth: Disabled

### Notes

Serial Info: lrwxrwxrwx 1 root root 7 Jan 13 16:49 /dev/serial0 -> ttyAMA0
No RTL-SDR installed
Single USB device expected (DigiRig DR-891)


## rt-display

**Role:** UI / Kiosk Display
**Hardware:** Raspberry Pi 3
**OS:** Raspberry Pi OS Desktop (64-bit) – Bookworm

### System

Linux rt-display 6.12.47+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.12.47-1+rpt1~bookworm (2025-09-16) aarch64 GNU/Linux
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION="12 (bookworm)"
VERSION_CODENAME=bookworm
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"

### Networking

**Hostname:** rt-display
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host noprefixroute 
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    link/ether b8:27:eb:5d:d5:70 brd ff:ff:ff:ff:ff:ff
    inet 192.168.4.68/22 brd 192.168.7.255 scope global dynamic noprefixroute eth0
       valid_lft 14269sec preferred_lft 14269sec
    inet6 fd79:b451:4635:1:293d:46b1:6e98:e4/64 scope global dynamic noprefixroute 
       valid_lft 2591871sec preferred_lft 604671sec
    inet6 fe80::c78a:e932:869c:9b6/64 scope link noprefixroute 
       valid_lft forever preferred_lft forever
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    link/ether b8:27:eb:08:80:25 brd ff:ff:ff:ff:ff:ff
    inet 192.168.8.215/24 brd 192.168.8.255 scope global dynamic noprefixroute wlan0
       valid_lft 43135sec preferred_lft 43135sec
    inet6 fe80::a468:5a43:7491:37ed/64 scope link noprefixroute 
       valid_lft forever preferred_lft forever

### Display

Screen 0: minimum 16 x 16, current 1920 x 1080, maximum 32767 x 32767
XWAYLAND0 connected 1920x1080+0+0 (normal left inverted right x axis y axis) 600mm x 340mm
   1920x1080     59.96*+
   1440x1080     59.99  
   1400x1050     59.98  
   1280x1024     59.89  
   1280x960      59.94  
   1152x864      59.96  
   1024x768      59.92  
   800x600       59.86  
   640x480       59.38  
   320x240       59.52  
   1680x1050     59.95  
   1440x900      59.89  
   1280x800      59.81  
   720x480       59.71  
   640x400       59.95  
   320x200       58.96  
   1600x900      59.95  
   1368x768      59.88  
   1280x720      59.86  
   1024x576      59.90  
   864x486       59.92  
   720x400       59.55  
   640x350       59.77 

HDMI output confirmed
Desktop boots correctly

## rt-wpsd

**Role:** DMR / WPSD External System
**Hardware:** Raspberry Pi 4
**OS:** Existing installation (not reimaged)

### System

Linux rt-wpsd 6.1.21-v8+ #1642 SMP PREEMPT Mon Apr  3 17:24:16 BST 2023 aarch64 GNU/Linux
PRETTY_NAME="Raspbian GNU/Linux 11 (bullseye)"
NAME="Raspbian GNU/Linux"
VERSION_ID="11"
VERSION="11 (bullseye)"
VERSION_CODENAME=bullseye
ID=raspbian
ID_LIKE=debian
HOME_URL="http://www.raspbian.org/"
SUPPORT_URL="http://www.raspbian.org/RaspbianForums"
BUG_REPORT_URL="http://www.raspbian.org/RaspbianBugs"

### Networking

**Hostname:** rt-wpsd
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host 
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default qlen 1000
    link/ether 2c:cf:67:0b:7e:d5 brd ff:ff:ff:ff:ff:ff
    inet 192.168.8.184/24 brd 192.168.8.255 scope global dynamic noprefixroute eth0
       valid_lft 42785sec preferred_lft 37385sec
    inet6 fe80::f8d8:a47a:9843:953c/64 scope link 
       valid_lft forever preferred_lft forever
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    link/ether 2c:cf:67:0b:7e:d7 brd ff:ff:ff:ff:ff:ff
    inet 192.168.1.113/24 brd 192.168.1.255 scope global dynamic noprefixroute wlan0
       valid_lft 85988sec preferred_lft 75188sec
    inet6 2600:1009:b129:ed3a:e112:fab8:5ad1:1eb/64 scope global mngtmpaddr noprefixroute 
       valid_lft forever preferred_lft forever
    inet6 fe80::279d:8762:3467:4d64/64 scope link 
       valid_lft forever preferred_lft forever
default via 192.168.8.1 dev eth0 proto dhcp src 192.168.8.184 metric 202 
default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.113 metric 303 mtu 1428 
192.168.1.0/24 dev wlan0 proto dhcp scope link src 192.168.1.113 metric 303 mtu 1428 
192.168.8.0/24 dev eth0 proto dhcp scope link src 192.168.8.184 metric 202 

### Listening Services

Netid    State           Recv-Q          Send-Q              Local Address:Port              Peer Address:Port          Process          
udp      UNCONN          0               0                         0.0.0.0:34832                  0.0.0.:*                              
udp      UNCONN          0               0                         0.0.0.0:68                     0.0.0.:*                              
udp      UNCONN          0               0                         0.0.0.0:111                    0.0.0.:*                              
udp      UNCONN          0               0                       127.0.0.1:62031                  0.0.0.0:*                              
udp      UNCONN          0               0                       127.0.0.1:62032                  0.0.0.0:*                              
udp      UNCONN          0               0                         0.0.0.0:40094                  0.0.0.0:*                              
udp      UNCONN          0               0                         0.0.0.0:40095                  0.0.0.0:*                              
udp      UNCONN          0               0                         0.0.0.0:5353                   0.0.0.0:*                              
udp      UNCONN          0               0                         0.0.0.0:36159                  0.0.0.0:*                              
udp      UNCONN          0               0                        127.0.0.1:7642                  0.0.0.0:*                              
udp      UNCONN          0               0                       127.0.0.1:7643                   0.0.0.0:*                           
udp      UNCONN          0               0                         0.0.0.0:48813                  0.0.0.0:*                              
udp      UNCONN          0               0                         0.0.0.0:3769                   0.0.0.0:*                              
udp      UNCONN          0               0                         0.0.0.0:1900                    0.0.0.0:*                              
udp      UNCONN          0               0                               *:111                                  *:*                              
udp      UNCONN          0               0                               *:546                                  *:*                              
tcp      LISTEN          0               128                       0.0.0.0:2222                    0.0.0.0:*                              
tcp      LISTEN          0               511                       0.0.0.0:80                      0.0.0.0:*                              
tcp      LISTEN          0               4096                      0.0.0.0:111                     0.0.0.0:*                              
tcp      LISTEN          0               128                       0.0.0.0:22                      0.0.0.0:*                              
tcp      LISTEN          0               4096                         [::]:111                        [::]:*                              
tcp      LISTEN          0               128                          [::]:22                         [::]:*   

### Legacy cleanup

Removed legacy Mobile Comms services:
- node-health.service
- node-control.service
- temp_service.service

Associated code under /opt/mobile removed.
systemd units deleted and daemon reloaded.

### Notes

- Treated as external dependency
- Version information documented separately


### Access notes
- Hostname standardized to rt-wpsd
- System user retained from WPSD install (not renamed)
- Treated as external appliance
- RollingThunder will integrate via network APIs only

