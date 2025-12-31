# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for pyatv-server sidecar.

Optimized for:
- Cross-platform builds (macOS ARM64/Intel, Windows, Linux)
- Tauri-compatible binary naming
- Minimal binary size (<30MB target)
- Complete pyatv/zeroconf/cryptography support
"""

import platform
import sys
from pathlib import Path

# Try to import PyInstaller utilities for collect_submodules
try:
    from PyInstaller.utils.hooks import collect_submodules, collect_data_files
    USE_COLLECT = True
except ImportError:
    USE_COLLECT = False

# =============================================================================
# PLATFORM DETECTION
# =============================================================================

def get_target_triple():
    """Get Tauri-compatible target triple for current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # Architecture mapping
    arch_map = {
        'arm64': 'aarch64',
        'aarch64': 'aarch64',
        'x86_64': 'x86_64',
        'amd64': 'x86_64',
        'i386': 'i686',
        'i686': 'i686',
    }
    
    # OS mapping
    os_map = {
        'darwin': 'apple-darwin',
        'linux': 'unknown-linux-gnu',
        'windows': 'pc-windows-msvc',
    }
    
    arch = arch_map.get(machine, machine)
    os_triple = os_map.get(system, f'unknown-{system}')
    
    return f'{arch}-{os_triple}'


def get_binary_name():
    """Get platform-specific binary name with extension."""
    target = get_target_triple()
    base_name = f'pyatv-server-{target}'
    
    if platform.system().lower() == 'windows':
        return f'{base_name}.exe'
    return base_name


TARGET_TRIPLE = get_target_triple()
BINARY_NAME = get_binary_name()
IS_WINDOWS = platform.system().lower() == 'windows'
IS_MACOS = platform.system().lower() == 'darwin'
IS_LINUX = platform.system().lower() == 'linux'

print(f'[pyatv-server.spec] Building for: {TARGET_TRIPLE}')
print(f'[pyatv-server.spec] Binary name: {BINARY_NAME}')

# =============================================================================
# HIDDEN IMPORTS
# =============================================================================
# PyInstaller misses dynamically loaded modules. This comprehensive list ensures
# all pyatv protocols, zeroconf async modules, and cryptography backends are bundled.

# Base hidden imports (always required)
HIDDEN_IMPORTS = [
    # === pyatv core ===
    'pyatv',
    'pyatv.const',
    'pyatv.exceptions',
    'pyatv.convert',
    
    # === pyatv protocols (dynamically loaded via pyatv.protocols.__init__) ===
    'pyatv.protocols',
    'pyatv.protocols.mrp',
    'pyatv.protocols.mrp.mrp',
    'pyatv.protocols.mrp.protocol',
    'pyatv.protocols.mrp.protobuf',
    'pyatv.protocols.mrp.player_state',
    'pyatv.protocols.companion',
    'pyatv.protocols.companion.companion',
    'pyatv.protocols.companion.protocol',
    'pyatv.protocols.companion.connection',
    'pyatv.protocols.airplay',
    'pyatv.protocols.airplay.airplay',
    'pyatv.protocols.airplay.auth',
    'pyatv.protocols.airplay.player',
    'pyatv.protocols.airplay.channels',
    'pyatv.protocols.raop',
    'pyatv.protocols.raop.raop',
    'pyatv.protocols.raop.protocols',
    'pyatv.protocols.dmap',
    'pyatv.protocols.dmap.dmap',
    
    # === pyatv core/interface (facade pattern, async loaders) ===
    'pyatv.core',
    'pyatv.core.facade',
    'pyatv.core.scan',
    'pyatv.core.protocol',
    'pyatv.interface',
    
    # === pyatv storage (credential persistence) ===
    'pyatv.storage',
    'pyatv.storage.file_storage',
    
    # === pyatv support (crypto utilities) ===
    'pyatv.support',
    'pyatv.support.chacha20',
    'pyatv.support.opack',
    'pyatv.support.http',
    'pyatv.support.shield',
    
    # === zeroconf (mDNS/Bonjour discovery) ===
    'zeroconf',
    'zeroconf.asyncio',
    'zeroconf._utils',
    'zeroconf._utils.ipaddress',
    'zeroconf._utils.name',
    'zeroconf._utils.net',
    'zeroconf._utils.time',
    
    # === cryptography (authentication) ===
    'cryptography',
    'cryptography.hazmat',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.ciphers',
    'cryptography.hazmat.primitives.ciphers.aead',
    'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.asymmetric.ed25519',
    'cryptography.hazmat.primitives.asymmetric.x25519',
    'cryptography.hazmat.primitives.kdf',
    'cryptography.hazmat.primitives.hashes',
    'cryptography.hazmat.backends',
    
    # === chacha20poly1305 (reuseable cipher for pyatv) ===
    'chacha20poly1305_reuseable',
    
    # === aiohttp and dependencies (HTTP client for AirPlay/Companion) ===
    'aiohttp',
    'aiohttp.client',
    'aiohttp.connector',
    'aiohttp.web',
    'multidict',
    'yarl',
    'async_timeout',
    'aiosignal',
    'frozenlist',
    
    # === SRP (Secure Remote Password for pairing) ===
    'srptools',
    
    # === Standard library async (ensure bundled correctly) ===
    'asyncio',
    'asyncio.events',
    'asyncio.base_events',
    'asyncio.protocols',
    'asyncio.streams',
    
    # === Other dependencies ===
    'typing_extensions',
    'certifi',
    'charset_normalizer',
    'idna',
    'attrs',
    
    # === Pydantic (required by pyatv for settings) ===
    'pydantic',
    'pydantic_core',
    'pydantic.deprecated',
    'pydantic.deprecated.decorator',
    'pydantic._internal',
    'pydantic._internal._core_utils',
    'pydantic._internal._decorators',
    'pydantic._internal._fields',
    'pydantic._internal._generics',
    'pydantic._internal._model_construction',
    'pydantic._internal._repr',
    'pydantic._internal._typing_extra',
    'pydantic._internal._validators',
    'annotated_types',
]

