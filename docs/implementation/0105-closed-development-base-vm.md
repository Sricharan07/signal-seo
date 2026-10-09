# Slice 0105: Closed Development Base VM

Classification: **security-critical operational preparation**: manual AWS
provisioning, administrative access and network isolation. Base: `404eeb9`.
Contract: Revision 4.0 sections 4 and 19; Revision 3.2 sections 2, 29 and 35.
This records one owner-requested base VM, not a Signal deployment or installer.
No architecture or release gate changes; no new ADR is required.

**Current state: base VM provisioned; application deployment unavailable.**
The real AWS and SSH checks cover only the machine and its closed administrative
boundary. Initial bootstrap required manual recovery, recorded below. The
[evidence](../evidence/0105-closed-development-base-vm.json) distinguishes that
failure from the subsequently verified configuration.

## Resources and Ownership

| Setting | Observed value |
| --- | --- |
| Provider | AWS Lightsail, owner's existing CLI account |
| Instance | `integration-test-vm` |
| Location | `us-east-1`, `us-east-1a` |
| Blueprint | `ubuntu_24_04`, Ubuntu 24.04 LTS |
| Bundle | `medium_ipv6_3_0`, IPv6-only |
| Capacity | 2 vCPUs, 4 GB RAM, 80 GB SSD |
| Base price checked 2026-09-30 | USD 20/month; not an account spending cap |
| Extra resources created | One imported public SSH key; no disks, snapshots, static IPv4 or load balancer |

The price comes from the real `get-bundles` response and
[AWS pricing](https://aws.amazon.com/lightsail/pricing/). Taxes, excess transfer,
future resources and provider/API usage are not included in this base estimate.
This sizing is an owner-selected development starting point, **not a measured
minimum for the full stack**. No application load test has run.

The dedicated RSA-4096 operator private key remains on the owner's Mac with mode
`0600`, outside the repository; only its public half was imported to AWS. Operator
IPs, host keys, provider payloads and credentials are not committed. No Signal,
GitHub, Slack, Google, Jev or OpenAI application secrets were put on the VM.

## Closed Boundary

- The AWS firewall contains exactly TCP 22 from the Mac's current public IPv6
  `/128`, with no IPv4 sources or Lightsail-connect aliases. UFW independently
  denies incoming traffic and allows the same single-source SSH rule.
- Public-key authentication only, `ubuntu` only; root, passwords, keyboard
  interaction, agent forwarding, X11 and remote forwarding are disabled. Local
  forwarding can target only `127.0.0.1`. SSH configuration validation passed.
- IMDSv2 tokens are required; response hop limit is one. A metadata request
  without a token returns HTTP 401. No metadata credentials were retrieved.
- No public application listener exists. Ports 80, 443, 3000, 5432, 7233, 8080
  and 8200 were tested from the Mac and timed out. Only SSH is externally bound;
  DNS/time infrastructure listeners are local or link-specific.
- No runtime, repository checkout, customer data, synthetic pilot, connector,
  callback, tunnel, DNS record or TLS endpoint was deployed. `example.invalid` and
  its existing website/mail records were not changed.

SSH host keys returned by AWS were empty. The initial ED25519 host key was pinned
on first contact at the exact IPv6 returned by authenticated AWS, then all SSH
checks used `StrictHostKeyChecking=yes`. This is trust on first use, not an
independently authenticated AWS host-key attestation. The pin survived reboot.

## Failures and Recovery

1. Base64-encoding the complete public-key text was rejected by AWS with
   `InvalidInputException`. The exact named key was confirmed absent. Passing
   the OpenSSH public `.pub` file directly to `--public-key-base64 file://...`
   succeeded with the same RSA-4096 key. No private key was transmitted.
2. The first firewall update was rejected while the VM was `pending`. After AWS
   reported `running`, the replace-all port update succeeded; its readback
   contains only the restricted SSH rule. No application was installed during
   this transition. The launch-time OS firewall did not take effect, as below.
3. This Lightsail blueprint prepended its shell initialization to the supplied
   cloud-config, so the YAML was executed as shell and cloud-init reported
   `scripts_user` failure. The YAML parsing check alone did not qualify startup.
   An explicit configuration file was copied over pinned SSH, installed as
   root-owned mode `0644`, checked with `sshd -t`, and applied with UFW and an SSH
   reload. Fresh connections verified every intended setting.
4. An AWS reboot confirmed the SSH pin, firewall and authentication settings
   persisted. The next boot reported cloud-init `done`; the once-per-instance
   failed script was not retried. The original failure logs were preserved, not
   cleared or relabelled as a successful unattended bootstrap.

The original cloud-config is **not a reusable qualified launch template**. A
future installer must use a reviewed Lightsail shell launch script and prove
first-boot success, rather than depend on this manual repair.

## Verification and Administration

The actual result includes AWS sizing/state/port/metadata readbacks, SSH resource
checks, rejected root/no-key/password attempts, blocked application ports and
reboot persistence. Ubuntu package updates, including the new AWS kernel, were
installed without package removals; the final reboot runs Ubuntu 24.04.5 LTS and
`7.0.0-1013-aws`, with no upgradable packages at the check. `npm test` passed
(24 repository and 148 dashboard cases, documentation, typecheck and build).
No application or PostgreSQL safety behavior changed in this slice;
the database and connector qualification suites were not rerun.

An intermediate repository rerun hit duplicate generated declarations in
`.next/types`. Four additional `2.ts` copies were preserved in ignored runtime
storage; the full unchanged gate passed after removing those copies from the
generated input directory. No source or typecheck/test configuration changed.

Read current non-secret provider state without printing access credentials:

```sh
aws lightsail get-instance --region <region> --instance-name <instance> \
  --query 'instance.{State:state.name,Bundle:bundleId,CPU:hardware.cpuCount,RAM:hardware.ramSizeInGb,Disk:hardware.disks[].sizeInGb,IPv6:ipv6Addresses,Metadata:metadataOptions}'
aws lightsail get-instance-port-states --region <region> --instance-name <instance>
aws lightsail get-bundles --region <region> \
  --query 'bundles[?bundleId==`medium_ipv6_3_0`].{Bundle:bundleId,Price:price}'
npm test
```

The operator's local verification script and pinned `known_hosts` are in
`$HOME/.codex/signal-aws-base-20260930/`; the private key is
`$HOME/.ssh/integration-test-vm-20260930`. For a changed Mac IPv6, verify the new exact
source through the owner's authenticated AWS account and update **both** firewall
layers. Never resolve a lockout by opening SSH to the world. Do not print full
`get-instance-access-details` responses; they include temporary credentials.

## Still Unavailable

This single VM does not meet the private-pilot or production topology: separate
isolated execution capacity, physically independent journal/backup/recovery,
qualified database, identity, secrets, egress, TLS/callback and restore paths are
still required. The local synthetic pilot must not be exposed or reused with
customer credentials. No public test-service exception has been granted.

Live provider qualification, the hosting installer, published/signed images,
production writes and release admission remain unavailable. The next product
work remains review 0104, then its controlled live qualification; provisioning
this machine does not satisfy those gates.
