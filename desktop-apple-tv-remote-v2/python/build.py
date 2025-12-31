#!/usr/bin/env python3
"""
Build script for creating PyInstaller binaries for the pyatv-server sidecar.

Uses pyatv-server.spec for optimized builds with:
- Comprehensive hidden imports for pyatv/zeroconf/cryptography
- Excludes for unused modules (tkinter, numpy, etc.)
- Platform-specific optimizations (UPX on Windows, strip on all)
- Tauri-compatible binary naming
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SPEC_FILE = SCRIPT_DIR / "pyatv-server.spec"
DIST_DIR = SCRIPT_DIR / "dist"
BUILD_DIR = SCRIPT_DIR / "build"
TAURI_BINARIES = SCRIPT_DIR.parent / "src-tauri" / "binaries"


def get_target_triple() -> str:
    """Get the Rust-style target triple for the current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    arch_map = {
        'arm64': 'aarch64',
        'aarch64': 'aarch64',
        'x86_64': 'x86_64',
        'amd64': 'x86_64',
    }

    if system == "darwin":
        arch = arch_map.get(machine, 'x86_64')
        return f"{arch}-apple-darwin"
    elif system == "linux":
        arch = arch_map.get(machine, 'x86_64')
        return f"{arch}-unknown-linux-gnu"
    elif system == "windows":
        return "x86_64-pc-windows-msvc"
    else:
        raise ValueError(f"Unsupported platform: {system} {machine}")


def get_binary_name() -> str:
    """Get platform-specific binary name with extension."""
    target = get_target_triple()
    base_name = f"pyatv-server-{target}"
    if platform.system().lower() == "windows":
        return f"{base_name}.exe"
    return base_name


def clean_build_dirs():
    """Clean previous build artifacts."""
    for dir_path in [DIST_DIR, BUILD_DIR]:
        if dir_path.exists():
            print(f"Cleaning: {dir_path}")
            shutil.rmtree(dir_path)


def build():
    """Build the pyatv-server binary using PyInstaller spec file."""
    target_triple = get_target_triple()
    output_name = get_binary_name()

    print(f"=" * 60)
    print(f"Building pyatv-server for: {target_triple}")
    print(f"Output binary: {output_name}")
    print(f"=" * 60)

    # Verify spec file exists
    if not SPEC_FILE.exists():
        print(f"ERROR: Spec file not found: {SPEC_FILE}")
        sys.exit(1)

    # Clean previous builds
    clean_build_dirs()

    # Ensure output directories exist
    DIST_DIR.mkdir(exist_ok=True)
    TAURI_BINARIES.mkdir(parents=True, exist_ok=True)

    # PyInstaller command using spec file
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--clean",
        "--noconfirm",
        str(SPEC_FILE),
    ]

    print(f"\nRunning: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))

    if result.returncode != 0:
        print("\n" + "=" * 60)
        print("BUILD FAILED!")
        print("=" * 60)
        sys.exit(1)

    # Find the built binary (spec file generates platform-specific name)
    src_binary = DIST_DIR / output_name
    
    # If not found with extension, try without (PyInstaller adds .exe on Windows)
    if not src_binary.exists() and platform.system().lower() == "windows":
        src_binary = DIST_DIR / output_name.replace(".exe", "")
    
    if not src_binary.exists():
        # Try to find any pyatv-server binary in dist
        candidates = list(DIST_DIR.glob("pyatv-server*"))
        if candidates:
            src_binary = candidates[0]
            print(f"Found binary: {src_binary.name}")

    if not src_binary.exists():
        print(f"ERROR: Binary not found in {DIST_DIR}")
        print(f"Contents: {list(DIST_DIR.iterdir())}")
        sys.exit(1)

    # Get binary size
    size_bytes = src_binary.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    # Copy to Tauri binaries directory
    dst_binary = TAURI_BINARIES / output_name
    shutil.copy2(src_binary, dst_binary)
    
    # Make executable (Unix-like systems)
    if platform.system().lower() != "windows":
        os.chmod(dst_binary, 0o755)

    print("\n" + "=" * 60)
    print("BUILD SUCCESSFUL!")
    print("=" * 60)
    print(f"Binary: {dst_binary}")
    print(f"Size: {size_mb:.1f} MB ({size_bytes:,} bytes)")
    
    # Size warning
    if size_mb > 30:
        print(f"\nWARNING: Binary size ({size_mb:.1f} MB) exceeds 30 MB target")
    else:
        print(f"\n[OK] Binary size is within 30 MB target")

    return dst_binary


def verify_binary(binary_path: Path) -> bool:
    """Basic verification that the binary was created correctly."""
    if not binary_path.exists():
        print(f"Verification failed: Binary not found at {binary_path}")
        return False
    
    # Check it's executable and has reasonable size
    size_mb = binary_path.stat().st_size / (1024 * 1024)
    if size_mb < 1:
        print(f"Verification failed: Binary too small ({size_mb:.1f} MB)")
        return False
    
    print(f"[OK] Binary exists and has valid size ({size_mb:.1f} MB)")
    return True


if __name__ == "__main__":
    binary = build()
    if not verify_binary(binary):
        sys.exit(1)
