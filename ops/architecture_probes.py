#!/usr/bin/env python3
"""Architecture probe runner — layer-2 IPC checks with honest work-RPC budgets.

Usage:
  python ops/architecture_probes.py           # JSON to stdout; exit 0 green / 1 red
  python ops/architecture_probes.py --ipc     # IPC layer only (default)

Knobs: UTAH_ARCH_PROBE_LIVENESS_S (default 5), UTAH_ARCH_PROBE_WORK_S (30),
UTAH_ARCH_PROBE_WORK_HEAVY_S (60).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utah import architecture_probes  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] not in ("--ipc", "-h", "--help"):
        print(f"unknown arg: {args[0]!r}", file=sys.stderr)
        return 2
    payload = architecture_probes.run_ipc_layer()
    print(json.dumps(payload, default=repr))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
