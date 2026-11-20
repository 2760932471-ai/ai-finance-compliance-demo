"""Read-only, repeatable acceptance check for exported examination results."""
import hashlib,json,re
from collections import Counter
from pathlib import Path
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]

def verify():
    outputs=[]
    for label in ('案例1','案例2'):
        folder=ROOT/'deliverables'/label
        def read(name):return json.loads((folder/name).read_text(encoding='utf-8'))
        manifest=read('文件清单.json');es=read('识别结果.json');rs=read('当前审核结果.json');trace=read('执行记录.json');review=read('复核记录.json')
        assert len(rs)==65 and len({r['id'] for r in rs})==65,'规则不完整'
        assert all(r['execution']=='completed' for r in rs),'含未完成技术项'
        assert all(r['verdict'] in ('通过','不通过','待人工判断') for r in rs),'状态枚举错误'
        evidence_ids={e['id'] for e in es}
        assert all(set(r['evidence_ids'])<=evidence_ids for r in rs),'含不存在的证据'
        assert all(r['evidence_ids'] or r.get('missing') for r in rs),'无证据且无缺失说明'
        assert all(r['verdict']!='通过' or r['evidence_ids'] for r in rs),'通过无依据'
        html=(folder/'审核报告.html').read_text(encoding='utf-8')
        assert 'AI财务审核' in html and '衡审' not in html,'报告品牌未更新'
        snapshot=read('报告快照.json');integrity=read('报告文件校验.json')
        assert snapshot['schema_version']=='audit-report-v1','快照版本错误'
        assert snapshot['results']==rs,'快照与审核结果不一致'
        assert snapshot['summary']==trace['summary'],'快照与执行汇总不一致'
        assert not re.search(r'[A-Za-z]:[\\/]',json.dumps(snapshot,ensure_ascii=False)),'快照泄露本机绝对路径'
        for fmt,meta in integrity['formats'].items():
            payload=(folder/('报告快照.json' if fmt=='json' else '审核报告.'+fmt)).read_bytes()
            assert hashlib.sha256(payload).hexdigest()==meta['sha256'],'导出摘要不一致'
            assert len(payload)==meta['bytes'],'导出长度不一致'
        assert integrity['snapshot_sha256']==integrity['formats']['json']['sha256'],'快照摘要不一致'
        pdf_path=folder/'审核报告.pdf'
        assert pdf_path.read_bytes().startswith(b'%PDF-'),'PDF文件头无效'
        reader=PdfReader(pdf_path)
        pdf_text='\n'.join(p.extract_text() for p in reader.pages)
        assert all(r['id'] in pdf_text for r in rs),'PDF缺少规则'
        assert all(e in pdf_text for e in evidence_ids),'PDF缺少证据'
        assert '待人工确认' in pdf_text and '文字摘录' in pdf_text,'PDF状态或摘录标记缺失'
        assert len(reader.pages)<100 and reader.outline,'长报告分页或书签异常'
        assert all(abs(float(p.mediabox.width)-595.28)<1 for p in reader.pages),'不是A4版式'
        local=read('本地程序核验.json')
        assert local['version']=='local-evidence-checks-v1','缺少本地核验版本'
        assert all(x['evidence_id'] in evidence_ids for x in local['calendar']),'日期核验引用不存在'
        assert all(x['status'] in ('matched','conflict','invalid','unavailable') for x in local['calendar']),'日期核验状态无效'
        assert all(x['status'] in ('read','error','unavailable') for x in local['photo_metadata']),'EXIF读取状态无效'
        assert '日期星期与原图 EXIF 本地核验' in html,'报告缺少本地核验'
        if local.get('supplemental'):assert local['scope_note'] in html,'后补核验未注明未参与原判'
        assert '<script' not in html.lower(),'报告含执行脚本'
        assert len(re.findall(r'src="data:image/jpeg;base64,',html))==36,'报告原件预览不全'
        assert all(f'id="{e}"' in html for e in evidence_ids),'报告证据索引不全'
        assert trace['summary']['complete'] and trace['quality']['passed'],'完整性检查未通过'
        raster=[e for e in es if e.get('raw_method')=='Windows OCR']
        assert len(raster)==31,'本轮扫描件和照片的转写覆盖不全'
        assert trace.get('recognition_version')=='visual-transcription-v2','未使用新版识别流程'
        assert all(e.get('recognition',{}).get('status') in ('readable','partial','no_text','unreadable') for e in raster),'有转写技术未完成项'
        assert all('raw_text' in e and e['method']=='AI视觉转写' for e in raster),'未分别保留转写与原始OCR'
        assert all(e.get('recognition',{}).get('uncertain_regions') for e in raster if e['recognition']['status'] in ('partial','unreadable')),'不清区域没有说明'
        decision=trace.get('decision_review',{})
        assert decision.get('provider') in ('off','jev','startlux'),'缺少决策服务状态'
        if decision.get('provider')!='off':
            assert decision['status']=='已完成' and not decision['errors'],'决策复核未完整'
            selected=[r for r in rs if decision.get('scope')!='p0' or r['risk']=='P0']
            assert decision['count']==decision['target_count']==len(selected),'决策覆盖与声明范围不一致'
            assert all(r.get('decision',{}).get('meta',{}).get('input_hash') for r in selected),'缺少真实决策调用记录'
            original={r['id']:r for r in read('AI原始审核结果.json')}
            if decision['mode']=='shadow':assert all(r['verdict']==original[r['id']]['verdict'] for r in rs),'对比模式改写了原判'
        assert not review['confirmed_at'],'交付样例尚无人工签署，不应显示已确认'
        sha=sorted(m['sha256'] for m in manifest if m['status']=='业务文件')
        outputs.append({'case':label,'run_id':trace['run_id'],'files':len(manifest),'unique_documents':len(sha),'evidence':len(es),'verdicts':dict(Counter(r['verdict'] for r in rs)),'pdf_pages':len(reader.pages),'report_id':snapshot['id'],'sha':sha,'ids':sorted(evidence_ids)})
    assert outputs[0]['sha']==outputs[1]['sha'],'包装变体业务内容不一致'
    assert outputs[0]['ids']==outputs[1]['ids'],'相同内容的证据索引不一致'
    assert outputs[0]['verdicts']==outputs[1]['verdicts'],'相同输入结果不一致'
    return [{k:v for k,v in o.items() if k not in ('sha','ids')} for o in outputs]

if __name__=='__main__':print(json.dumps({'passed':True,'cases':verify()},ensure_ascii=False,indent=2))
