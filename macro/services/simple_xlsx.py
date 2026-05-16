"""Small XLSX reader for external indicator files.

openpyxl/xlrd を追加せず、公開 Excel の単純な表だけを読むための最小実装。
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta
from typing import List
from xml.etree import ElementTree as ET


NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def excel_serial_to_date(value: float) -> date:
    """Excel の日付シリアル値を date に変換する。"""
    return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()


def _column_index(cell_ref: str) -> int:
    match = re.match(r'([A-Z]+)', cell_ref or 'A1')
    if not match:
        return 0
    idx = 0
    for ch in match.group(1):
        idx = idx * 26 + ord(ch) - ord('A') + 1
    return idx - 1


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    if 'xl/sharedStrings.xml' not in zf.namelist():
        return []
    root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
    return [
        ''.join(t.text or '' for t in si.iter(f'{NS}t'))
        for si in root.findall(f'{NS}si')
    ]


def read_first_sheet(content: bytes) -> List[List[str]]:
    """XLSX の先頭シートを二次元配列として返す。"""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        shared = _shared_strings(zf)
        sheet_name = next(
            name for name in zf.namelist()
            if name.startswith('xl/worksheets/sheet')
        )
        root = ET.fromstring(zf.read(sheet_name))
        rows: List[List[str]] = []
        for row in root.iter(f'{NS}row'):
            out: List[str] = []
            for cell in row.findall(f'{NS}c'):
                idx = _column_index(cell.attrib.get('r', 'A1'))
                while len(out) <= idx:
                    out.append('')
                cell_type = cell.attrib.get('t')
                value_node = cell.find(f'{NS}v')
                if cell_type == 'inlineStr':
                    value = ''.join(t.text or '' for t in cell.iter(f'{NS}t'))
                elif value_node is None:
                    value = ''
                elif cell_type == 's':
                    value = shared[int(value_node.text)]
                else:
                    value = value_node.text or ''
                out[idx] = value
            rows.append(out)
        return rows
