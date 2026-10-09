"""Synthetic package, not an Astro compatibility qualification."""

import base64
import hashlib
import io
import json
import tarfile
from dataclasses import replace

from signal_core.github_app import (
    GitHubRepositoryCheckout,
    GitHubRepositoryInventory,
    GitHubTreeEntry,
)

from tests.tooling.test_candidate_build import _sha, _source

CLI = b"""#!/usr/bin/env node
const fs = require('fs');
if (process.argv[2] !== 'build') process.exit(40);
if (fs.existsSync('lifecycle-ran') || fs.existsSync('/var/run/docker.sock') ||
    fs.existsSync('/run/secrets') || Object.keys(process.env).some(k=>k.startsWith('SIGNAL_')))
  process.exit(41);
if (fs.existsSync('needs-lifecycle') && !fs.existsSync('lifecycle-ran')) process.exit(42);
if (fs.existsSync('malicious-build')) fs.writeFileSync('.github/workflows/ci.yml', 'changed');
fs.mkdirSync('public-build');
fs.writeFileSync('public-build/index.html',
  '<!doctype html><title>Synthetic page</title><meta name="description" content="Bounded build">');
"""


def package_tarball(*, extra=None, script=CLI):
    package = {
        "name": "astro",
        "version": "5.0.0",
        "bin": {"astro": "bin.cjs"},
        "scripts": {
            "postinstall": (
                "node -e \"require('fs').writeFileSync('/workspace/lifecycle-ran','ran')\""
            )
        },
    }
    files = {"package/package.json": json.dumps(package).encode(), "package/bin.cjs": script}
    files.update(extra or {})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        directory = tarfile.TarInfo("package/")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        for path, content in files.items():
            entry = tarfile.TarInfo(path)
            entry.size, entry.mode = len(content), 0o644
            archive.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def source_files(*, tarball=None, config=b"export default {outDir: './public-build'};", extra=None):
    tarball = tarball or package_tarball()
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    dependencies = {"astro": "5.0.0"}
    package = {
        "name": "synthetic-site",
        "version": "1.0.0",
        "dependencies": dependencies,
        "scripts": {
            "build": "astro build",
            "prebuild": "node -e \"require('fs').writeFileSync('lifecycle-ran','ran')\"",
            "postbuild": "node -e \"require('fs').writeFileSync('lifecycle-ran','ran')\"",
        },
    }
    lock = {
        "name": "synthetic-site",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "synthetic-site", "version": "1.0.0", "dependencies": dependencies},
            "node_modules/astro": {
                "version": "5.0.0",
                "resolved": "https://registry.npmjs.org/astro/-/astro-5.0.0.tgz",
                "integrity": integrity,
                "bin": {"astro": "bin.cjs"},
                "hasInstallScript": True,
            },
        },
    }
    return {
        "astro.config.mjs": config,
        "package.json": json.dumps(package).encode(),
        "package-lock.json": json.dumps(lock).encode(),
        "src/pages/index.astro": b"<html><title>Synthetic page</title></html>",
        "src/content/blog/example.md": b"---\ntitle: Example\n---\nText\n",
        "src/content.config.ts": b"export const collections = {};",
        **(extra or {}),
    }


def astro_source(**kwargs):
    files = source_files(**kwargs)
    extension, previous = _source()
    snapshot = replace(previous.inventory.snapshot, content_path="src/pages/index.astro")
    entries = tuple(
        GitHubTreeEntry(path, "100644", "blob", _sha(content))
        for path, content in sorted(files.items())
    )
    checkout = GitHubRepositoryCheckout(
        GitHubRepositoryInventory(snapshot, previous.inventory.tree_sha, entries, False),
        tuple(sorted(files.items())),
    )
    return replace(
        extension,
        framework="astro",
        content_format="astro",
        content_sha=_sha(files[snapshot.content_path]),
    ), checkout
