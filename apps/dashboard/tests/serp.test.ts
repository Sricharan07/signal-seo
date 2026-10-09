import assert from "node:assert/strict";
import test from "node:test";

import { decodeEntities, serpCrumb, serpPreview } from "../lib/serp";

test("a metadata change becomes a search-result preview with decoded text", () => {
  const preview = serpPreview(
    "<title>Pricing</title>",
    '<title>Pricing: docs hosting from $0 &amp; up</title>\n<meta name="description" content="Host &quot;versioned&quot; docs.">',
  );
  assert.ok(preview);
  assert.equal(preview.before.title, "Pricing");
  assert.equal(preview.before.description, null);
  assert.equal(preview.after.title, "Pricing: docs hosting from $0 & up");
  assert.equal(preview.after.description, 'Host "versioned" docs.');
  assert.equal(preview.titleChanged, true);
  assert.equal(preview.descriptionChanged, true);
});

test("changes that touch neither the title nor the description have no preview", () => {
  assert.equal(serpPreview("<p>Old</p>", "<p>New</p>"), null);
  assert.equal(serpPreview("<title>Same</title>", "<title>Same</title>"), null);
  assert.equal(serpPreview('<meta name="robots" content="x">', '<meta name="robots" content="y">'), null);
});

test("markup inside a fragment is never treated as HTML and entities are bounded", () => {
  const preview = serpPreview("", "<title>&lt;script&gt;alert(1)&lt;/script&gt;</title>");
  assert.equal(preview?.after.title, "<script>alert(1)</script>");
  assert.equal(decodeEntities("&#x1F600; &#0; &unknown;"), "😀 &#0; &unknown;");
});

test("search crumbs follow the result format", () => {
  assert.equal(serpCrumb("https://docs.example.test/pricing/plans"), "docs.example.test › pricing › plans");
  assert.equal(serpCrumb("not a url"), "not a url");
});
