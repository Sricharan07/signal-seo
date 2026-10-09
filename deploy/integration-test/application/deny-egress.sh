#!/bin/sh
set -eu
if ! /usr/sbin/iptables -C DOCKER-USER -i sig-app -m conntrack --ctstate NEW -j DROP 2>/dev/null; then
  /usr/sbin/iptables -I DOCKER-USER 1 -i sig-app -m conntrack --ctstate NEW -j DROP
fi
if ! /usr/sbin/iptables -C INPUT -i sig-app -m conntrack --ctstate NEW -j DROP 2>/dev/null; then
  /usr/sbin/iptables -I INPUT 1 -i sig-app -m conntrack --ctstate NEW -j DROP
fi
