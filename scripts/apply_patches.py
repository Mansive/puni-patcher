#!/usr/bin/env python3
"""
apply_patches.py - Apply patches to an existing eden repository.

Usage:
    python apply_patches.py <path-to-eden-repo>

This script:
1. Verifies the eden repo is at the expected base commit
2. Creates a 'patched-release' branch
3. Applies all patches in order using git am
"""

import subprocess
import sys
from pathlib import Path


# Auto-detect paths relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
PATCH_REPO = SCRIPT_DIR.parent
PATCHES_DIR = PATCH_REPO / "patches"
BASE_COMMIT_FILE = PATCH_REPO / "BASE_COMMIT.txt"


def run_git(*args, cwd=None, capture=False, check=True):
    """Run a git command, print it, and optionally capture output."""
    cmd = ["git", *args]
    print(f"  > {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=capture,
    )

    if check and result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}")
        sys.exit(1)

    return result


def print_header(step: int, total: int, message: str):
    """Print a step header like [1/4] Message..."""
    print(f"[{step}/{total}] {message}")


def load_base_commit() -> str:
    """Load the expected base commit hash from BASE_COMMIT.txt."""
    if not BASE_COMMIT_FILE.exists():
        sys.exit(f"Error: {BASE_COMMIT_FILE} not found!")

    commit = BASE_COMMIT_FILE.read_text(encoding="utf-8").strip()

    if not commit or len(commit) < 7:
        sys.exit("Error: BASE_COMMIT.txt is empty or invalid!")

    return commit


def validate_eden_repo(eden_repo: Path, expected_commit: str):
    """Verify the eden repo exists and is at the expected commit."""
    if not (eden_repo / ".git").exists():
        sys.exit(
            f"Error: '{eden_repo}' is not a git repository!\n\n"
            f"Usage:\n"
            f"  python {Path(__file__).name} <path-to-eden-repo>"
        )

    # Check if expected commit exists in the repo
    result = run_git(
        "cat-file",
        "-t",
        expected_commit,
        cwd=eden_repo,
        capture=True,
        check=False,
    )

    if result.returncode != 0:
        sys.exit(
            f"Error: Commit {expected_commit[:12]} not found in repository.\n"
            f"  Try running 'git fetch' in your eden repo to update."
        )


def main():
    # Parse arguments
    if len(sys.argv) < 2:
        script_name = Path(__file__).name
        sys.exit(
            f"Usage: python {script_name} <path-to-eden-repo>\n\n"
            f"Example:\n"
            f"  python {script_name} ../eden\n"
            f"  python {script_name} C:\\path\\to\\eden"
        )

    eden_repo = Path(sys.argv[1]).resolve()

    print(f"Eden repo:   {eden_repo}")
    print(f"Patch repo:  {PATCH_REPO}")
    print()

    # Step 1: Load and verify base commit
    print_header(1, 4, "Loading BASE_COMMIT.txt...")
    expected_commit = load_base_commit()
    print(f"  Expected commit: {expected_commit[:12]}...")

    validate_eden_repo(eden_repo, expected_commit)

    # Step 2: Checkout the base commit
    print_header(2, 4, "Checking out base commit...")
    run_git("checkout", "-f", expected_commit, cwd=eden_repo)
    run_git("clean", "-fdx", cwd=eden_repo)

    # Step 3: Create patched branch
    print_header(3, 4, "Creating patched-release branch...")
    # Delete branch if exists (ignore errors)
    subprocess.run(
        ["git", "branch", "-D", "patched-release"],
        cwd=eden_repo,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    run_git("checkout", "-b", "patched-release", cwd=eden_repo)

    # Step 4: Apply patches
    print_header(4, 4, "Applying patches...")
    patch_files = sorted(PATCHES_DIR.glob("*.patch"))

    if not patch_files:
        print("  No patches found in patches/ directory.")
        print("Done! (No patches to apply)")
        return

    print(f"  Found {len(patch_files)} patch(es)")

    # Use --3way for better conflict resolution
    result = subprocess.run(
        ["git", "am", "--3way"] + [str(p) for p in patch_files],
        cwd=eden_repo,
    )

    if result.returncode != 0:
        print("\n" + "=" * 60)
        print("ERROR: Patch application failed!")
        print("=" * 60)
        print(f"To abort and reset: cd {eden_repo} && git am --abort")
        print("To resolve manually: fix conflicts, git add, git am --continue")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("SUCCESS! All patches applied.")
    print("=" * 60)
    print(f"Source is ready in: {eden_repo}")
    print("You can now build following standard eden instructions.")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
