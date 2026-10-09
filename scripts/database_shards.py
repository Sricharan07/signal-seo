"""Concurrent isolated database lab workers, with complete partition evidence."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from database_lab import (
    IMAGE,
    ROOT,
    LabError,
    isolated_postgres,
    provision,
    source_hashes,
    summarize_junit,
)
from lab_runtime import runtime_root

BRAIN_TEST = (
    "tests/control_plane/test_business_brain_extraction.py::"
    "test_real_sources_shared_egress_injection_and_replay"
)


def validate_partitions(manifests: list[dict], count: int) -> int:
    if len(manifests) != count:
        raise LabError("Missing database shard collection report.")
    expected = manifests[0]["all"]
    if not expected or len(set(expected)) != len(expected):
        raise LabError("Database collection is empty or has duplicate identities.")
    selected = []
    for index, manifest in enumerate(manifests):
        if manifest["all"] != expected or manifest["selected"] != expected[index::count]:
            raise LabError("Database shard collection changed or assignment was incomplete.")
        selected.extend(manifest["selected"])
    if len(selected) != len(expected) or set(selected) != set(expected):
        raise LabError("Database tests must run exactly once across shards.")
    return len(expected)


def combine_junit(paths: list[Path], destination: Path) -> None:
    combined = ET.Element("testsuites")
    for path in paths:
        root = ET.parse(path).getroot()
        if not root.findall(".//testcase"):
            raise LabError("Empty database shard report.")
        if root.tag == "testsuite":
            combined.append(root)
        elif root.tag == "testsuites":
            combined.extend(root.findall("testsuite"))
        else:
            raise LabError("Invalid database shard JUnit root.")
    ET.ElementTree(combined).write(destination, encoding="utf-8", xml_declaration=True)


def worker(index: int, count: int, directory: Path, repeats: int) -> int:
    directory.mkdir(parents=True, exist_ok=False)
    with isolated_postgres() as (admin_dsn, common):
        env, version = provision(admin_dsn, common)
        env.update(
            SIGNAL_DATABASE_TEST_SHARD=str(index),
            SIGNAL_DATABASE_TEST_SHARDS=str(count),
            SIGNAL_DATABASE_COLLECTION_REPORT=str(directory / "collection.json"),
        )
        # A gate-wide basetemp cannot be shared by concurrent pytest workers.
        env["PYTEST_ADDOPTS"] = f"--basetemp={directory / 'pytest-tmp'}"
        migrate = [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"]
        for _ in range(2):
            subprocess.run(migrate, env=env, cwd=ROOT, check=True, timeout=180)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/control_plane",
                "-v",
                f"--junitxml={directory / 'suite.xml'}",
            ],
            env=env,
            cwd=ROOT,
            timeout=1200,
        )
        code = result.returncode
        env.pop("SIGNAL_DATABASE_TEST_SHARD")
        env.pop("SIGNAL_DATABASE_COLLECTION_REPORT")
        for repeat in range(repeats):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    BRAIN_TEST,
                    "-v",
                    f"--junitxml={directory / f'brain-{repeat}.xml'}",
                ],
                env=env,
                cwd=ROOT,
                timeout=180,
            )
            code = code or result.returncode
    (directory / "version.txt").write_text(version)
    return code


def stop_workers(processes) -> None:
    active = [p for p in processes if p.poll() is None]
    for process in active:
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 60
    unconfirmed = False
    for process in active:
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            unconfirmed = True
    if unconfirmed:
        raise LabError("Database worker cleanup unconfirmed after cancellation.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shards",
        type=int,
        choices=range(1, 7),
        default=os.environ.get("SIGNAL_DATABASE_TEST_SHARDS", "3"),
    )
    parser.add_argument("--repeat-brain", type=int, choices=range(0, 21), default=0)
    parser.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--artifact-directory", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.shards <= 6:
        parser.error("Database shards must be between 1 and 6.")
    if args.worker is not None:
        if not 0 <= args.worker < args.shards or args.artifact_directory is None:
            parser.error("Invalid internal database worker arguments.")
        return worker(args.worker, args.shards, args.artifact_directory, args.repeat_brain)
    if args.artifact_directory is not None:
        parser.error("Artifact directory is internal to the database lab.")
    hashes = source_hashes()
    directory = runtime_root(ROOT) / "database-tests"
    directory.mkdir(parents=True, exist_ok=True)
    invocation = Path(tempfile.mkdtemp(prefix="run-", dir=directory))
    processes, logs = [], []
    completed = False
    started = time.monotonic()
    try:
        for index in range(args.shards):
            log = (invocation / f"shard-{index}.log").open("w")
            logs.append(log)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "scripts/run-database-tests.py",
                        "--shards",
                        str(args.shards),
                        "--worker",
                        str(index),
                        "--artifact-directory",
                        str(invocation / str(index)),
                        "--repeat-brain",
                        str(args.repeat_brain if index == 0 else 0),
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            )
        deadline = time.monotonic() + 1800
        while any(p.poll() is None for p in processes):
            if time.monotonic() >= deadline:
                raise LabError("Database shards exceeded their bounded deadline.")
            time.sleep(0.1)
        for index, process in enumerate(processes):
            print(
                f"Database shard {index + 1}/{args.shards}: exit {process.returncode}", flush=True
            )
            if process.returncode:
                print((invocation / f"shard-{index}.log").read_text(), flush=True)
        if hashes != source_hashes():
            raise LabError("Source changed during verification; rerun against stable source.")
        manifests = [
            json.loads((invocation / str(i) / "collection.json").read_text())
            for i in range(args.shards)
        ]
        collected = validate_partitions(manifests, args.shards)
        suite_paths = [invocation / str(i) / "suite.xml" for i in range(args.shards)]
        combine_junit(suite_paths, directory / "suite.xml")
        suite = summarize_junit(directory / "suite.xml")
        if len(suite["tests"]) != collected:
            raise LabError("Database JUnit count does not match exact collection coverage.")
        repeat_paths = [invocation / "0" / f"brain-{i}.xml" for i in range(args.repeat_brain)]
        combine_junit([*suite_paths, *repeat_paths], directory / "latest.xml")
        report = {
            "schema_version": 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "database": (invocation / "0/version.txt").read_text(),
            "image": IMAGE,
            "python": sys.version.split()[0],
            "source_sha256": hashes,
            "production_authority": False,
            "cleanup": "completed",
            "shards": args.shards,
            "collected": collected,
            "suite_passed": suite["passed"],
            "brain_repetitions": args.repeat_brain,
            "seconds": round(time.monotonic() - started, 3),
            **summarize_junit(directory / "latest.xml"),
        }
        (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"Database: {report['passed']} passed in {report['seconds']}s", flush=True)
        code = int(any(p.returncode for p in processes) or report["passed"] != len(report["tests"]))
        completed = code == 0
        return code
    finally:
        try:
            stop_workers(processes)
        finally:
            for log in logs:
                log.close()
        # Keep failure diagnostics, never remove another invocation's artifacts.
        if completed:
            shutil.rmtree(invocation)
