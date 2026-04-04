"""Auto-detect kicad-cli for PCB rendering and 3D exports.

Searches platform-specific paths for the kicad-cli executable.
Gracefully returns None if not found — all kidoc features that use
kicad-cli degrade to alternative renderers or skip.

Zero external dependencies — Python stdlib only.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess


def find_kicad_cli() -> str | None:
    """Find the kicad-cli executable.

    Search order:
    1. PATH (covers Linux with system install, or user-added symlinks)
    2. Flatpak (Linux: ``flatpak run --command=kicad-cli org.kicad.KiCad``)
    3. macOS app bundle (``/Applications/KiCad/*.app/Contents/MacOS/kicad-cli``)
    4. Windows Program Files (``C:\\Program Files\\KiCad\\*\\bin\\kicad-cli.exe``)

    Returns the command string/list, or None if not found.
    """
    # 1. Check PATH
    path = shutil.which('kicad-cli')
    if path:
        return path

    system = platform.system()

    # 2. Flatpak (Linux)
    if system == 'Linux':
        try:
            result = subprocess.run(
                ['flatpak', 'run', '--command=kicad-cli',
                 'org.kicad.KiCad', '--version'],
                capture_output=True, timeout=10)
            if result.returncode == 0:
                return 'flatpak run --command=kicad-cli org.kicad.KiCad'
        except (OSError, subprocess.TimeoutExpired):
            pass

    # 3. macOS app bundle
    if system == 'Darwin':
        candidates = glob.glob(
            '/Applications/KiCad/KiCad*.app/Contents/MacOS/kicad-cli')
        candidates += glob.glob(
            '/Applications/KiCad*.app/Contents/MacOS/kicad-cli')
        for c in sorted(candidates, reverse=True):  # newest version first
            if os.path.isfile(c):
                return c

    # 4. Windows Program Files
    if system == 'Windows':
        for pf in [os.environ.get('ProgramFiles', r'C:\Program Files'),
                    os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')]:
            candidates = glob.glob(
                os.path.join(pf, 'KiCad', '*', 'bin', 'kicad-cli.exe'))
            for c in sorted(candidates, reverse=True):
                if os.path.isfile(c):
                    return c

    return None


def kicad_cli_version(cli_cmd: str) -> str | None:
    """Get kicad-cli version string, or None if the command fails."""
    try:
        parts = cli_cmd.split() if ' ' in cli_cmd else [cli_cmd]
        result = subprocess.run(
            parts + ['--version'],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def export_pcb_svg(cli_cmd: str, pcb_path: str, output_path: str,
                   layers: str = 'F.Cu,F.SilkS,Edge.Cuts') -> bool:
    """Export PCB layers to SVG using kicad-cli.

    Returns True on success.
    """
    parts = cli_cmd.split() if ' ' in cli_cmd else [cli_cmd]
    cmd = parts + ['pcb', 'export', 'svg',
                   '--layers', layers,
                   '--output', output_path,
                   pcb_path]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def export_pcb_3d(cli_cmd: str, pcb_path: str, output_path: str,
                  side: str = 'top', width: int = 2000,
                  height: int = 1500) -> bool:
    """Export PCB 3D render to PNG using kicad-cli.

    Returns True on success.
    """
    parts = cli_cmd.split() if ' ' in cli_cmd else [cli_cmd]
    cmd = parts + ['pcb', 'render',
                   '--output', output_path,
                   '--side', side,
                   '--width', str(width),
                   '--height', str(height),
                   '--quality', 'high',
                   '--background', 'transparent',
                   pcb_path]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
