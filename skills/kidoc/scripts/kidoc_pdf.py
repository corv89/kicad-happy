#!/usr/bin/env python3
"""PDF generation from kidoc markdown scaffolds.

Converts markdown to PDF using ReportLab + svglib.  SVGs are embedded
as vector graphics (not rasterized).  Runs inside the reports/.venv/.

Usage (called by kidoc_generate.py, not directly):
    python3 kidoc_pdf.py --input reports/HDD.md --output reports/output/HDD.pdf
                         --config '{"project": {"name": "..."}}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# These imports require the venv to be active
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, inch
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
    PageBreak, Preformatted, ListFlowable, ListItem,
)
from reportlab.platypus.tableofcontents import TableOfContents

# svglib for vector SVG embedding
try:
    from svglib.svglib import svg2rlg
except ImportError:
    svg2rlg = None

# Add kidoc scripts to path for the markdown parser
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kidoc_md_parser import parse_markdown, parse_inline


# ======================================================================
# Page sizes
# ======================================================================

PAGE_SIZES = {
    'letter': letter,
    'a4': A4,
}


# ======================================================================
# Styles
# ======================================================================

def _build_styles():
    """Build custom paragraph styles."""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        'KidocTitle', parent=styles['Title'],
        fontSize=20, spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        'KidocH1', parent=styles['Heading1'],
        fontSize=16, spaceAfter=8, spaceBefore=16,
    ))
    styles.add(ParagraphStyle(
        'KidocH2', parent=styles['Heading2'],
        fontSize=13, spaceAfter=6, spaceBefore=12,
    ))
    styles.add(ParagraphStyle(
        'KidocH3', parent=styles['Heading3'],
        fontSize=11, spaceAfter=4, spaceBefore=8,
    ))
    styles.add(ParagraphStyle(
        'KidocBody', parent=styles['Normal'],
        fontSize=9, leading=12, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        'KidocCode', parent=styles['Code'],
        fontSize=7.5, leading=9, leftIndent=12,
        fontName='Courier', backColor=HexColor('#f5f5f5'),
        borderWidth=0.5, borderColor=HexColor('#e0e0e0'),
        borderPadding=4, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        'KidocBlockquote', parent=styles['Normal'],
        fontSize=9, leading=12, leftIndent=18,
        textColor=HexColor('#606060'), fontName='Helvetica-Oblique',
        borderWidth=0, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        'KidocCaption', parent=styles['Normal'],
        fontSize=8, textColor=HexColor('#606060'),
        alignment=TA_CENTER, spaceAfter=8,
    ))
    return styles


# ======================================================================
# Inline formatting to ReportLab XML
# ======================================================================

def _runs_to_para_xml(runs: list[dict]) -> str:
    """Convert inline runs to ReportLab Paragraph XML markup."""
    parts = []
    for r in runs:
        text = _escape_xml(r['text'])
        if r.get('code'):
            parts.append(f'<font face="Courier" size="8" color="#c04000">{text}</font>')
        elif r.get('bold') and r.get('italic'):
            parts.append(f'<b><i>{text}</i></b>')
        elif r.get('bold'):
            parts.append(f'<b>{text}</b>')
        elif r.get('italic'):
            parts.append(f'<i>{text}</i>')
        elif r.get('link'):
            parts.append(f'<a href="{_escape_xml(r["link"])}" color="blue">{text}</a>')
        else:
            parts.append(text)
    return ''.join(parts)


def _escape_xml(text: str) -> str:
    """Escape special XML characters."""
    return (text.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


# ======================================================================
# Element to flowable conversion
# ======================================================================

def _element_to_flowables(elem: dict, styles, base_dir: str,
                          page_width: float) -> list:
    """Convert a parsed markdown element to ReportLab flowables."""
    etype = elem['type']

    if etype == 'heading':
        level = elem['level']
        style_name = {1: 'KidocTitle', 2: 'KidocH1', 3: 'KidocH2'}.get(
            level, 'KidocH3')
        style = styles[style_name]
        return [Paragraph(_escape_xml(elem['text']), style)]

    elif etype == 'paragraph':
        xml = _runs_to_para_xml(elem['runs'])
        return [Paragraph(xml, styles['KidocBody'])]

    elif etype == 'image':
        return _build_image(elem, base_dir, page_width, styles)

    elif etype == 'table':
        return _build_table(elem, styles, page_width)

    elif etype == 'code_block':
        code = _escape_xml(elem['code'])
        return [Preformatted(code, styles['KidocCode'])]

    elif etype == 'hr':
        return [Spacer(1, 6)]

    elif etype == 'bullet_list':
        items = []
        for item_runs in elem['items']:
            xml = _runs_to_para_xml(item_runs)
            items.append(ListItem(Paragraph(xml, styles['KidocBody']),
                                  bulletType='bullet', value='disc'))
        return [ListFlowable(items, bulletType='bullet', start='disc')]

    elif etype == 'numbered_list':
        items = []
        for i, item_runs in enumerate(elem['items']):
            xml = _runs_to_para_xml(item_runs)
            items.append(ListItem(Paragraph(xml, styles['KidocBody'])))
        return [ListFlowable(items, bulletType='1')]

    elif etype == 'blockquote':
        xml = _runs_to_para_xml(elem['runs'])
        return [Paragraph(xml, styles['KidocBlockquote'])]

    return []


def _build_image(elem: dict, base_dir: str, page_width: float,
                 styles) -> list:
    """Build image flowable — SVG as vector, raster as Image."""
    path = elem['path']
    if not os.path.isabs(path):
        path = os.path.join(base_dir, path)

    if not os.path.isfile(path):
        return [Paragraph(f'<i>[Image not found: {_escape_xml(elem["path"])}]</i>',
                          styles['KidocCaption'])]

    flowables = []
    max_width = page_width - 2 * inch  # margins

    if path.lower().endswith('.svg') and svg2rlg:
        drawing = svg2rlg(path)
        if drawing:
            # Scale to fit page width
            scale = min(1.0, max_width / drawing.width) if drawing.width > 0 else 1.0
            drawing.width *= scale
            drawing.height *= scale
            drawing.scale(scale, scale)
            flowables.append(drawing)
    else:
        # Raster image
        try:
            img = Image(path)
            img_w, img_h = img.drawWidth, img.drawHeight
            if img_w > max_width:
                scale = max_width / img_w
                img.drawWidth = img_w * scale
                img.drawHeight = img_h * scale
            flowables.append(img)
        except Exception:
            flowables.append(Paragraph(
                f'<i>[Failed to load image: {_escape_xml(elem["path"])}]</i>',
                styles['KidocCaption']))

    if elem.get('alt'):
        flowables.append(Paragraph(
            f'<i>{_escape_xml(elem["alt"])}</i>', styles['KidocCaption']))

    return flowables


def _build_table(elem: dict, styles, page_width: float) -> list:
    """Build a ReportLab Table from parsed markdown table."""
    headers = elem['headers']
    rows = elem['rows']
    alignments = elem.get('alignments', ['left'] * len(headers))

    # Build data array with Paragraph objects for text wrapping
    col_count = len(headers)
    style = styles['KidocBody']
    header_style = ParagraphStyle('TableHeader', parent=style,
                                  fontName='Helvetica-Bold', fontSize=8)
    cell_style = ParagraphStyle('TableCell', parent=style, fontSize=8)

    data = []
    data.append([Paragraph(f'<b>{_escape_xml(h)}</b>', header_style)
                 for h in headers])
    for row in rows:
        cells = []
        for i in range(col_count):
            cell_text = row[i] if i < len(row) else ''
            cells.append(Paragraph(_escape_xml(cell_text), cell_style))
        data.append(cells)

    # Compute column widths
    available = page_width - 2 * inch
    col_width = available / col_count

    t = Table(data, colWidths=[col_width] * col_count)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#e8e8f0')),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#c0c0c0')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    return [t, Spacer(1, 6)]


# ======================================================================
# Header / footer
# ======================================================================

def _make_header_footer(config: dict):
    """Return an onPage callback for headers and footers."""
    project = config.get('project', {})
    branding = config.get('reports', {}).get('branding', {})
    header_left = branding.get('header_left', project.get('company', ''))
    header_right = branding.get('header_right', '')
    classification = config.get('reports', {}).get('classification', '')

    # Resolve simple placeholders
    for key, val in project.items():
        header_left = header_left.replace(f'{{{key}}}', str(val))
        header_right = header_right.replace(f'{{{key}}}', str(val))

    def _on_page(canvas, doc):
        canvas.saveState()
        width, height = doc.pagesize

        # Header
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(HexColor('#808080'))
        if header_left:
            canvas.drawString(doc.leftMargin, height - 0.4 * inch, header_left)
        if header_right:
            canvas.drawRightString(width - doc.rightMargin,
                                   height - 0.4 * inch, header_right)

        # Footer
        canvas.drawCentredString(width / 2, 0.4 * inch,
                                 f"Page {doc.page}")
        if classification:
            canvas.drawRightString(width - doc.rightMargin, 0.4 * inch,
                                   classification)

        canvas.restoreState()

    return _on_page


# ======================================================================
# Main generation
# ======================================================================

def generate_pdf(markdown_path: str, output_path: str, config: dict) -> str:
    """Convert markdown to PDF.  Returns the output path."""
    with open(markdown_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    elements = parse_markdown(md_text)
    styles = _build_styles()
    base_dir = os.path.dirname(os.path.abspath(markdown_path))

    # Page size
    page_size_name = config.get('reports', {}).get('page_size', 'letter')
    page_size = PAGE_SIZES.get(page_size_name.lower(), letter)
    page_width = page_size[0]

    # Build document
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or '.', exist_ok=True)
    doc = SimpleDocTemplate(
        output_path, pagesize=page_size,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )

    flowables = []
    for elem in elements:
        flowables.extend(
            _element_to_flowables(elem, styles, base_dir, page_width))

    on_page = _make_header_footer(config)
    doc.build(flowables, onFirstPage=on_page, onLaterPages=on_page)

    return output_path


def main():
    parser = argparse.ArgumentParser(description='Generate PDF from markdown')
    parser.add_argument('--input', '-i', required=True,
                        help='Input markdown file')
    parser.add_argument('--output', '-o', required=True,
                        help='Output PDF file')
    parser.add_argument('--config', '-c', default='{}',
                        help='JSON config string or path to config file')
    args = parser.parse_args()

    # Load config
    if os.path.isfile(args.config):
        with open(args.config) as f:
            config = json.load(f)
    else:
        config = json.loads(args.config)

    output = generate_pdf(args.input, args.output, config)
    print(output, file=sys.stderr)


if __name__ == '__main__':
    main()
