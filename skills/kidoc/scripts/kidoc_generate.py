#!/usr/bin/env python3
"""Orchestrator for kidoc document generation.

Manages the full pipeline: analysis → render → scaffold → PDF/DOCX.
Runs analysis and rendering with system Python (zero-dep), then
dispatches PDF/DOCX generation to the project-local venv.

Usage:
    python3 kidoc_generate.py --project-dir . --format pdf
    python3 kidoc_generate.py --project-dir . --format docx
    python3 kidoc_generate.py --project-dir . --format all
    python3 kidoc_generate.py --project-dir . --doc HDD.md --format pdf

Zero external dependencies — Python stdlib only (dispatches to venv for PDF/DOCX).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kidoc_venv import ensure_venv, venv_python

_kicad_scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '..', '..', 'kicad', 'scripts')
if os.path.isdir(_kicad_scripts):
    sys.path.insert(0, os.path.abspath(_kicad_scripts))

try:
    from project_config import load_config, load_config_from_path
except ImportError:
    def load_config(search_dir):
        return {'version': 1, 'project': {}, 'suppressions': []}
    def load_config_from_path(path):
        return {'version': 1, 'project': {}, 'suppressions': []}


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Map markdown stem → human-readable document type name
_DOC_TYPE_NAMES = {
    'hdd': 'Hardware Design Report',
    'ce_technical_file': 'CE Technical File',
    'design_review': 'Design Review',
    'icd': 'Interface Control Document',
    'manufacturing': 'Manufacturing Transfer Package',
}


def _build_filename(stem: str, project_name: str, revision: str) -> str:
    """Build a human-readable filename from project info.

    Examples:
        "SacMap Rev2 - Hardware Design Report Rev 2.0"
        "Widget Board - Design Review Rev 1.1"
        "HDD" (fallback if no project name)
    """
    # Try to match stem to a known doc type
    doc_type_name = ''
    stem_lower = stem.lower().replace('-', '_').replace(' ', '_')
    for key, name in _DOC_TYPE_NAMES.items():
        if key in stem_lower or stem_lower in key:
            doc_type_name = name
            break
    if not doc_type_name:
        doc_type_name = stem.replace('_', ' ').replace('-', ' ').title()

    parts = []
    if project_name:
        parts.append(project_name)
    parts.append(doc_type_name)
    name = ' - '.join(parts)

    if revision:
        name += f' Rev {revision}'

    # Sanitize for filesystem
    name = name.replace('/', '-').replace('\\', '-').replace(':', '-')
    return name


def _find_markdown_files(project_dir: str) -> list[str]:
    """Find markdown scaffolds in the reports/ directory."""
    reports_dir = os.path.join(project_dir, 'reports')
    if not os.path.isdir(reports_dir):
        return []
    return sorted(
        os.path.join(reports_dir, f)
        for f in os.listdir(reports_dir)
        if f.endswith('.md') and not f.startswith('.')
    )


def _generate_html(md_path: str, output_path: str,
                    config: dict) -> bool:
    """Generate HTML — zero-dep, runs with system Python."""
    cmd = [
        sys.executable,
        os.path.join(SCRIPTS_DIR, 'kidoc_html.py'),
        '--input', md_path,
        '--output', output_path,
        '--config', json.dumps(config),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"HTML generation failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def _generate_pdf(venv_py: str, md_path: str, output_path: str,
                   config: dict) -> bool:
    """Dispatch PDF generation to the venv."""
    config_json = json.dumps(config)
    cmd = [
        venv_py,
        os.path.join(SCRIPTS_DIR, 'kidoc_pdf.py'),
        '--input', md_path,
        '--output', output_path,
        '--config', config_json,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"PDF generation failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def _generate_docx(venv_py: str, md_path: str, output_path: str,
                    config: dict) -> bool:
    """Dispatch DOCX generation to the venv."""
    config_json = json.dumps(config)
    cmd = [
        venv_py,
        os.path.join(SCRIPTS_DIR, 'kidoc_docx.py'),
        '--input', md_path,
        '--output', output_path,
        '--config', config_json,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"DOCX generation failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def _generate_odt(venv_py: str, md_path: str, output_path: str,
                   config: dict) -> bool:
    """Dispatch ODT generation to the venv."""
    config_json = json.dumps(config)
    cmd = [
        venv_py,
        os.path.join(SCRIPTS_DIR, 'kidoc_odt.py'),
        '--input', md_path,
        '--output', output_path,
        '--config', config_json,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ODT generation failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def generate_documents(project_dir: str, formats: list[str],
                       doc_name: str | None = None,
                       config: dict | None = None) -> list[str]:
    """Generate PDF/DOCX from markdown scaffolds.

    Returns list of output file paths.
    """
    if config is None:
        config = load_config(project_dir)

    # Find markdown files
    if doc_name:
        md_path = doc_name
        if not os.path.isabs(md_path):
            md_path = os.path.join(project_dir, 'reports', md_path)
        md_files = [md_path] if os.path.isfile(md_path) else []
    else:
        md_files = _find_markdown_files(project_dir)

    if not md_files:
        print("No markdown files found in reports/. Run kidoc_scaffold.py first.",
              file=sys.stderr)
        return []

    # Ensure venv for PDF/DOCX/ODT (not needed for HTML)
    needs_venv = any(f in formats for f in ('pdf', 'docx', 'odt', 'all'))
    venv_py = None
    if needs_venv:
        print("Checking report generation environment...", file=sys.stderr)
        venv_py = ensure_venv(project_dir)

    output_dir = os.path.join(project_dir, 'reports', 'output')
    os.makedirs(output_dir, exist_ok=True)

    outputs = []
    for md_path in md_files:
        stem = Path(md_path).stem
        project = config.get('project', {})
        rev = project.get('revision', '')
        proj_name = project.get('name', '')

        # Build human-readable filename a manager can understand
        # e.g. "SacMap Rev2 - Hardware Design Report Rev 2.0.pdf"
        base_name = _build_filename(stem, proj_name, rev)

        if 'html' in formats or 'all' in formats:
            html_path = os.path.join(output_dir, f"{base_name}.html")
            print(f"Generating HTML: {html_path}", file=sys.stderr)
            if _generate_html(md_path, html_path, config):
                outputs.append(html_path)
                print(f"  -> {html_path}", file=sys.stderr)

        if 'pdf' in formats or 'all' in formats:
            pdf_path = os.path.join(output_dir, f"{base_name}.pdf")
            print(f"Generating PDF: {pdf_path}", file=sys.stderr)
            if _generate_pdf(venv_py, md_path, pdf_path, config):
                outputs.append(pdf_path)
                print(f"  -> {pdf_path}", file=sys.stderr)

        if 'docx' in formats or 'all' in formats:
            docx_path = os.path.join(output_dir, f"{base_name}.docx")
            print(f"Generating DOCX: {docx_path}", file=sys.stderr)
            if _generate_docx(venv_py, md_path, docx_path, config):
                outputs.append(docx_path)
                print(f"  -> {docx_path}", file=sys.stderr)

        if 'odt' in formats or 'all' in formats:
            odt_path = os.path.join(output_dir, f"{base_name}.odt")
            print(f"Generating ODT: {odt_path}", file=sys.stderr)
            if _generate_odt(venv_py, md_path, odt_path, config):
                outputs.append(odt_path)
                print(f"  -> {odt_path}", file=sys.stderr)

    return outputs


def main():
    parser = argparse.ArgumentParser(
        description='Generate PDF/DOCX from kidoc markdown scaffolds')
    parser.add_argument('--project-dir', '-p', default='.',
                        help='Path to KiCad project directory')
    parser.add_argument('--format', '-f', default='pdf',
                        choices=['pdf', 'html', 'docx', 'odt', 'all'],
                        help='Output format (default: pdf)')
    parser.add_argument('--doc', default=None,
                        help='Specific markdown file to process')
    parser.add_argument('--config', default=None,
                        help='Path to .kicad-happy.json config')
    args = parser.parse_args()

    if args.config:
        config = load_config_from_path(args.config)
    else:
        config = load_config(args.project_dir)

    formats = [args.format] if args.format != 'all' else ['html', 'pdf', 'docx', 'odt']

    outputs = generate_documents(
        project_dir=args.project_dir,
        formats=formats,
        doc_name=args.doc,
        config=config,
    )

    if outputs:
        print(f"\nGenerated {len(outputs)} document(s):", file=sys.stderr)
        for o in outputs:
            print(f"  {o}", file=sys.stderr)
    else:
        print("No documents generated.", file=sys.stderr)


if __name__ == '__main__':
    main()
