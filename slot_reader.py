"""Read Xiao Luo slots and court colors from Tencent-exported XLSX.

This module only extracts source appointments. It never writes to the database
and does not decide whether adjacent names are one lesson or several lessons.
"""
import io
import posixpath
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from xml.etree import ElementTree as ET


MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = {'m': MAIN}
COURT_COLORS = {'FFC9C7': 'Meadows', '53AEEA': 'Grange Club'}


def _cell_colors(archive, root):
    """Read raw fill metadata without openpyxl's stricter style validation."""
    if 'xl/styles.xml' not in archive.namelist():
        return {}
    try:
        styles = ET.fromstring(archive.read('xl/styles.xml'))
    except ET.ParseError:
        return {}
    fill_elements = styles.find('m:fills', NS)
    xf_elements = styles.find('m:cellXfs', NS)
    fills = list(fill_elements) if fill_elements is not None else []
    xfs = list(xf_elements) if xf_elements is not None else []
    colors = {}
    for cell in root.findall('m:sheetData/m:row/m:c', NS):
        try:
            style = xfs[int(cell.attrib.get('s', '0'))]
            fill = fills[int(style.attrib.get('fillId', '0'))]
        except (ValueError, IndexError):
            continue
        pattern = fill.find('m:patternFill', NS)
        if pattern is None or pattern.attrib.get('patternType') != 'solid':
            continue
        foreground = pattern.find('m:fgColor', NS)
        if foreground is None or foreground.attrib.get('tint') not in (None, '0', '0.0'):
            continue
        raw = foreground.attrib.get('rgb', '').upper()
        if re.fullmatch(r'(?:[0-9A-F]{2})?[0-9A-F]{6}', raw):
            colors[cell.attrib['r']] = raw[-6:]
    return colors


def clean_name(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value)).split())


def _coordinates(address):
    match = re.fullmatch(r'([A-Z]+)(\d+)', address)
    if not match:
        raise ValueError('无效的单元格坐标。')
    col = 0
    for letter in match[1]:
        col = col * 26 + ord(letter) - ord('A') + 1
    return col, int(match[2])


def _cell_values(root, strings):
    cells, formulas = {}, set()
    for cell in root.findall('m:sheetData/m:row/m:c', NS):
        address = cell.attrib.get('r', '')
        if cell.find('m:f', NS) is not None:
            formulas.add(address)
        value = cell.find('m:v', NS)
        text = value.text or '' if value is not None else ''
        if cell.attrib.get('t') == 's' and text:
            try:
                text = strings[int(text)]
            except (ValueError, IndexError):
                raise ValueError('共享文字索引损坏，请重新导出课表。')
        elif cell.attrib.get('t') == 'inlineStr':
            text = ''.join(t.text or '' for t in cell.iter(f'{{{MAIN}}}t'))
        cells[address] = text
    return cells, formulas


def _week(header, year):
    match = re.search(r'(\d{1,2})\s*[.月/\-]\s*(\d{1,2})\s*日?\s*[-—–~～至]\s*(\d{1,2})\s*[.月/\-]\s*(\d{1,2})', header)
    if not match:
        raise ValueError('A2 没找到类似 10.19-10.25 的日期范围。')
    month, day, end_month, end_day = map(int, match.groups())
    start = date(year, month, day)
    end_year = year + int((end_month, end_day) < (month, day))
    end = date(end_year, end_month, end_day)
    if (end - start).days != 6:
        raise ValueError('顶部日期须包含连续七天，请检查课表。')
    if start.weekday() != 0:
        raise ValueError('所选年份与表中周一至周日不符，请核对年份和顶部日期。')
    return start, end


