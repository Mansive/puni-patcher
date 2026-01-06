#!/usr/bin/env python3
"""
export_patches.py - Generate patches from your fork.

Usage:
    python export_patches.py [path-to-repo]

If no path is provided, exports patches from the current directory.
"""

import subprocess
import sys
from pathlib import Path

UPSTREAM_REMOTE = "gitlab"
UPSTREAM_BRANCH = "master"

# Auto-detect paths relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
PATCH_REPO = SCRIPT_DIR.parent
PATCHES_DIR = PATCH_REPO / "patches"
PREVIEW_DIFF = PATCH_REPO / "preview.diff"
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
        sys.exit(f"Command failed with exit code {result.returncode}")

    return result


def print_header(step: int, total: int, message: str):
    """Print a step header like [1/5] Message..."""
    print(f"[{step}/{total}] {message}")


def print_done(patch_count: int):
    """Print final success message."""
    print()
    print("=" * 60)
    print("DONE!")
    print("=" * 60)
    print(f"  Patches exported: {patch_count}")
    print(f"  Location: {PATCHES_DIR}")
    print()
    print("Next steps:")
    print("  1. Review the generated patches")
    print("  2. Commit and push to puni-patcher repo")


def clean_old_patches():
    """Remove existing .patch files to avoid stale patches."""
    print_header(1, 5, "Cleaning old patches...")
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)

    old_patches = list(PATCHES_DIR.glob("*.patch"))
    for f in old_patches:
        f.unlink()
        print(f"  Removed: {f.name}")

    if not old_patches:
        print("  (No old patches to remove)")


def generate_patches(eden_repo: Path):
    """Run git format-patch to create individual patch files."""
    print_header(2, 5, "Generating patches (git format-patch)...")
    run_git(
        "format-patch",
        "--no-numbered",
        "--no-signature",
        "--start-number",
        "1",
        "-o",
        str(PATCHES_DIR),
        f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}..HEAD",
        cwd=eden_repo,
    )


def generate_preview_diff(eden_repo: Path):
    """Generate a single combined diff for easy review."""
    print_header(3, 5, "Generating preview.diff...")
    diff_range = f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}..HEAD"

    with open(PREVIEW_DIFF, "w", encoding="utf-8") as f:
        subprocess.run(
            ["git", "diff", diff_range],
            stdout=f,
            cwd=eden_repo,
        )
    print(f"  Written: {PREVIEW_DIFF}")


def generate_series_file() -> list[str]:
    """Generate patches/series listing all patches in order."""
    print_header(4, 5, "Generating patches/series...")

    patch_files = sorted(f.name for f in PATCHES_DIR.glob("*.patch"))
    series_file = PATCHES_DIR / "series"

    with open(series_file, "w", encoding="utf-8") as f:
        for patch in patch_files:
            f.write(f"{patch}\n")

    print(f"  Written: {series_file} ({len(patch_files)} patches)")
    return patch_files


def update_base_commit(eden_repo: Path):
    """Update BASE_COMMIT.txt with the current upstream commit hash."""
    print_header(5, 5, "Updating BASE_COMMIT.txt...")

    result = run_git(
        "rev-parse",
        f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}",
        cwd=eden_repo,
        capture=True,
        check=False,
    )

    if result.returncode != 0:
        print("  Warning: Could not get upstream commit hash")
        return

    new_commit = result.stdout.strip()

    BASE_COMMIT_FILE.write_text(new_commit + "\n", encoding="utf-8")
    print(f"  Updated commit: {new_commit}")


def validate_eden_repo(eden_repo: Path):
    """Verify the eden repo exists and has the gitlab remote."""
    if not (eden_repo / ".git").exists():
        sys.exit(
            f"Error: '{eden_repo}' is not a git repository!\n\n"
            f"Usage:\n"
            f"  python {Path(__file__).name} [path-to-eden-repo]\n"
            f"  python {Path(__file__).name}  # uses current directory"
        )

    result = run_git(
        "remote",
        "get-url",
        UPSTREAM_REMOTE,
        cwd=eden_repo,
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(
            f"Error: No '{UPSTREAM_REMOTE}' remote found in eden repo.\n"
            f"  Add a remote named '{UPSTREAM_REMOTE}' pointing to your upstream."
        )


def main():
    # Parse arguments
    if len(sys.argv) >= 2:
        eden_repo = Path(sys.argv[1]).resolve()
    else:
        eden_repo = Path.cwd()

    print(f"Eden repo:   {eden_repo}")
    print(f"Patch repo:  {PATCH_REPO}")
    print()

    validate_eden_repo(eden_repo)

    clean_old_patches()
    generate_patches(eden_repo)
    generate_preview_diff(eden_repo)
    patch_files = generate_series_file()
    update_base_commit(eden_repo)

    print_done(len(patch_files))


if __name__ == "__main__":
    main()
