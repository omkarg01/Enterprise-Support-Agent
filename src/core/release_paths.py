"""Release-path rule (DL-D2, DL-Q3): changes to agent behaviour wait for the scheduled, gated release.

A pull request that touches prompts, Jev question sets, thresholds, policies or model config fails
this check unless it carries the `scheduled-release` label (passed in as RELEASE_ALLOWED=true).

Usage: python -m core.release_paths <base-ref>
"""

import fnmatch
import os
import subprocess
import sys

BEHAVIOUR_PATHS = (
    "src/**/prompts/*",
    "src/core/orchestrator/decisions.py",  # Jev question sets (ADP-05)
    "config/thresholds*",
    "config/models*",
    "policies/*",
)


def behaviour_changes(paths):
    """Return the changed paths that alter agent behaviour, sorted."""
    return sorted(p for p in paths if any(fnmatch.fnmatch(p, pattern) for pattern in BEHAVIOUR_PATHS))


def check(paths, allowed):
    """Exit status for CI: 1 when behaviour paths changed outside a scheduled release, else 0."""
    flagged = behaviour_changes(paths)
    if flagged and not allowed:
        print("Behaviour changes must ship in the scheduled release (label 'scheduled-release'):")
        print("\n".join(f"  {p}" for p in flagged))
        return 1
    return 0


def main(argv):
    base = argv[1] if len(argv) > 1 else "origin/main"
    diff = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                          capture_output=True, text=True, check=True)
    return check(diff.stdout.split(), os.environ.get("RELEASE_ALLOWED") == "true")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
