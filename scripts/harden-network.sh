#!/usr/bin/env bash
# Local-isolation hardening for the cloud telemetry link
# (ASKET-SPEC-COMMS-CLOUD-v1.0 §9.1). Run on the Jetson after njord.service
# is working, before relying on the cloud bridge in the field.
#
# Fix applied relative to the design note's own §9.1 rule, found by
# actually tracing the network topology it describes (§1-2), not by
# testing on real hardware (no Jetson/radio link in the environment this
# was written in):
#
#   The design note's rule was:
#     iptables -A INPUT -p tcp --dport 8765 -s 10.10.20.0/24 -j DROP
#   That blocks traffic sourced from 10.10.20.0/24 — the BOAT's OWN LAN
#   (per §1.1's subnet table, the Jetson/OAK/Livox/NetMetal/CSS610 all live
#   there). Per §2.2's own Layer-3 routing setup, anything crossing the
#   radio link from the shore arrives with a source IP in the SHORE subnet
#   (10.10.10.0/24), routed in via 10.10.99.0/30 — standard IP routing,
#   not NAT, so the source address is preserved. The design note's rule
#   therefore blocks the wrong subnet: it does nothing to stop a shore- or
#   radio-link-originated connection from reaching Foxglove, which is
#   exactly the threat §9.1's own prose describes defending against.
#
#   The prose's actual requirement — "bound strictly to 127.0.0.1" — is a
#   default-deny-except-loopback rule, which is what this script applies
#   instead: ACCEPT from 127.0.0.1, DROP everything else to that port,
#   regardless of which subnet it claims to be from.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash harden-network.sh" >&2
  exit 1
fi

echo "==> Firewalling Foxglove bridge (8765/TCP) to loopback only"
# Order matters: the ACCEPT rule must be inserted before the DROP rule.
iptables -C INPUT -p tcp --dport 8765 -s 127.0.0.1 -j ACCEPT 2>/dev/null \
  || iptables -I INPUT -p tcp --dport 8765 -s 127.0.0.1 -j ACCEPT
iptables -C INPUT -p tcp --dport 8765 -j DROP 2>/dev/null \
  || iptables -A INPUT -p tcp --dport 8765 -j DROP

echo "==> SSH: public-key authentication only"
SSHD_CONFIG=/etc/ssh/sshd_config
if ! grep -q "^PasswordAuthentication no" "$SSHD_CONFIG" 2>/dev/null; then
  cp "$SSHD_CONFIG" "${SSHD_CONFIG}.bak.$(date +%s)"
  sed -i -E 's/^#?PasswordAuthentication .*/PasswordAuthentication no/' "$SSHD_CONFIG"
  grep -q "^PasswordAuthentication" "$SSHD_CONFIG" || echo "PasswordAuthentication no" >> "$SSHD_CONFIG"
  systemctl reload sshd || systemctl reload ssh || true
  echo "    PasswordAuthentication disabled (backup saved alongside sshd_config)."
else
  echo "    Already disabled."
fi
echo "    Confirm /home/node/.ssh/authorized_keys has the operator's public key"
echo "    BEFORE this takes effect on a remote box — a lockout here means a"
echo "    physical console session to fix, same caveat as any sshd change."

echo "==> Persisting iptables rules across reboot"
if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save
  echo "    Saved via netfilter-persistent."
else
  echo "    netfilter-persistent not installed — these rules will NOT survive"
  echo "    a reboot. Install it: apt-get install -y iptables-persistent"
  echo "    (the design note's own §9.1 never addressed persistence either —"
  echo "    a bare 'iptables -A' at a terminal is gone on the next reboot.)"
fi

echo
echo "==> Current rule set for port 8765:"
iptables -L INPUT -n --line-numbers | grep -E "8765|^Chain" || true
echo
echo "Done. Outbound-only cloud dial-out (§9.1 point 3) needs no firewall"
echo "rule — cloud_telemetry_node only ever opens outbound connections and"
echo "this host accepts no inbound connection on any cloud-related port."
