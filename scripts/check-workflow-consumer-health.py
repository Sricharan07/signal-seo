#!/usr/bin/env python3
"""Fail closed unless the local workflow consumer reports ready."""

import http.client
import json
import os
import sys


def main() -> int:
    try:
        port = int(os.environ.get("SIGNAL_WORKFLOW_HEALTH_PORT", "8081"))
        if not 1024 <= port <= 65535:
            raise ValueError
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", "/health/ready", headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(4096)
        connection.close()
        record = json.loads(body)
        if (
            response.status != 200
            or record.get("schema_version") != 1
            or record.get("service") != "workflow_command_consumer"
            or record.get("probe") != "ready"
            or record.get("status") != "ready"
            or record.get("ready") is not True
        ):
            raise ValueError
    except Exception:
        print("Workflow consumer is not ready.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
