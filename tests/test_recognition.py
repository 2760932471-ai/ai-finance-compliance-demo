"""V0.2 acceptance cases written before implementing the visual transcription stage."""
import copy,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import recognition,model

class RecognitionTests(unittest.TestCase):
    def page(self,**kw):
        return {'id':'D001-P1','text':'14:05\n2026年04月18日 星期六','readability':'readable','uncertain_regions':[],**kw}
    def test_rejects_missing_duplicate_and_unknown_ids(self):
        for pages in [[],[self.page(),self.page()],[self.page(id='invented')]]:
            with self.subTest(pages=pages),self.assertRaises(ValueError):recognition.validate({'pages':pages},['D001-P1'])
    def test_rejects_invented_confidence_or_empty_readable(self):
        for row in [self.page(confidence=.99),self.page(text=''),self.page(readability='partial'),self.page(readability='no_text'),self.page(uncertain_regions=['看不清'])]:
            with self.subTest(row=row),self.assertRaises(ValueError):recognition.validate({'pages':[row]},['D001-P1'])
    def test_no_text_and_partial_are_honest(self):
        for row in [self.page(text='',readability='no_text'),self.page(text='2026年[看不清]',readability='partial',uncertain_regions=['水印月份反光'])]:
            self.assertEqual(recognition.validate({'pages':[row]},['D001-P1'])[0],row)
    def test_service_failure_keeps_raw_separate(self):
        e={'id':'D001-P1','text':'乱码候选','lines':[{'text':'乱码候选'}],'method':'Windows OCR','error':'','confirmed':False}
        original=copy.deepcopy(e);recognition.preserve_raw(e);recognition.unavailable(e,'服务不可用')
        self.assertEqual(e['raw_text'],original['text']);self.assertEqual(e['raw_lines'],original['lines'])
        self.assertEqual(e['text'],'');self.assertEqual(e['recognition']['status'],'needs_review');self.assertFalse(e['confirmed'])
    def test_visual_transcription_does_not_claim_human_confirmation(self):
        e={'id':'D001-P1','text':'错字','lines':[],'method':'Windows OCR','error':''}
        recognition.preserve_raw(e);recognition.apply(e,self.page(),{'cached':True,'original_job':'test'})
        self.assertEqual(e['raw_text'],'错字');self.assertIn('14:05',e['text']);self.assertFalse(e['confirmed'])
        self.assertEqual(e['lines'],[]);self.assertTrue(e['recognition']['meta']['cached'])
    def test_preprocessing_preserves_original_and_records_crop(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);source=p/'original.png';Image.new('RGB',(1600,2400),'white').save(source);before=source.read_bytes()
            paths,records=recognition.image_inputs({'id':'D001-P1','page':1,'image':str(source)}, {'type':'.png','path':str(source)},p/'derived')
            self.assertEqual(source.read_bytes(),before);self.assertEqual(len(paths),2)
            self.assertEqual(records[0]['region'],[0,0,1,1]);self.assertEqual(records[1]['region'],[0,.65,1,1])
            self.assertTrue(all(Path(x).exists() for x in paths));self.assertTrue(all(len(x['sha256'])==64 for x in records))
    def test_progress_distinguishes_failed_batch_from_next_model_wait(self):
        evidence=[{'id':f'E{i}','method':'Windows OCR','image':'preview','document_id':'D'} for i in range(9)]
        doc={'id':'D'}
        progress=[]
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ',{'AUDIT_VISUAL_OCR':'on','AUDIT_VISUAL_WORKERS':'1'}), patch.object(recognition,'image_inputs',return_value=(['full','bottom'],[{'region':[]},{'region':[]}])):
            def infer(prompt, schema, images, timeout):
                if len(images)==16:
                    raise RuntimeError('模型调用超时')
                return {'pages':[self.page(id='E8')]}, {}
            recognition.enhance(evidence,[doc],Path(folder),lambda i,n,m:progress.append((i,m)),infer=infer)
        self.assertTrue(any(i==8 and '模型调用超时' in m for i,m in progress))
        self.assertTrue(any(i==8 and '第 9–9 页' in m and '等待模型' in m for i,m in progress))
        self.assertIn('已取得 1 页',progress[-1][1])
        self.assertEqual(evidence[0]['recognition']['status'],'needs_review')

    def test_two_model_batches_can_finish_out_of_order_without_mixing_evidence(self):
        evidence=[{'id':f'E{i}','method':'Windows OCR','image':'preview','document_id':'D'} for i in range(17)]
        first=threading.Event()
        second_recorded=threading.Event()
        records=[]
        counts=[]
        def infer(prompt,schema,images,timeout):
            entries=json.loads(prompt.split('证据与附图顺序：')[-1])
            ids=[row['id'] for row in entries]
            if ids[0]=='E0':
                first.set()
                if not second_recorded.wait(3): raise RuntimeError('未实现并行识别')
            elif ids[0]=='E8':
                if not first.wait(3): raise RuntimeError('未启动第一批')
            return {'pages':[self.page(id=i,text='文字'+i) for i in reversed(ids)]},{'job_id':ids[0]}
        def record(meta):
            records.append(meta)
            if meta.get('job_id')=='E8': second_recorded.set()
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ',{'AUDIT_VISUAL_OCR':'on','AUDIT_VISUAL_WORKERS':'2'}), patch.object(recognition,'image_inputs',return_value=(['full','bottom'],[{'region':[]},{'region':[]}])):
            recognition.enhance(evidence,[{'id':'D'}],Path(folder),lambda i,n,m:counts.append(i),record,infer)
        self.assertEqual(records[0]['job_id'],'E8')
        self.assertEqual([e['text'] for e in evidence],['文字E'+str(i) for i in range(17)])
        self.assertEqual(counts,sorted(counts))
        self.assertEqual(counts[-1],17)

    def test_model_prompt_carries_uncertainty(self):
        e={'id':'D001-P1','name':'photo.jpg','page':1,'category':'现场照片','method':'AI视觉转写','text':'[看不清]','error':'','image':'yes','recognition':{'status':'partial','uncertain_regions':['日期模糊']}}
        prompt=model.rule_prompt([], [e],[],{})
        self.assertIn('日期模糊',prompt);self.assertIn('recognition',prompt)

if __name__=='__main__':unittest.main()
