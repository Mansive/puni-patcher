<p align="center">
    <img src="assets/A17_Blue_Puni_Mona_Lisa.webp" alt="Mona Lisa Puni" height="150">
</p>
<h1 align="center">Puni Patcher</h1>

Patches to support NCE (Native Code Execution) hooking support to Eden on Android. While it's relatively simple to hook the Dynarmic backend, Agent's standard hooking methods will crash or freeze the emulator on the NCE backend. These patches serve to give Agent more control over Eden for stable NCE hooking.

For more information, see [`NCEHooks.md`](docs/NCEHooks.md).

**The patches are very experimental!** Expect crashes and freezes, especially since this project was mostly accomplished through vibecoding.

## Prerequisites

1. Install [Git](https://git-scm.com/install/) onto your system
2. Obtain the Eden emulator source code
3. Checkout the commit specified in [`BASE_COMMIT.txt`](BASE_COMMIT.txt)
4. Run the apply script pointing to your eden directory

## Quick Start

```bash
python scripts/apply_patches.py <path-to-your-eden-repo>
```

This will:

1. Verify the repo is at the expected [base commit](BASE_COMMIT.txt)
2. Create a `patched-release` branch
3. Apply all patches in order

## Files

- `patches/` - The actual `.patch` files (generated via `git format-patch`)
- `preview.diff` - Combined diff showing all changes for ease of viewing
- `BASE_COMMIT.txt` - The commit hash these patches apply to
- `scripts/apply_patches.py` - Script to apply patches to your eden repo
- `scripts/export_patches.py` - Script to regenerate patches for Puni Patcher

## If Patches Fail to Apply

If `git am` fails mid-apply:

```bash
cd <your-eden-repo>
git am --abort   # Reset to clean state
```

Then verify your repo is at the correct base commit.

## Building

After patches are applied, follow the standard Eden build instructions.

## NCE Hooks Overview

![High-level Diagram](docs/nce_hooks_diagram.svg)