"""Rule source intake boundaries and faithful source positions."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from app import rule_sources as source


def docx(body,extras=()):
    buffer=io.BytesIO()
    with ZipFile(buffer,'w',ZIP_DEFLATED) as package:
        package.writestr('word/document.xml',
                         '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                         '<w:body>'+body+'</w:body></w:document>')
        for name,data in extras:
            package.writestr(name,data)
    return buffer.getvalue()


def pdf(*pages):
    buffer=io.BytesIO()
    canvas=Canvas(buffer)
    for text in pages:
        if text is None:
            canvas.drawImage(ImageReader(io.BytesIO(png())),20,20,width=500,height=700)
        elif text:
            canvas.drawString(50,720,text)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def png():
    buffer=io.BytesIO()
    Image.new('RGB',(100,60),'white').save(buffer,format='PNG')
    return buffer.getvalue()


class RuleSourceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.root=Path(temp.name)
        self.addCleanup(temp.cleanup)

    def read(self,name,raw):
        return source.read_source(name,raw,self.root)

    def test_text_and_markdown_keep_lines_encoding_values_and_headers(self):
        text='审核范围\n\n| 检查项目 | 要求 |\r\n| 范围 | 必须小于 500 元 |'
        for name,encoding in [('制度.TXT','gb18030'),('规则.md','utf-16'),('rules.txt','utf-8-sig')]:
            with self.subTest(name=name):
                value=self.read(name,text.encode(encoding))
                self.assertFalse(value['warnings'])
                self.assertEqual([s['locator'] for s in value['segments']],['第1行','第3行','第4行'])
                self.assertEqual(value['segments'][-1],dict(id='S0003',name=name,text='| 范围 | 必须小于 500 元 |',locator='第4行'))

    def test_word_table_and_content_control_preserve_empty_column_and_paragraphs(self):
        body=('<w:sdt><w:sdtContent><w:p><w:r><w:t>签约主体</w:t></w:r></w:p></w:sdtContent></w:sdt>'
              '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>级别</w:t></w:r></w:p></w:tc>'
              '<w:tc><w:p/></w:tc><w:tc><w:p><w:r><w:t>必填项</w:t></w:r></w:p>'
              '<w:p><w:r><w:t>例外须审批</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        value=self.read('审批标准.docx',docx(body,[('word/header1.xml',b'<x/>')]))
        self.assertEqual([(s['locator'],s['text']) for s in value['segments']],
                         [('正文段落1','签约主体'),('表1第1行','级别 |  | 必填项\n例外须审批')])
        self.assertIn('页眉',value['warnings'][0])

    def test_native_pdf_is_offline_and_scanned_pages_are_reported(self):
        with patch.object(source.model,'available',return_value=False), patch.object(source.model,'infer') as infer:
            value=self.read('standard.pdf',pdf('Limit 5 dollars',None,'Approval required',''))
        infer.assert_not_called()
        self.assertEqual([s['locator'] for s in value['segments']],['第1页','第3页'])
        self.assertIn('Approval required',value['segments'][-1]['text'])
        self.assertTrue(any('页码：2' in w for w in value['warnings']))
        self.assertTrue(any('第4页' in w and '未调用' in w for w in value['warnings']))

    def test_vision_uses_existing_adapter_and_preserves_partial_readability(self):
        response={'pages':[{'id':'SRC-P1','text':'必须[看不清]','readability':'partial','uncertain_regions':['条件金额']}]}
        env={'AUDIT_VISUAL_OCR':'on'}
        with patch.dict('os.environ',env), patch.object(source.model,'available',return_value=True), patch.object(source.model,'infer',return_value=(response,{})) as inference:
            value=self.read('标准截图.png',png())
        self.assertEqual(inference.call_count,1)
        self.assertEqual(len(inference.call_args.args[2]),2)
        self.assertIn('视觉转写',value['segments'][0]['locator'])
        self.assertEqual(value['segments'][0]['text'],'必须[看不清]')
        self.assertTrue(any('条件金额' in warning for warning in value['warnings']))
        self.assertEqual(list(self.root.iterdir()),[])

    def test_vision_failure_and_unread_budget_are_visible(self):
        with patch.dict('os.environ',{'AUDIT_VISUAL_OCR':'on'}), patch.object(source.model,'available',return_value=True), patch.object(source.model,'infer',side_effect=RuntimeError('private adapter details')) as infer:
            value=self.read('扫描.pdf',pdf(*[None]*6))
        self.assertFalse(value['segments'])
        self.assertEqual(infer.call_count,1)
        self.assertTrue(any('5、6' in item for item in value['warnings']))
        self.assertTrue(any('1、2、3、4' in item for item in value['warnings']))
        self.assertNotIn('private adapter',str(value))

    def test_limits_reject_bad_input_without_silently_discarding_rules(self):
        for name,body in [('../标准.txt',b'n'),('bad.xls',b'xyz'),('bad.pdf',b'no'),('bad.png',b'no'),
                          ('bad.txt',b'abc\x00bin'),('empty.md',b'  \n'),('bad.docx',b'bad')]:
            with self.subTest(name=name),self.assertRaises(ValueError):
                self.read(name,body)
        with patch.object(source,'MAX_BYTES',5),self.assertRaises(ValueError):
            self.read('large.txt',b'123456')
        with patch.object(source,'MAX_PAGES',2),self.assertRaises(ValueError):
            self.read('large.pdf',pdf('A','B','C'))
        with patch.object(source,'MAX_SEGMENTS',1),self.assertRaises(ValueError):
            self.read('long.md',b'one\ntwo')
        with self.assertRaisesRegex(ValueError,'嵌套'):
            self.read('nested.docx',docx('<w:tbl><w:tr><w:tc><w:tbl/></w:tc></w:tr></w:tbl>'))


if __name__=='__main__':
    unittest.main()
