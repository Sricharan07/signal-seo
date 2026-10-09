import base64
import hashlib
import io
import json
import tarfile
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from signal_core.astro_source import AstroSourceUnavailable, inspect_astro_source, strict_json
from signal_core.candidate_build import CandidatePolicyRejected, plan_candidate_build
from signal_core.candidate_sandbox import DockerCandidateSandbox, _create_args
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile, validate_profile_request
from signal_core.github_format import detect_repository_format
from signal_core.npm_registry import (
    LockedTarball,
    NpmRegistryCache,
    NpmRegistryUnavailable,
    locked_dependencies,
    verify_tarball,
)

from tests.tooling.astro_build_support import astro_source, package_tarball, source_files


@pytest.mark.parametrize("suffix", ["js", "mjs", "ts", "mts"])
def test_astro_requires_config_dependency_and_lock_and_observes_output_and_collections(suffix):
    extension, checkout = astro_source()
    files = dict(checkout.files)
    config = files.pop("astro.config.mjs")
    files["astro.config." + suffix] = config
    observed = inspect_astro_source(files)
    assert observed.output_directory == "public-build"
    assert observed.collection_entries == ("src/content/blog/example.md",)
    assert observed.collection_configs == ("src/content.config.ts",)
    assessment = detect_repository_format(checkout.inventory, dict(checkout.files))
    assert assessment.framework == "astro"
    assert assessment.output_directory == "public-build"
    assert assessment.collection_entries == observed.collection_entries
    assert extension.candidate_compatible


@pytest.mark.parametrize("missing", ["astro.config.mjs", "package.json", "package-lock.json"])
def test_incomplete_astro_markers_never_claim_a_build_profile(missing):
    _, checkout = astro_source()
    files = dict(checkout.files)
    del files[missing]
    with pytest.raises(AstroSourceUnavailable):
        inspect_astro_source(files)
    assert detect_repository_format(checkout.inventory, files).framework == "unknown"
    assert detect_repository_format(checkout.inventory).framework == "unknown"


@pytest.mark.parametrize(
    "config",
    [
        b"export default () => ({outDir:'dist'});",
        b"export default {outDir: process.env.OUT};",
        b"export default { ...other };",
        b"export default {output:'server'};",
        b"export default {outDir:'../escape'};",
        b"export default {outDir:'src'};",
        b"export default {outDir:'/tmp/dist'};",
        b"export default {outDir:'dist', outDir:'elsewhere'};",
    ],
)
def test_astro_detection_is_not_execution_and_unsupported_configuration_is_unavailable(config):
    extension, checkout = astro_source(config=config)
    observed = detect_repository_format(checkout.inventory, dict(checkout.files))
    assert observed.framework == "astro"
    assert observed.build_unavailable_reason
    with pytest.raises(CandidatePolicyRejected):
        plan_candidate_build(extension, checkout)


def test_define_config_literal_and_custom_collection_source_are_parsed_without_evaluation():
    files = source_files(
        config=(
            b"import {defineConfig} from 'astro/config'; export default defineConfig({"
            b"srcDir:'pages', outDir:'out', integrations:[anything()]});"
        )
    )
    files["pages/content/news.json"] = b"{}"
    observed = inspect_astro_source(files)
    assert observed.output_directory == "out"
    assert observed.collection_entries == ("pages/content/news.json",)


@pytest.mark.parametrize(
    "path",
    [
        "src/pages/index.astro",
        "src/pages/[slug].astro",
        "src/layouts/Layout.astro",
        "src/content/blog/example.md",
        ".github/workflows/ci.yml",
        "package.json",
        "auth/session.ts",
    ],
)
def test_all_astro_delivery_and_protected_path_patches_remain_refused(path):
    extension, checkout = astro_source()
    with pytest.raises(CandidatePolicyRejected, match="ASTRO_DELIVERY_UNAVAILABLE"):
        plan_candidate_build(
            extension, checkout, patch={path: b"changed"}, approved_paths=frozenset({path})
        )


@pytest.mark.parametrize(
    "extra",
    [
        {".npmrc": b"registry=other"},
        {".env.production": b"SECRET=synthetic"},
        {"node_modules/existing.js": b"code"},
    ],
)
def test_host_configuration_and_preinstalled_dependencies_are_not_sandbox_inputs(extra):
    extension, checkout = astro_source(extra=extra)
    with pytest.raises(CandidatePolicyRejected, match="ASTRO_BUILD_PROFILE_UNAVAILABLE"):
        plan_candidate_build(extension, checkout)


def test_closed_registry_manifest_and_tarball_integrity_identity_and_bounds():
    tarball = package_tarball()
    files = source_files(tarball=tarball)
    manifest = locked_dependencies(files["package.json"], files["package-lock.json"])
    assert manifest.lockfile_sha256 == hashlib.sha256(files["package-lock.json"]).hexdigest()
    assert verify_tarball(manifest.tarballs[0], tarball) > 0
    for bad in (tarball + b"changed", b"", b"x" * (5 * 1024 * 1024 + 1)):
        with pytest.raises(NpmRegistryUnavailable, match="INTEGRITY"):
            verify_tarball(manifest.tarballs[0], bad)
    with pytest.raises(NpmRegistryUnavailable, match="IDENTITY"):
        verify_tarball(replace(manifest.tarballs[0], name="other"), tarball)


