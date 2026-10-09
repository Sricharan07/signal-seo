from datetime import UTC, datetime

from signal_core.github_app import (
    GitHubRepositoryInventory,
    GitHubRepositorySnapshot,
    GitHubTreeEntry,
)
from signal_core.github_format import detect_repository_format

from tests.tooling.astro_build_support import source_files


def _inventory(*paths, content_path="app/page.tsx", truncated=False):
    snapshot = GitHubRepositorySnapshot(
        installation_id=123,
        repository_id=345,
        owner="SignalOwner",
        repository="website",
        full_name="SignalOwner/website",
        private=True,
        default_branch="main",
        base_branch="main",
        base_sha="a" * 40,
        protected=True,
        content_path=content_path,
        credential_expires_at=datetime.now(UTC),
    )
    entries = tuple(
        GitHubTreeEntry(path, mode, "blob", format(index + 1, "040x"))
        for index, (path, mode) in enumerate(paths)
    )
    return GitHubRepositoryInventory(snapshot, "b" * 40, entries, truncated)


def test_next_and_exact_content_file_have_tree_evidence():
    observed = detect_repository_format(
        _inventory(("next.config.mjs", "100644"), ("app/page.tsx", "100644"))
    )
    assert (observed.framework, observed.content_format, observed.coverage) == (
        "nextjs",
        "tsx",
        "complete",
    )
    assert [(entry.path, entry.sha) for entry in observed.marker_entries] == [
        ("next.config.mjs", "0" * 39 + "1")
    ]
    assert observed.content_entry.path == "app/page.tsx"
    assert observed.tree_sha == "b" * 40


def test_astro_hugo_and_eleventy_markers_and_content_formats():
    cases = (
        ("astro.config.mjs", "src/pages/index.astro", "astro", "astro"),
        ("hugo.toml", "content/blog/post.md", "hugo", "markdown"),
        ("eleventy.config.js", "src/post.njk", "eleventy", "nunjucks"),
    )
    for marker, content, framework, content_format in cases:
        metadata = source_files() if framework == "astro" else None
        extra = (("package.json", "100644"), ("package-lock.json", "100644")) if metadata else ()
        observed = detect_repository_format(
            _inventory((marker, "100644"), (content, "100644"), *extra, content_path=content),
            metadata,
        )
        assert (observed.framework, observed.content_format, observed.coverage) == (
            framework,
            content_format,
            "complete",
        )


def test_missing_target_symlink_and_truncated_inventory_never_claim_readiness():
    missing = detect_repository_format(_inventory(("next.config.js", "100644")))
    assert missing.coverage == "partial"
    assert missing.content_entry is None
    symlink = detect_repository_format(
        _inventory(("next.config.js", "100644"), ("app/page.tsx", "120000"))
    )
    assert symlink.content_format == "unknown"
    assert symlink.coverage == "partial"
    truncated = detect_repository_format(
        _inventory(("next.config.js", "100644"), ("app/page.tsx", "100644"), truncated=True)
    )
    assert truncated.framework == "unknown"
    assert truncated.coverage == "partial"


def test_conflicting_framework_markers_are_explicitly_ambiguous():
    observed = detect_repository_format(
        _inventory(
            ("next.config.js", "100644"),
            ("astro.config.mjs", "100644"),
            ("app/page.tsx", "100644"),
        )
    )
    assert observed.framework == "ambiguous"
    assert {entry.path for entry in observed.marker_entries} == {
        "next.config.js",
        "astro.config.mjs",
    }
