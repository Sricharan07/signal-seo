"""Synthetic provider behind real durable shared egress, never a public service."""

import base64
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from signal_core.crawl_http import CrawlFetchUnavailable, EgressHttpResult
from signal_core.github_pr_patch import git_blob_sha, git_commit_sha, git_tree_sha

# Imported and collected fixtures must share one synthetic lab journal identity.
SYNTHETIC_JOURNAL_KEY = Ed25519PrivateKey.generate()
SYNTHETIC_JOURNAL_ENCRYPTION_KEY = secrets.token_bytes(64)
JOURNAL_SIGNING_KEY = SYNTHETIC_JOURNAL_KEY
JOURNAL_ENCRYPTION_KEY = SYNTHETIC_JOURNAL_ENCRYPTION_KEY

SOURCE = (
    b'<html><head><title>Signal Guide</title><script type="application/ld+json">'
    b"{bad}</script></head><body><h1>Signal Guide</h1></body></html>"
)


class DeliveryRepositoryDouble:
    def __init__(self, origin):
        self.origin = origin
        self.files = {
            "index.html": SOURCE,
            ".eleventy.js": b"module.exports = {};\n",
            "package.json": (b'{"scripts":{"build":"mkdir -p _site && cp *.html _site/"}}'),
        }
        self.base_sha = "a" * 40
        self.trees = {git_tree_sha(self.files): self.files.copy()}
        self.base_tree = git_tree_sha(self.files)
        self.commits = {self.base_sha: {"sha": self.base_sha, "tree": {"sha": self.base_tree}}}
        self.refs = {}
        self.pulls = []
        self.calls = []
        self.lost_step = None
        self.after_write = None
        self.deployed = True
        self.protected = True
        self.delivery_time = datetime.now(UTC) - timedelta(seconds=20)

    def entries(self, files):
        return [
            {"path": path, "mode": "100644", "type": "blob", "sha": git_blob_sha(body)}
            for path, body in sorted(files.items())
        ]

    def request(self, outbound, *, policy):
        self.calls.append(outbound)
        path = urlsplit(outbound.url).path
        repo = "/repos/SignalOwner/website"
        body = json.loads(outbound.body) if outbound.body else None
        status = 201 if outbound.method == "POST" else 200
        media = "application/json"
        step = None
        if not outbound.url.startswith("https://api.github.com/"):
            content_path = path.lstrip("/") or "index.html"
            files = self.files
            if self.pulls:
                tree = self.commits[self.pulls[0]["head"]["sha"]]["tree"]["sha"]
                files = self.trees[tree]
            result = files.get(content_path, b"Not found")
            status = 200 if content_path in files else 404
            media = "text/html"
        elif path.endswith("/access_tokens"):
            result = {
                "token": "t" * 32,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "permissions": {**body["permissions"], "metadata": "read"},
                "repository_selection": "selected",
                "repositories": [{"id": 245, "full_name": "SignalOwner/website"}],
            }
        elif path == "/app/installations/9342":
            result = {
                "id": 9342,
                "app_id": 123,
                "suspended_at": None,
                "repository_selection": "selected",
                "permissions": {"contents": "write", "metadata": "read", "pull_requests": "write"},
            }
        elif path == repo:
            result = {
                "id": 245,
                "full_name": "SignalOwner/website",
                "private": True,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            }
        elif path == repo + "/branches/main":
            result = {"name": "main", "commit": {"sha": self.base_sha}, "protected": self.protected}
        elif path == repo + "/git/trees" and outbound.method == "POST":
            step = "tree"
            files = self.trees[body["base_tree"]].copy()
            for item in body["tree"]:
                files[item["path"]] = item["content"].encode()
            digest = git_tree_sha(files)
            self.trees[digest] = files
            result = {"sha": digest}
        elif path == repo + "/git/commits" and outbound.method == "POST":
            step = "commit"
            digest = git_commit_sha(
                tree_sha=body["tree"],
                base_sha=body["parents"][0],
                message=body["message"],
                created_at=datetime.fromisoformat(body["author"]["date"]),
            )
            result = {
                "sha": digest,
                "tree": {"sha": body["tree"]},
                "parents": [{"sha": item} for item in body["parents"]],
                "message": body["message"],
            }
            self.commits[digest] = result
        elif "/git/commits/" in path:
            digest = path.rsplit("/", 1)[-1]
            if digest == "b" * 40 and self.pulls:
                result = {"sha": digest, "tree": self.commits[self.pulls[0]["head"]["sha"]]["tree"]}
            else:
                result = self.commits.get(digest)
                status = 200 if result else 404
        elif "/git/trees/" in path:
            digest = path.rsplit("/", 1)[-1]
            files = self.trees.get(digest)
            result = (
                {"sha": digest, "truncated": False, "tree": self.entries(files)} if files else {}
            )
            status = 200 if files else 404
        elif "/git/blobs/" in path:
            digest = path.rsplit("/", 1)[-1]
            content = next(
                content for content in self.files.values() if git_blob_sha(content) == digest
            )
            result = {
                "sha": digest,
                "size": len(content),
                "encoding": "base64",
                "content": base64.b64encode(content).decode(),
            }
        elif path == repo + "/git/refs" and outbound.method == "POST":
            step = "branch"
            self.refs[body["ref"]] = body["sha"]
            result = {"ref": body["ref"], "object": {"type": "commit", "sha": body["sha"]}}
        elif "/git/ref/heads/" in path:
            ref = "refs/heads/" + path.split("/git/ref/heads/", 1)[1]
            result = (
                {"ref": ref, "object": {"type": "commit", "sha": self.refs[ref]}}
                if ref in self.refs
                else {}
            )
            status = 200 if ref in self.refs else 404
        elif path == repo + "/pulls" and outbound.method == "POST":
            step = "pr"
            result = {
                "number": 42,
                "html_url": "https://github.com/SignalOwner/website/pull/42",
                "head": {
                    "ref": body["head"],
                    "sha": self.refs["refs/heads/" + body["head"]],
                    "repo": {"id": 245},
                },
                "base": {"ref": body["base"], "sha": self.base_sha, "repo": {"id": 245}},
                "body": body["body"],
                "state": "open",
            }
            self.pulls.append(result)
        elif path == repo + "/pulls":
            result = self.pulls
        elif path == repo + "/pulls/42":
            result = {
                **self.pulls[0],
                "merged": True,
                "state": "closed",
                "merge_commit_sha": "b" * 40,
                "merged_at": self.delivery_time.isoformat(),
            }
        elif path.endswith("/check-runs"):
            head = path.split("/commits/")[1].split("/")[0]
            result = {
                "total_count": 1,
                "check_runs": [
                    {"id": 91, "head_sha": head, "status": "completed", "conclusion": "success"}
                ],
            }
        elif path.endswith("/status"):
            result = {
                "sha": path.split("/commits/")[1].split("/")[0],
                "total_count": 0,
                "statuses": [],
            }
        elif path == repo + "/deployments":
            result = (
                [
                    {
                        "id": 81,
                        "sha": "b" * 40,
                        "environment": "production",
                        "production_environment": True,
                        "transient_environment": False,
                        "creator": {"id": 56},
                        "created_at": (self.delivery_time + timedelta(seconds=5)).isoformat(),
                    }
                ]
                if self.deployed
                else []
            )
        elif path == repo + "/deployments/81/statuses":
            result = [
                {
                    "id": 82,
                    "state": "success",
                    "environment": "production",
                    "environment_url": self.origin + "/",
                    "deployment_url": "https://api.github.com" + repo + "/deployments/81",
                    "creator": {"id": 56},
                    "created_at": (self.delivery_time + timedelta(seconds=10)).isoformat(),
                }
            ]
        else:
            raise AssertionError(f"Unimplemented synthetic route: {path}")
        if step and self.after_write:
            self.after_write(step)
        if step is not None and step == self.lost_step:
            self.lost_step = None
            raise CrawlFetchUnavailable("Synthetic response lost after effect.")
        encoded = result if isinstance(result, bytes) else json.dumps(result).encode()
        time.sleep(0.005)
        return EgressHttpResult(
            1,
            outbound.url,
            outbound.url,
            outbound.method,
            "fetched",
            status,
            media,
            (("content-type", media),),
            "8.8.8.8",
            encoded,
            sha256(encoded).hexdigest(),
            len(encoded),
            5,
        )
