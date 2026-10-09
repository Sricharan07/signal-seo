from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.crawl_audit import CrawlAuditInput, CrawlAuditUnavailable, detect_crawl_findings


def row(url="https://example.com/", **changes):
    source = CrawlAuditInput(
        run_id=uuid4(),
        manifest_sha256=b"a" * 32,
        manifest_coverage="complete",
        discovered_count=1,
        frontier_id=uuid4(),
        url_id=uuid4(),
        url=url,
        depth=0,
        discovered_from_url_id=None,
        settlement_id=uuid4(),
        terminal_state="fetched",
        http_status=200,
        robots_snapshot_id=uuid4(),
        page_record_id=uuid4(),
        title="Home",
        meta_description="Useful description",
        canonical_url=url,
        headings=((1, "Welcome"),),
        structured_data_types=("WebPage",),
        internal_links=(),
        parse_error_count=0,
        output_truncated=False,
        body_sha256=b"b" * 32,
    )
    return replace(source, **changes)


def test_complete_well_formed_page_has_no_invented_findings():
    source = row()
    report = detect_crawl_findings(uuid4(), (source,))
    assert report.findings == ()
    assert report.coverage == {
        "pages": "complete",
        "metadata": "observed",
        "sitemaps": "not_assessed",
        "robots": "observed",
    }
    assert report.input_count == 1


def test_deterministic_page_rules_bind_exact_immutable_page_record():
    source = row(
        title=None,
        meta_description=None,
        canonical_url="https://other.example/path",
        headings=(),
        parse_error_count=1,
    )
    manifest = uuid4()
    first = detect_crawl_findings(manifest, (source,))
    replay = detect_crawl_findings(manifest, (source,))
    assert first == replay
    assert {finding.key for finding in first.findings} == {
        "metadata.title.missing",
        "metadata.meta_description.missing",
        "headings.h1.missing",
        "canonical.external",
        "structured_data.invalid_json_ld",
    }
    assert {finding.source_id for finding in first.findings} == {source.page_record_id}
    assert all(finding.resource_locator == source.url for finding in first.findings)


def test_duplicate_title_and_broken_discovered_link_are_evidence_bound():
    home = row(discovered_count=3, internal_links=("https://example.com/gone",))
    second = row(
        "https://example.com/second",
        run_id=home.run_id,
        manifest_sha256=home.manifest_sha256,
        discovered_count=3,
    )
    gone = row(
        "https://example.com/gone",
        run_id=home.run_id,
        manifest_sha256=home.manifest_sha256,
        discovered_count=3,
        depth=1,
        discovered_from_url_id=home.url_id,
        terminal_state="http_error",
        http_status=404,
        page_record_id=None,
        title=None,
        body_sha256=None,
    )
    report = detect_crawl_findings(uuid4(), (home, second, gone))
    assert [finding.key for finding in report.findings].count("metadata.title.duplicate") == 2
    assert [finding.key for finding in report.findings].count(
        "metadata.meta_description.duplicate"
    ) == 2
    broken = next(
        finding for finding in report.findings if finding.key == "links.internal.not_found"
    )
    assert broken.source_kind == "settlement"
    assert broken.source_id == gone.settlement_id
    assert broken.resource_locator == gone.url


def test_missing_alt_requires_committed_page_image_observation():
    source = row(missing_alt_images=("/team.png",))
    report = detect_crawl_findings(uuid4(), (source,))
    finding = next(item for item in report.findings if item.key == "images.alt.missing")
    assert finding.source_id == source.page_record_id
    assert finding.resource_locator == source.url
    assert not any(
        item.key == "images.alt.missing"
        for item in detect_crawl_findings(
            uuid4(), (replace(source, missing_alt_images=()),)
        ).findings
    )


def test_robots_denial_is_partial_and_only_root_blockage_is_a_finding():
    blocked = row(
        terminal_state="robots_denied",
        page_record_id=None,
        body_sha256=None,
        http_status=None,
        manifest_coverage="partial",
    )
    report = detect_crawl_findings(uuid4(), (blocked,))
    assert [finding.key for finding in report.findings] == ["robots.homepage.denied"]
    assert report.coverage["robots"] == "partial"
    assert report.coverage["pages"] == "partial"


def test_missing_manifest_evidence_never_turns_into_zero_findings():
    source = row(discovered_count=2)
    with pytest.raises(CrawlAuditUnavailable, match="cover"):
        detect_crawl_findings(uuid4(), (source,))
    with pytest.raises(CrawlAuditUnavailable, match="manifest"):
        detect_crawl_findings(uuid4(), ())
    with pytest.raises(CrawlAuditUnavailable, match="incomplete"):
        detect_crawl_findings(uuid4(), (replace(source, discovered_count=1, page_record_id=None),))
