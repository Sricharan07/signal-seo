#!/bin/sh
set -eu

# Scope only this dedicated bridge; retain established SSH-tunnel responses.
if ! /usr/sbin/iptables -C DOCKER-USER -i sig-bao -m conntrack --ctstate NEW -j DROP 2>/dev/null; then
  /usr/sbin/iptables -I DOCKER-USER 1 -i sig-bao -m conntrack --ctstate NEW -j DROP
fi
