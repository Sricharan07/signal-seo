"""Tiny generated-date Astro source fixture; synthetic package is not the Astro compiler."""

import hashlib
from dataclasses import replace

from signal_core.candidate_build_service import CandidateBuildRecord

from tests.tooling.astro_build_support import CLI, astro_source, package_tarball

DESCRIPTION = (
    "Calendar information about the date and the day with more information about this page."
)
FIXTURE_FILES = {
    "src/pages/index.astro": (
        b'---\nimport Layout from "../layouts/Calendar.astro";\nconst title = "Calendar";\n'
        b'---\n<Layout title={title}><h1>Calendar Guide</h1><img src="guide.png"></Layout>'
    ),
    "src/pages/dates/[slug].astro": (
        b'---\nimport Layout from "../../layouts/Calendar.astro";\n'
        b'import dates from "../../data/dates.json";\nexport function getStaticPaths() { '
        b"return dates.days.map(date => ({params: {slug: date}, props: {date}})); }\n"
        b'const {date} = Astro.props;\nconst title = "Calendar";\n---\n'
        b"<Layout title={title}><h1>Calendar {date}</h1></Layout>"
    ),
    "src/layouts/Calendar.astro": b"---\nconst {title} = Astro.props;\n---\n"
    b'<html><head><title>{title}</title><meta name="description" content="'
    + DESCRIPTION.encode()
    + b'"></head><body><slot /></body></html>',
    "src/data/dates.json": b'{"days":["2026-01-01","2026-01-02","2026-01-03"]}',
    "src/data/generator.ts": (
        b'export function metadata(date: string) { return {title: "Calendar"}; }'
    ),
    "src/components/Banner.astro": b'---\nconst title = "Calendar";\n---\n<h2>{title}</h2>',
    "src/content/blog/guide.json": b'{"title":"Calendar", "description":"Calendar guide"}',
}

FIXTURE_CLI = (
    CLI[: CLI.index(b"fs.mkdirSync('public-build');")]
    + b"""
const description = 'Calendar information about the date and the day '+
 'with more information about this page.';
const page = (title,body) => '<html><head><title>'+title+'</title>'+
 '<meta name="description" content="'+description+'"></head><body>'+body+'</body></html>';
const title = (path,date) => {
  const source=fs.readFileSync(path,'utf8');
  const match=source.match(/const title = ("[^"]*"|`[^`]*`);/);
  if(!match) process.exit(43);
  return match[1].slice(1,-1).replace('${date}',date||'');
};
fs.mkdirSync('public-build');
fs.writeFileSync('public-build/index.html',page(title('src/pages/index.astro'),
 '<h1>Calendar Guide</h1><img src="guide.png">'));
for(const date of JSON.parse(fs.readFileSync('src/data/dates.json')).days) {
  fs.mkdirSync('public-build/dates/'+date,{recursive:true});
  fs.writeFileSync('public-build/dates/'+date+'/index.html',
   page(title('src/pages/dates/[slug].astro',date),'<h1>Calendar '+date+'</h1>'));
}
fs.mkdirSync('public-build/blog/guide',{recursive:true});
fs.writeFileSync('public-build/blog/guide/index.html',
 page(JSON.parse(fs.readFileSync('src/content/blog/guide.json')).title,'<h1>Calendar Guide</h1>'));
"""
)


def fixture_source():
    tarball = package_tarball(script=FIXTURE_CLI)
    extension, checkout = astro_source(tarball=tarball, extra=FIXTURE_FILES)
    from signal_core.github_pr_patch import git_tree_sha

    tree = git_tree_sha(dict(checkout.files))
    return (
        replace(extension, tree_sha=tree),
        replace(checkout, inventory=replace(checkout.inventory, tree_sha=tree)),
        tarball,
    )


def record(html: dict[str, str], *, patch="d" * 64):
    from uuid import uuid4

    return CandidateBuildRecord(
        uuid4(),
        uuid4(),
        "completed",
        "a" * 40,
        patch,
        "synthetic-toolchain",
        "npm run build",
        exit_class="passed",
        logs_sha256="e" * 64,
        lockfile_sha256="f" * 64,
        built_html=tuple(sorted(html.items())),
        artifacts=tuple(
            (p, hashlib.sha256(s.encode()).hexdigest(), len(s.encode()))
            for p, s in sorted(html.items())
        ),
    )
