"""Disposable non-dev OpenBao ceremony using synthetic, independently keyed recipients."""

import base64
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from openbao_lab import IMAGE, LABEL, cleanup, docker, require_free_space
from self_host import ROOT, SelfHostStore, initialize, unseal
from self_host_material import tls_material


@dataclass(frozen=True)
class SelfHostLabServer:
    store: SelfHostStore = field(repr=False)
    root: str = field(repr=False)
    tls: dict = field(repr=False)

    def __iter__(self):
        return iter((self.store, self.root, self.tls))


def gpg(home, *args, content=None):
    result = subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--pinentry-mode", "loopback", *args],
        input=content,
        capture_output=True,
        check=True,
        timeout=90,
    )
    return result.stdout


@contextmanager
def isolated_bootstrap(monkeypatch):
    require_free_space(ROOT)
    if not shutil.which("gpg"):
        raise RuntimeError("GPG required for the real encrypted initialization ceremony.")
    name = "signal-self-host-tests-" + secrets.token_hex(6)
    # Keep GPG's Unix socket path below platform limits, especially on macOS.
    with tempfile.TemporaryDirectory(prefix="signal-shlab-", dir="/tmp") as temporary:
        directory = Path(temporary).resolve()
        os.chmod(directory, 0o755)
        tls = tls_material()
        tls_directory = directory / "tls"
        tls_directory.mkdir(mode=0o755)
        for filename, value in {**tls["leaves"]["openbao"], "ca.pem": tls["ca.pem"]}.items():
            (tls_directory / filename).write_text(value)
            (tls_directory / filename).chmod(0o444)
        recovery = directory / "recovery"
        recovery.mkdir(mode=0o700)
        ca = recovery / "ca.pem"
        ca.write_text(tls["ca.pem"])
        ca.chmod(0o600)
        home = directory / "synthetic-gnupg"
        home.mkdir(mode=0o700)
        recipients = []
        for number in range(4):
            uid = f"synthetic-self-host-{number}@example.invalid"
            gpg(home, "--passphrase", "", "--quick-generate-key", uid, "rsa2048", "encr", "1d")
            recipients.append(base64.b64encode(gpg(home, "--export", uid)).decode())
        print("Starting disposable non-dev encrypted OpenBao bootstrap.", flush=True)
        store = None
        try:
            docker(
                "container",
                "create",
                "--name",
                name,
                "--label",
                f"{LABEL}={name}",
                "--publish",
                "127.0.0.1::8200",
                "--memory",
                "384m",
                "--cpus",
                "1",
                "--user",
                "100:1000",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/openbao/file:rw,nosuid,nodev,size=64m,uid=100,gid=1000,mode=0700",
                "--tmpfs",
                "/openbao/logs:rw,nosuid,nodev,size=16m,uid=100,gid=1000,mode=0700",
                "--mount",
                f"type=bind,source={tls_directory},target=/openbao/tls,readonly",
                "--mount",
                f"type=bind,source={ROOT / 'deploy/self-host/openbao.hcl'},"
                "target=/openbao/config/server.hcl,readonly",
                "--entrypoint",
                "bao",
                IMAGE,
                "server",
                "-config=/openbao/config/server.hcl",
                timeout=180,
            )
            docker("container", "start", name)
            binding = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Ports"]["8200/tcp"]
            assert len(binding) == 1 and binding[0]["HostIp"] == "127.0.0.1"
            store = SelfHostStore(f"https://localhost:{binding[0]['HostPort']}", ca)
            deadline = time.monotonic() + 45
            while True:
                try:
                    if store.request("GET", "/sys/init")["initialized"] is False:
                        break
                except Exception:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Disposable bootstrap did not become ready.")
                time.sleep(0.2)
            assert initialize(store, recovery, recipients[:3], recipients[3])
            assert not initialize(store, recovery, recipients[:3], recipients[3])
            envelope = json.loads((recovery / "openbao-init.pgp.json").read_text())
            root = gpg(home, "--decrypt", content=base64.b64decode(envelope["root_token"])).decode()
            shares = [
                gpg(home, "--decrypt", content=base64.b64decode(share)).decode()
                for share in envelope["keys_base64"]
            ]
            entries = iter(shares[:2])
            monkeypatch.setattr("self_host.hidden", lambda _: next(entries))
            assert unseal(store)
            assert not unseal(store)
            raw = (recovery / "openbao-init.pgp.json").read_text()
            assert root not in raw and all(share not in raw for share in shares)
            yield SelfHostLabServer(store, root, tls)
        finally:
            if store is not None:
                store.close()
            cleanup(name)
            subprocess.run(
                ["gpgconf", "--homedir", str(home), "--kill", "all"],
                capture_output=True,
                check=True,
                timeout=30,
            )
