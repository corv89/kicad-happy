"""Shared utilities for parsing KiCad S-expression (.kicad_sch) files.

Provides low-level helpers used by bom_manager.py and edit_properties.py.
"""

from __future__ import annotations


def find_matching_paren(text: str, start: int) -> int:
    """Find the index of the closing paren matching the open paren at `start`."""
    depth = 1
    i = start + 1
    in_string = False
    while i < len(text) and depth > 0:
        c = text[i]
        if in_string:
            if c == '\\':
                i += 2
                continue
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
        i += 1
    return i - 1


def escape_kicad_string(s: str) -> str:
    """Escape a string for use in a KiCad S-expression property value."""
    return s.replace('\\', '\\\\').replace('"', '\\"')
