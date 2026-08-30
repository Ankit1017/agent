"""Install a rights-confirmed local GLB through the shared strict validator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from local_harness.infrastructure.audio2face_avatar import install_avatar  # noqa: E402


def main() -> None:
    """Validate and install the explicit command-line avatar asset."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    validation = install_avatar(args.asset, args.destination, args.max_bytes, args.name)
    print(
        "Validated avatar: "
        f"{validation.meshes} meshes, {validation.vertices} vertices, "
        f"{len(validation.face_controls)} face controls, "
        f"{len(validation.tongue_controls)} optional tongue controls."
    )


if __name__ == "__main__":
    main()
