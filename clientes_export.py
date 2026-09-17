"""Small native XLSX export; all user-provided text is stored as text, never formulas."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
import re


def clientes_sin_ventana_xlsx(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 100000:
        raise ValueError("La lista debe contener entre 1 y 100000 clientes.")
    headers = ["Cliente", "Nombre", "Visitas sin ventana", "Motivo"]
    sheet_rows = []
    def text_cell(ref, value, header=False):
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))[:32767]
        return f'<c r="{ref}" t="inlineStr" s="{1 if header else 0}"><is><t xml:space="preserve">{escape(text)}</t></is></c>'
    sheet_rows.append('<row r="1">' + ''.join(text_cell(f'{col}1', value, True) for col, value in zip('ABCD', headers)) + '</row>')
    for index, row in enumerate(rows, 2):
        if not isinstance(row, dict):
            raise ValueError("Cliente invalido.")
        count = row.get("pedidos")
        if type(count) is not int or count < 0 or count > 1000000000:
            raise ValueError("Cantidad de visitas invalida.")
        for key in ("cliente", "nombre", "motivo"):
            if not isinstance(row.get(key), (str, int)):
                raise ValueError("Datos de cliente invalidos.")
        cells = text_cell(f'A{index}', row['cliente']) + text_cell(f'B{index}', row['nombre'])
        cells += f'<c r="C{index}"><v>{count}</v></c>' + text_cell(f'D{index}', row['motivo'])
        sheet_rows.append(f'<row r="{index}">{cells}</row>')
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    files = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Clientes sin ventana" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml': f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF15233B"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="1" width="18" customWidth="1"/><col min="2" max="2" width="42" customWidth="1"/><col min="3" max="3" width="23" customWidth="1"/><col min="4" max="4" width="50" customWidth="1"/></cols><sheetData>{"".join(sheet_rows)}</sheetData><autoFilter ref="A1:D{len(rows)+1}"/></worksheet>'
    }
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, xml in files.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + xml)
    return output.getvalue()