# Use collect_submodules for comprehensive coverage if available
if USE_COLLECT:
    print('[pyatv-server.spec] Using collect_submodules for comprehensive imports')
    HIDDEN_IMPORTS.extend(collect_submodules('pyatv.protocols'))
    HIDDEN_IMPORTS.extend(collect_submodules('pyatv.support'))
    HIDDEN_IMPORTS.extend(collect_submodules('pyatv.storage'))
    HIDDEN_IMPORTS.extend(collect_submodules('pyatv.core'))
    HIDDEN_IMPORTS.extend(collect_submodules('zeroconf'))

# Remove duplicates while preserving order
HIDDEN_IMPORTS = list(dict.fromkeys(HIDDEN_IMPORTS))

# =============================================================================
# EXCLUDES (reduce binary size)
# =============================================================================
# These modules are not needed for a headless JSON-RPC server

EXCLUDES = [
    # GUI frameworks
    'tkinter',
    '_tkinter',
    'tk',
    'tcl',
    
    # Scientific computing (not needed)
    'numpy',
    'scipy',
    'pandas',
    'matplotlib',
    'PIL',
    'Pillow',
    
    # Testing frameworks
    'pytest',
    'unittest',
    '_pytest',
    'nose',
    'doctest',
    
    # Development tools
    'IPython',
    'ipython',
    'jupyter',
    'notebook',
    'sphinx',
    
    # Build tools
    'setuptools',
    'pip',
    'wheel',
    'distutils',
    'pkg_resources',
    
    # Other unused
    'xml.etree.ElementTree',  # We don't parse XML
    'pydoc',
    'pdb',
    'lib2to3',
    'ensurepip',
    'venv',
    'curses',
    '_curses',
    'readline',
    
]

# =============================================================================
# UPX CONFIGURATION
# =============================================================================
# UPX compression: reliable on Windows, spotty on macOS/Linux

# Enable UPX on Windows for significant size reduction
# Disable on macOS/Linux to avoid potential crashes
USE_UPX = IS_WINDOWS

# Exclude these files from UPX compression (can cause crashes)
UPX_EXCLUDES = [
    'python*.dll',
    'vcruntime*.dll',
    'api-ms-win-*.dll',
    'libpython*.so*',
    'libpython*.dylib',
    'Python',
]

# =============================================================================
# ANALYSIS
# =============================================================================

block_cipher = None

a = Analysis(
    ['src/main.py'],
    pathex=[str(Path(SPECPATH) / 'src')],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# =============================================================================
# PYZ (Python Zip Archive)
# =============================================================================

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

# =============================================================================
# EXE (Executable)
# =============================================================================

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=BINARY_NAME.replace('.exe', ''),  # PyInstaller adds .exe on Windows
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,  # Strip debug symbols for smaller size
    upx=USE_UPX,
    upx_exclude=UPX_EXCLUDES,
    runtime_tmpdir=None,
    console=not IS_WINDOWS,  # No console window on Windows (runs as sidecar)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

print(f'[pyatv-server.spec] Configuration complete')
print(f'[pyatv-server.spec] Hidden imports: {len(HIDDEN_IMPORTS)}')
print(f'[pyatv-server.spec] Excludes: {len(EXCLUDES)}')
print(f'[pyatv-server.spec] UPX enabled: {USE_UPX}')
