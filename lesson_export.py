"""Read-only training-record exports. Uses only Python's standard library."""
from collections import OrderedDict
from datetime import date, timedelta
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import user_service as svc


def eligible_lessons(records, week_start, now=None):
    """Only finished, checked-in, complete records in the displayed week."""
    if isinstance(week_start, str):
        week_start = date.fromisoformat(week_start)
    end = week_start + timedelta(days=7)
    eligible = []
    for row in records:
        if not week_start <= date.fromisoformat(row['starts_at'][:10]) < end:
            continue
        if svc.attendance(row, now) != '上了' or not row['checkin_done']:
            continue
        try:
            svc.validate_checkin(row['note'], row['next_goal'])
        except ValueError:
            continue
        eligible.append(row)
    return sorted(eligible, key=lambda row: (row['starts_at'], row['id']))


def select_records(records, selected_ids, week_start, now=None):
    """Revalidate the selection; duplicates and stale IDs cannot leak records."""
    wanted = set(selected_ids)
    return [row for row in eligible_lessons(records, week_start, now) if row['id'] in wanted]


def grouped_records(records):
    groups = OrderedDict()
    for row in sorted(records, key=lambda row: (row['starts_at'], row['id'])):
        groups.setdefault(row['student_id'], []).append(row)
    return list(groups.values())


def lesson_label(row):
    label=f"{row['starts_at'][:10]} {row['starts_at'][11:16]}–{row['ends_at'][11:16]} · {row['kind']} · {row['location']}"
    return label+(f" · 教练 {row['coach_name']}" if row.get('coach_name') else '')


def student_text(records):
    if not records:
        return ''
    lines = [f"{records[0]['student_name']}的网球训练记录"]
    for row in records:
        lines.extend(['', lesson_label(row), '本节训练：', row['note'].strip(),
                      '下次目标：', row['next_goal'].strip()])
    return '\n'.join(lines)


def export_text(records):
    return '\n\n──────────\n\n'.join(student_text(group) for group in grouped_records(records))


def export_txt(records):
    # BOM lets Windows Notepad identify Chinese text reliably.
    return export_text(records).replace('\n', '\r\n').encode('utf-8-sig')


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ET.register_namespace('w', W)


def _node(parent, tag, **attributes):
    return ET.SubElement(parent, f'{{{W}}}{tag}', {f'{{{W}}}{k}': str(v) for k, v in attributes.items()})


def _paragraph(parent, text, style='Normal'):
    p = _node(parent, 'p')
    _node(_node(p, 'pPr'), 'pStyle', val=style)
    # XML 1.0 disallows most control characters; preserve Unicode and line breaks.
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', text)
    for index, line in enumerate(text.replace('\r\n', '\n').replace('\r', '\n').split('\n')):
        run = _node(p, 'r')
        if index:
            _node(run, 'br')
        t = _node(run, 't')
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = line
    return p


def export_docx(records):
    """Build an editable DOCX, without requiring a separate Word dependency."""
    if not records:
        raise ValueError('请先选择课程。')
    document = ET.Element(f'{{{W}}}document')
    body = _node(document, 'body')
    _paragraph(body, '网球训练记录', 'Title')
    for group in grouped_records(records):
        _paragraph(body, group[0]['student_name'], 'Heading1')
        for row in group:
            _paragraph(body, lesson_label(row), 'Heading2')
            _paragraph(body, '本节训练', 'Heading3')
            _paragraph(body, row['note'].strip())
            _paragraph(body, '下次目标', 'Heading3')
            _paragraph(body, row['next_goal'].strip())
    section = _node(body, 'sectPr')
    _node(section, 'pgSz', w=12240, h=15840)
    _node(section, 'pgMar', top=1080, right=1080, bottom=1080, left=1080, header=0, footer=0, gutter=0)
    styles = ET.Element(f'{{{W}}}styles')
    for style_id, size, bold, before, after in [
        ('Normal', 22, False, 0, 120), ('Title', 36, True, 0, 240),
        ('Heading1', 30, True, 240, 120), ('Heading2', 24, True, 180, 100),
        ('Heading3', 22, True, 120, 60),
    ]:
        s = _node(styles, 'style', type='paragraph', styleId=style_id)
        _node(s, 'name', val=style_id)
        if style_id == 'Normal':
            s.set(f'{{{W}}}default', '1')
        else:
            _node(s, 'basedOn', val='Normal')
        ppr = _node(s, 'pPr')
        _node(ppr, 'spacing', before=before, after=after, line=300, lineRule='auto')
        _node(ppr, 'widowControl')
        if style_id != 'Normal':
            _node(ppr, 'keepNext')
            _node(ppr, 'keepLines')
        rpr = _node(s, 'rPr')
        _node(rpr, 'rFonts', ascii='Calibri', hAnsi='Calibri', eastAsia='Microsoft YaHei')
        _node(rpr, 'color', val='000000')
        _node(rpr, 'sz', val=size)
        _node(rpr, 'szCs', val=size)
        if bold:
            _node(rpr, 'b')
    content_types = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''
    relationships = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    document_relationships = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    buffer = BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', relationships)
        archive.writestr('word/_rels/document.xml.rels', document_relationships)
        archive.writestr('word/document.xml', ET.tostring(document, encoding='utf-8', xml_declaration=True))
        archive.writestr('word/styles.xml', ET.tostring(styles, encoding='utf-8', xml_declaration=True))
    return buffer.getvalue()