def _minute(value):
    try:
        minute = int((Decimal(str(value)) * 1440).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise ValueError('A27:A40 的时间格式无法识别。')
    if minute < 0 or minute >= 1440:
        raise ValueError('时间超出当天范围。')
    return minute


def parse_xiaoluo_xlsx(content, year):
    """Return original occupied cells, including merged spans, with no grouping."""
    if not isinstance(year, int) or not 1900 <= year <= 2200:
        raise ValueError('请明确填写有效年份。')
    if len(content) > 8 * 1024 * 1024:
        raise ValueError('文件超过 8 MB，请只保留需要导入的课表。')
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ValueError('这不是有效的 XLSX 文件，请重新导出。')
    with archive:
        if sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
            raise ValueError('文件解压后过大，暂不能导入。')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in item.iter(f'{{{MAIN}}}t')) for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        links = {item.attrib['Id']: item.attrib['Target'] for item in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        candidates = []
        for sheet in workbook.find('m:sheets', NS):
            target = links[sheet.attrib[f'{{{REL}}}id']]
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            root = ET.fromstring(archive.read(path))
            cells, formulas = _cell_values(root, strings)
            if re.sub(r'\s+', '', cells.get('A25', '')) == '小罗课程表':
                candidates.append((sheet.attrib['name'], root, cells, formulas))
        if len(candidates) != 1:
            raise ValueError('须且只能找到一个 A25 标为“小罗课程表”的工作表。')
        sheet_name, root, cells, formulas = candidates[0]
        colors = _cell_colors(archive, root)
        relevant = {'A2', 'A25'} | {f'A{r}' for r in range(27, 41)} | {f'{c}{r}' for c in 'BCDEFGH' for r in range(26, 41)}
        if relevant & formulas:
            raise ValueError('目标区域含公式，请导出为实际值后再导入。')
        for col, weekday in zip('BCDEFGH', ['周一', '周二', '周三', '周四', '周五', '周六', '周日']):
            if clean_name(cells.get(f'{col}26', '')) != weekday:
                raise ValueError('B26:H26 的星期布局发生变化，请检查文件。')
        week_start, week_end = _week(cells.get('A2', ''), year)
        minutes = {row: _minute(cells.get(f'A{row}', '')) for row in range(27, 41)}
        if any(minutes[row + 1] - minutes[row] != 60 for row in range(27, 40)):
            raise ValueError('当前只支持 A27:A40 每行一小时的固定时间布局。')
        spans = {}
        for merged in root.findall('m:mergeCells/m:mergeCell', NS):
            first, last = merged.attrib['ref'].split(':')
            first_col, first_row = _coordinates(first)
            last_col, last_row = _coordinates(last)
            intersects = first_col <= 8 and last_col >= 2 and first_row <= 40 and last_row >= 27
            if not intersects:
                continue
            if first_col != last_col or not (2 <= first_col <= 8 and 27 <= first_row <= last_row <= 40):
                raise ValueError('小罗时段内存在跨日期或越界的合并格，请人工检查。')
            spans[first] = (last_row, merged.attrib['ref'])
        records = []
        for col_offset, col in enumerate('BCDEFGH'):
            current_day = week_start + timedelta(days=col_offset)
            for row in range(27, 41):
                address = f'{col}{row}'
                name = clean_name(cells.get(address, ''))
                if not name:
                    continue
                last_row, reference = spans.get(address, (row, address))
                start = datetime.combine(current_day, datetime.min.time()) + timedelta(minutes=minutes[row])
                end_minute = minutes[last_row] + 60
                end = datetime.combine(current_day, datetime.min.time()) + timedelta(minutes=end_minute)
                records.append({
                    'source_cell': reference,
                    'raw_name': cells[address],
                    'name': name,
                    'starts_at': start.isoformat(timespec='minutes'),
                    'ends_at': end.isoformat(timespec='minutes'),
                    'group_label': bool(re.search(r'班|团课', name)),
                    'fill_color': colors.get(address, ''),
                    'location': COURT_COLORS.get(colors.get(address, ''), ''),
                    'hour_colors': [colors.get(f'{col}{r}', colors.get(address, '')) for r in range(row, last_row + 1)],
                })
        return {
            'sheet_name': sheet_name,
            'header': cells.get('A2', ''),
            'week_start': week_start.isoformat(),
            'week_end': week_end.isoformat(),
            'records': records,
        }
