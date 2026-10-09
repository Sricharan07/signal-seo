#!/bin/sh
set -eu
test "$(id -u)" -eq 0
cd "$(dirname "$0")/../../.."
context=$(pwd)
# The VM's legacy builder needs the same explicit allowlist at the context root.
for name in api dashboard ingress; do
  install -m 0644 "deploy/integration-test/application/Dockerfile.$name.dockerignore" .dockerignore
  docker build --memory=1536m --memory-swap=1536m -f "deploy/integration-test/application/Dockerfile.$name" -t "signal-test-$name:20260930" "$context"
done