@pytest.mark.parametrize(
    "key,value",
    [
        ("resolved", "https://other.example.invalid/astro.tgz"),
        ("resolved", "https://registry.npmjs.org/astro/-/astro-5.0.0.tgz?token=synthetic"),
        ("resolved", "file:../astro"),
        ("link", True),
        ("integrity", "sha1-synthetic"),
        ("version", "6.0.0"),
        ("integrity", None),
    ],
)
def test_manifest_rejects_unclosed_dependencies(key, value):
    files = source_files()
    lock = json.loads(files["package-lock.json"])
    lock["packages"]["node_modules/astro"][key] = value
    with pytest.raises(NpmRegistryUnavailable, match="LOCKFILE"):
        locked_dependencies(files["package.json"], json.dumps(lock).encode())


@pytest.mark.parametrize("path", ["package/../escape", "other/package.json"])
def test_integrity_matching_but_unsafe_tarball_is_refused(path):
    tarball = package_tarball(extra={path: b"unsafe"})
    item = LockedTarball(
        "https://registry.npmjs.org/astro/-/astro-5.0.0.tgz",
        "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode(),
        "astro",
        "5.0.0",
    )
    with pytest.raises(NpmRegistryUnavailable, match="TARBALL_REJECTED"):
        verify_tarball(item, tarball)


@pytest.mark.parametrize(
    "change",
    [
        {"method": "POST"},
        {"url": "https://registry.npmjs.org/astro"},
        {"url": "https://registry.npmjs.org:443/astro/-/astro-5.0.0.tgz"},
        {"url": "https://registry.npmjs.org/@scope/astro/-/other-5.0.0.tgz"},
        {"url": "https://registry.npmjs.org/astro/-/astro-5.0.0.tgz#fragment"},
        {"body": b"payload"},
        {"headers": (("authorization", "Bearer synthetic-token"),)},
        {"timeout_seconds": 6},
        {"max_response_bytes": 5 * 1024 * 1024 + 1},
    ],
)
def test_registry_profile_is_read_only_official_canonical_and_bounded(change):
    request = EgressHttpRequest(
        "GET",
        "https://registry.npmjs.org/astro/-/astro-5.0.0.tgz",
        headers=(("accept", "application/octet-stream"),),
        timeout_seconds=5,
        max_response_bytes=5 * 1024 * 1024,
        accepted_media_types=("application/octet-stream", "application/gzip"),
    )
    validate_profile_request(EgressProfile.NPM_REGISTRY, "connector", request, False)
    with pytest.raises(ValueError):
        validate_profile_request(
            EgressProfile.NPM_REGISTRY, "connector", replace(request, **change), False
        )


def test_cache_symlinks_are_rejected_before_writing_outside_worktree(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".runtime").symlink_to(outside, target_is_directory=True)
    with pytest.raises(NpmRegistryUnavailable, match="SCOPE"):
        NpmRegistryCache(worktree_root=worktree)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("document", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]"])
def test_metadata_json_is_strict(document):
    with pytest.raises(AstroSourceUnavailable):
        strict_json(document)


def test_cache_is_required_before_creating_a_sandbox_and_is_reverified(monkeypatch):
    extension, checkout = astro_source()
    plan = plan_candidate_build(extension, checkout)
    monkeypatch.setattr(
        "signal_core.candidate_sandbox._docker",
        lambda *a, **k: pytest.fail("Must not start container"),
    )
    with pytest.raises(CandidatePolicyRejected, match="CACHE_INCOMPLETE"):
        DockerCandidateSandbox().run(plan)
    item = plan.dependency_manifest.tarballs[0]
    key = hashlib.sha256(item.integrity.encode()).hexdigest() + ".tgz"
    with pytest.raises(NpmRegistryUnavailable, match="INTEGRITY"):
        DockerCandidateSandbox().run(replace(plan, dependencies=((key, b"corrupt"),)))
    args = _create_args("synthetic-astro", dependencies=True)
    assert args[args.index("--network") + 1] == "none"
    assert "--mount" not in args and "--volume" not in args
    assert "npm_config_ignore_scripts=true" in args


def test_tarball_links_are_refused_even_with_valid_lockfile_integrity():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        entry = tarfile.TarInfo("package/link")
        entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
        archive.addfile(entry)
    content = buffer.getvalue()
    item = LockedTarball(
        "https://registry.npmjs.org/astro/-/astro-5.0.0.tgz",
        "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode(),
        "astro",
        "5.0.0",
    )
    with pytest.raises(NpmRegistryUnavailable, match="TARBALL_REJECTED"):
        verify_tarball(item, content)


def test_cache_prunes_only_owned_regular_files_and_rejects_symlink_hits(monkeypatch):
    import signal_core.npm_registry as registry

    root = Path(__file__).resolve().parents[2]
    # A fresh checkout (CI or a clean worktree) has no .runtime directory yet.
    (root / ".runtime").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="0126-cache-", dir=root / ".runtime") as directory:
        cache = NpmRegistryCache(worktree_root=Path(directory))
        monkeypatch.setattr(registry, "MAX_CACHE_BYTES", 5)
        first, second = "a" * 64 + ".tgz", "b" * 64 + ".tgz"
        cache._save(first, b"one")
        cache._save(second, b"two")
        assert not (cache.directory / first).exists()
        assert cache._read(second) == b"two"
        (cache.directory / first).symlink_to("../outside")
        with pytest.raises(NpmRegistryUnavailable, match="CACHE_REJECTED"):
            cache._read(first)
        with pytest.raises(NpmRegistryUnavailable, match="CACHE_REJECTED"):
            cache._save("c" * 64 + ".tgz", b"new")
