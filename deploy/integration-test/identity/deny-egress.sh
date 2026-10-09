#!/bin/sh
set -eu

# Preserve replies to the operator's loopback ingress; deny new outbound access.
if ! /usr/sbin/iptables -C DOCKER-USER -i sig-identity -m conntrack --ctstate NEW -j DROP 2>/dev/null; then
  /usr/sbin/iptables -I DOCKER-USER 1 -i sig-identity -m conntrack --ctstate NEW -j DROP
fi
if ! /usr/sbin/iptables -C INPUT -i sig-identity -m conntrack --ctstate NEW -j DROP 2>/dev/null; then
  /usr/sbin/iptables -I INPUT 1 -i sig-identity -m conntrack --ctstate NEW -j DROP
fi
