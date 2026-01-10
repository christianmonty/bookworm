#!/usr/bin/env bash
set -euo pipefail
./tools/cloud-sql-proxy.exe bookworm-482101:us-east1:bookworm-pg --port 5432
