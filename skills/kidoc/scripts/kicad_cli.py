"""Auto-detect kicad-cli across all common installation methods.

Searches for the kicad-cli executable across: PATH, Flatpak, Snap,
macOS app bundles, Homebrew, Nix, Windows Program Files, Chocolatey,
and MSYS2.  Gracefully returns None if not found — all kidoc features
that use kicad-cli degrade to alternative renderers or skip.

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
    1. PATH (system package managers: apt, pacman, dnf, brew, nix-env, etc.)
    2. Flatpak (Linux: ``flatpak run --command=kicad-cli org.kicad.KiCad``)
    3. Snap (Linux: ``/snap/kicad/current/bin/kicad-cli`` or ``snap run``)
    4. macOS app bundle (``/Applications/KiCad/*.app/Contents/MacOS/kicad-cli``)
    5. Homebrew (macOS: ``/opt/homebrew/bin/kicad-cli``, ``/usr/local/bin/kicad-cli``)
    6. Nix (``~/.nix-profile/bin/kicad-cli``, ``/nix/var/nix/profiles/*/bin/kicad-cli``)
    7. Windows Program Files (``C:\\Program Files\\KiCad\\*\\bin\\kicad-cli.exe``)
    8. Chocolatey (Windows: ``C:\\ProgramData\\chocolatey\\bin\\kicad-cli.exe``)
    9. MSYS2 (Windows: ``C:\\msys64\\mingw64\\bin\\kicad-cli.exe``)

    Returns a command string that can be passed to ``_run_cli()``, or None.
    """
    # 1. Check PATH (covers apt, pacman, dnf, brew, nix-env, user symlinks)
    path = shutil.which('kicad-cli')
    if path:
        return path

    system = platform.system()

    # --- Linux-specific methods ---
    if system == 'Linux':
        # 2. Flatpak
        cmd = _check_flatpak()
        if cmd:
            return cmd

        # 3. Snap
        cmd = _check_snap()
        if cmd:
            return cmd

    # --- macOS-specific methods ---
    if system == 'Darwin':
        # 4. App bundle
        cmd = _check_macos_app_bundle()
        if cmd:
            return cmd

        # 5. Homebrew (ARM and Intel paths)
        for brew_path in ['/opt/homebrew/bin/kicad-cli',
                          '/usr/local/bin/kicad-cli']:
            if os.path.isfile(brew_path):
                return brew_path

    # --- Cross-platform: Nix ---
    # 6. Nix profiles
    cmd = _check_nix()
    if cmd:
        return cmd

    # --- Windows-specific methods ---
    if system == 'Windows':
        # 7. Program Files (standard installer)
        cmd = _check_windows_program_files()
        if cmd:
            return cmd

        # 8. Chocolatey
        choco = os.path.join(
            os.environ.get('ChocolateyInstall',
                           r'C:\ProgramData\chocolatey'),
            'bin', 'kicad-cli.exe')
        if os.path.isfile(choco):
            return choco

        # 9. MSYS2
        for msys_root in [r'C:\msys64', r'C:\msys32']:
            for env in ['mingw64', 'mingw32', 'ucrt64', 'clang64']:
                msys = os.path.join(msys_root, env, 'bin', 'kicad-cli.exe')
                if os.path.isfile(msys):
                    return msys

    return None


def _check_flatpak() -> str | None:
    """Check for KiCad installed via Flatpak."""
    if not shutil.which('flatpak'):
        return None
    try:
        result = subprocess.run(
            ['flatpak', 'run', '--command=kicad-cli',
             'org.kicad.KiCad', '--version'],
            capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return 'flatpak run --command=kicad-cli org.kicad.KiCad'
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _check_snap() -> str | None:
    """Check for KiCad installed via Snap."""
    # Direct path check (faster than running snap)
    snap_paths = [
        '/snap/kicad/current/bin/kicad-cli',
        '/snap/kicad/current/usr/bin/kicad-cli',
    ]
    for p in snap_paths:
        if os.path.isfile(p):
            return p

    # Fallback: try snap run
    if shutil.which('snap'):
        try:
            result = subprocess.run(
                ['snap', 'run', 'kicad.kicad-cli', '--version'],
                capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return 'snap run kicad.kicad-cli'
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _check_macos_app_bundle() -> str | None:
    """Check for KiCad macOS app bundle."""
    # Multiple naming conventions across KiCad versions
    patterns = [
        '/Applications/KiCad/KiCad*.app/Contents/MacOS/kicad-cli',
        '/Applications/KiCad*.app/Contents/MacOS/kicad-cli',
        os.path.expanduser('~/Applications/KiCad*.app/Contents/MacOS/kicad-cli'),
    ]
    candidates = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))
    # Sort descending to prefer newest version
    for c in sorted(set(candidates), reverse=True):
        if os.path.isfile(c):
            return c
    return None


def _check_nix() -> str | None:
    """Check for KiCad installed via Nix."""
    nix_paths = [
        os.path.expanduser('~/.nix-profile/bin/kicad-cli'),
        '/nix/var/nix/profiles/default/bin/kicad-cli',
    ]
    # Also check per-user profiles
    nix_profiles = glob.glob('/nix/var/nix/profiles/per-user/*/bin/kicad-cli')
    nix_paths.extend(nix_profiles)
    for p in nix_paths:
        if os.path.isfile(p):
            return p
    return None


def _check_windows_program_files() -> str | None:
    """Check Windows Program Files for KiCad installer."""
    for pf_var in ['ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432']:
        pf = os.environ.get(pf_var)
        if not pf:
            continue
        candidates = glob.glob(
            os.path.join(pf, 'KiCad', '*', 'bin', 'kicad-cli.exe'))
        for c in sorted(candidates, reverse=True):
            if os.path.isfile(c):
                return c
    return None


# ======================================================================
# CLI execution helpers
# ======================================================================

def _run_cli(cli_cmd: str, args: list[str],
             timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a kicad-cli command, handling both path and multi-word commands."""
    if ' ' in cli_cmd:
        parts = cli_cmd.split()
    else:
        parts = [cli_cmd]
    return subprocess.run(
        parts + args, capture_output=True, text=True, timeout=timeout)


def kicad_cli_version(cli_cmd: str) -> str | None:
    """Get kicad-cli version string, or None if the command fails."""
    try:
        result = _run_cli(cli_cmd, ['--version'], timeout=15)
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def export_pcb_svg(cli_cmd: str, pcb_path: str, output_path: str,
                   layers: str = 'F.Cu,F.SilkS,Edge.Cuts') -> bool:
    """Export PCB layers to SVG using kicad-cli.  Returns True on success."""
    try:
        result = _run_cli(cli_cmd, [
            'pcb', 'export', 'svg',
            '--layers', layers,
            '--output', output_path,
            pcb_path,
        ])
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def export_pcb_3d(cli_cmd: str, pcb_path: str, output_path: str,
                  side: str = 'top', width: int = 2000,
                  height: int = 1500) -> bool:
    """Export PCB 3D render to PNG using kicad-cli.  Returns True on success."""
    try:
        result = _run_cli(cli_cmd, [
            'pcb', 'render',
            '--output', output_path,
            '--side', side,
            '--width', str(width),
            '--height', str(height),
            '--quality', 'high',
            '--background', 'transparent',
            pcb_path,
        ])
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def export_sch_svg(cli_cmd: str, sch_path: str, output_dir: str) -> bool:
    """Export schematic to SVG using kicad-cli.  Returns True on success."""
    try:
        result = _run_cli(cli_cmd, [
            'sch', 'export', 'svg',
            '--output', output_dir,
            sch_path,
        ])
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
