"""Extract traceable DOCX body paragraphs and table rows without rendering Word."""
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree

_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_MAX_PART = 12 * 1024 * 1024
_MAX_SECTIONS = 1200


def _text(element):
    return ''.join((node.text or '') if node.tag == _NS+'t' else
                   '\n' if node.tag == _NS+'br' else '\t' if node.tag == _NS+'tab' else ''
                   for node in element.iter()).strip()


def extract(path):
    try:
        with ZipFile(path) as doc:
            part = doc.getinfo('word/document.xml')
            if part.file_size > _MAX_PART:
                raise ValueError('Word 正文过大，请拆分材料')
            raw = doc.read(part)
        if b'<!DOCTYPE' in raw.upper():
            raise ValueError('Word 正文包含不支持的文档声明')
        body = ElementTree.fromstring(raw).find(_NS+'body')
    except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError('无法打开 Word 正文，请另存为标准 DOCX 或 PDF') from exc
    if body is None:
        raise ValueError('Word 缺少正文')
    blocks = []
    paragraph = table = 0
    for element in body:
        if element.tag == _NS+'p':
            paragraph += 1
            text = _text(element)
            if text:
                blocks.append((text, {'type':'clause', 'clause':f'正文段落 {paragraph}'}))
        elif element.tag == _NS+'tbl':
            table += 1
            for row, line in enumerate(element.findall(_NS+'tr'), 1):
                cells = [_text(cell) for cell in line.findall(_NS+'tc')]
                if any(cells):
                    blocks.append((' | '.join(cells), {'type':'row', 'table':str(table), 'row':row}))
        if len(blocks) > _MAX_SECTIONS:
            raise ValueError('Word 段落或表格行过多，请按业务事项拆分')
    if not blocks:
        raise ValueError('Word 没有可提取的正文，请将扫描件转为 PDF 或补充原图')
    return blocks
