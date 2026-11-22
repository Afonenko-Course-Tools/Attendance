"""Atomic, dependency-free XLSX export of one readable, flat journal sheet."""

import os
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

from attendance_lab.domain.models import AttendanceError

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCREL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml"


def column_name(number):
    letters = ""
    while number:
        number, rem = divmod(number - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def xml_bytes(root):
    return tostring(root, encoding="utf-8", xml_declaration=True)


def styles_xml():
    styles = Element("styleSheet", xmlns=NS)
    fonts = SubElement(styles, "fonts", count="2")
    font = SubElement(fonts, "font")
    SubElement(font, "sz", val="11")
    SubElement(font, "name", val="Calibri")
    font = SubElement(fonts, "font")
    SubElement(font, "b")
    SubElement(font, "sz", val="11")
    SubElement(font, "name", val="Calibri")
    SubElement(font, "color", rgb="FFFFFFFF")
    fills = SubElement(styles, "fills", count="3")
    for pattern in ("none", "gray125"):
        SubElement(SubElement(fills, "fill"), "patternFill", patternType=pattern)
    fill = SubElement(SubElement(fills, "fill"), "patternFill", patternType="solid")
    SubElement(fill, "fgColor", rgb="FF003E78")
    SubElement(fill, "bgColor", indexed="64")
    borders = SubElement(styles, "borders", count="1")
    border = SubElement(borders, "border")
    for edge in ("left", "right", "top", "bottom", "diagonal"):
        SubElement(border, edge)
    base = SubElement(styles, "cellStyleXfs", count="1")
    SubElement(base, "xf", numFmtId="0", fontId="0", fillId="0", borderId="0")
    formats = SubElement(styles, "cellXfs", count="2")
    for font_id, fill_id in ((0, 0), (1, 2)):
        xf = SubElement(
            formats,
            "xf",
            numFmtId="0",
            fontId=str(font_id),
            fillId=str(fill_id),
            borderId="0",
            xfId="0",
            applyAlignment="1",
        )
        SubElement(xf, "alignment", vertical="top", wrapText="1")
    named = SubElement(styles, "cellStyles", count="1")
    SubElement(named, "cellStyle", name="Normal", xfId="0", builtinId="0")
    return xml_bytes(styles)


def export_xlsx(path: Path, headers, rows):
    headers = list(headers)
    rows = [list(row) for row in rows]
    if not headers or len(headers) > 16384 or len(rows) > 1048575:
        raise AttendanceError("Размер таблицы выходит за ограничения Excel.")
    if any(len(row) != len(headers) for row in rows):
        raise AttendanceError("Строка экспорта не соответствует заголовку.")
    root = Element("worksheet", xmlns=NS)
    views = SubElement(root, "sheetViews")
    view = SubElement(views, "sheetView", workbookViewId="0")
    frozen_columns = min(2, len(headers))
    top_left = f"{column_name(frozen_columns + 1)}2"
    SubElement(
        view,
        "pane",
        xSplit=str(frozen_columns),
        ySplit="1",
        topLeftCell=top_left,
        activePane="bottomRight",
        state="frozen",
    )
    columns = SubElement(root, "cols")
    for index, title in enumerate(headers, 1):
        largest = max(
            (len(str(row[index - 1])) for row in rows if row[index - 1] is not None), default=0
        )
        width = min(60, max(16, len(str(title)) + 2, min(48, largest + 2)))
        if any(word in str(title).lower() for word in ("фио", "тема", "занятие", "специальность")):
            width = max(32, width)
        SubElement(
            columns, "col", min=str(index), max=str(index), width=str(width), customWidth="1"
        )
    data = SubElement(root, "sheetData")
    for row_index, values in enumerate([headers, *rows], 1):
        row = SubElement(data, "row", r=str(row_index))
        if row_index == 1:
            row.set("ht", "48")
            row.set("customHeight", "1")
        for col_index, value in enumerate(values, 1):
            cell = SubElement(
                row, "c", r=f"{column_name(col_index)}{row_index}", s="1" if row_index == 1 else "0"
            )
            if value is None:
                continue
            if isinstance(value, (int, float)) and isfinite(value):
                SubElement(cell, "v").text = str(value)
            else:
                text = str(value)
                if len(text) > 32767 or any(ord(c) < 32 and c not in "\t\n\r" for c in text):
                    raise AttendanceError("Текст ячейки не поддерживается форматом Excel.")
                cell.set("t", "inlineStr")
                SubElement(SubElement(cell, "is"), "t").text = text
    SubElement(root, "autoFilter", ref=f"A1:{column_name(len(headers))}{len(rows) + 1}")
    content_types = Element(
        "Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types"
    )
    SubElement(
        content_types,
        "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    SubElement(content_types, "Default", Extension="xml", ContentType="application/xml")
    for part, kind in (
        ("workbook.xml", "sheet.main+xml"),
        ("worksheets/sheet1.xml", "worksheet+xml"),
        ("styles.xml", "styles+xml"),
    ):
        SubElement(content_types, "Override", PartName="/xl/" + part, ContentType=f"{MAIN}.{kind}")
    relationships = Element("Relationships", xmlns=REL)
    SubElement(
        relationships,
        "Relationship",
        Id="rId1",
        Type=f"{DOCREL}/officeDocument",
        Target="xl/workbook.xml",
    )
    workbook = Element("workbook", xmlns=NS, attrib={"xmlns:r": DOCREL})
    SubElement(
        SubElement(workbook, "sheets"), "sheet", name="Журнал", sheetId="1", attrib={"r:id": "rId1"}
    )
    workbook_rels = Element("Relationships", xmlns=REL)
    SubElement(
        workbook_rels,
        "Relationship",
        Id="rId1",
        Type=f"{DOCREL}/worksheet",
        Target="worksheets/sheet1.xml",
    )
    SubElement(
        workbook_rels, "Relationship", Id="rId2", Type=f"{DOCREL}/styles", Target="styles.xml"
    )
    path = Path(path)
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=".attendance-export-", suffix=".xlsx", delete=False
        ) as handle:
            temporary = Path(handle.name)
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            for name, document in (
                ("[Content_Types].xml", content_types),
                ("_rels/.rels", relationships),
                ("xl/workbook.xml", workbook),
                ("xl/_rels/workbook.xml.rels", workbook_rels),
                ("xl/worksheets/sheet1.xml", root),
            ):
                archive.writestr(name, xml_bytes(document))
            archive.writestr("xl/styles.xml", styles_xml())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
