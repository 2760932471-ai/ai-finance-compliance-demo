import hashlib, json, os, re, stat, subprocess, zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from PIL import Image, ImageOps
from pypdf import PdfReader
import pypdfium2 as pdfium
from .store import DATA, ROOT

MAX_UPLOAD=160*1024*1024
MAX_TOTAL=600*1024*1024
MAX_ENTRIES=3000
MAX_PAGES=100
Image.MAX_IMAGE_PIXELS=90_000_000

class StaticHTML(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.depth=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'): self.depth+=1
        if tag in ('tr','p','div','br','h1','h2','h3','li'): self.parts.append('\n')
        if tag in ('td','th'): self.parts.append(' | ')
        if tag=='input' and not self.depth:
            d=dict(attrs)
            if d.get('type') not in ('hidden','password'): self.parts.append(d.get('value',''))
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'): self.depth=max(0,self.depth-1)
    def handle_data(self,data):
        if not self.depth: self.parts.append(data)
def html_text(data):
    p=StaticHTML(); p.feed(decode_text(data))
    return re.sub(r'\n\s*\n+', '\n', ''.join(p.parts)).strip()
def decode_text(data):
    for enc in ('utf-8-sig','gb18030','utf-16'):
        try:return data.decode(enc)
        except UnicodeError:pass
    return data.decode('utf-8',errors='replace')
def digest(data):return hashlib.sha256(data).hexdigest()
def repair_name(info):
    n=info.filename
    if not info.flag_bits & 0x800:
        try:
            raw=n.encode('cp437')
            for enc in ('utf-8','gb18030'):
                try: n=raw.decode(enc);break
                except UnicodeError:pass
        except UnicodeError:pass
    return n.replace('\\','/')
def safe_name(n):
    p=PurePosixPath(n)
    if '\x00' in n or n.startswith(('/','\\')) or re.match(r'^[A-Za-z]:',n) or ':' in n or '..' in p.parts:
        raise ValueError('压缩包含有不安全路径，已拒绝')
    return n
def classify(name,text=''):
    s=name+' '+re.sub(r'(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])','',text[:1200])
    if '现场照片' in name:return '现场照片'
    if '银行' in s or '电子回单' in s or ('付款人' in s and '收款人' in s):return '支付凭证'
    if '面单' in name:return '报销面单'
    if '合同' in s or '旅行社' in name:return '合同'
    if 'BX' in name or 'BX市场费用报销单' in s:return '报销主单'
    if '发票' in s:return '发票'
    if '方案' in name or name.lower().endswith(('.htm','.html')):return '活动方案'
    return '辅助凭证'

def unpack(source, folder):
    folder.mkdir(exist_ok=True,parents=True);manifest=[];docs=[];seen={};total=0
    with zipfile.ZipFile(source) as z:
        infos=z.infolist()
        if len(infos)>MAX_ENTRIES:raise ValueError('文件条目过多')
        for index,i in enumerate(infos):
            name=safe_name(repair_name(i))
            if stat.S_ISLNK(i.external_attr>>16):raise ValueError('不支持压缩包中的符号链接')
            if i.flag_bits & 1:raise ValueError('不支持加密压缩包')
            total+=i.file_size
            if total>MAX_TOTAL or i.file_size>MAX_UPLOAD or (i.file_size>1024*1024 and i.file_size/max(1,i.compress_size)>250):raise ValueError('解压大小或压缩比超出安全限制')
            row={'index':index,'original_name':i.filename,'name':name,'size':i.file_size,'sha256':'','status':'','document_id':''}
            if i.is_dir():row['status']='目录';manifest.append(row);continue
            if '__MACOSX' in name.split('/') or Path(name).name.startswith('._') or Path(name).name=='.DS_Store':
                row['status']='忽略系统文件';manifest.append(row);continue
            data=z.read(i);sha=digest(data);row['sha256']=sha
            suffix=Path(name).suffix.lower()
            resource=any(p.endswith('_files') for p in PurePosixPath(name).parts)
            business=not resource and suffix in ('.pdf','.jpg','.jpeg','.png','.htm','.html')
            if business and sha in seen:
                row.update(status='重复业务文件',document_id=seen[sha]);manifest.append(row);continue
            ident=f'D{len(docs)+1:03d}' if business else f'R{index:04d}'
            stored=folder/(sha+suffix)
            if not stored.exists():stored.write_bytes(data)
            row.update(status='业务文件' if business else '配套资源',document_id=ident)
            if business:
                seen[sha]=ident;docs.append({'id':ident,'name':name,'sha256':sha,'size':len(data),'path':str(stored),'type':suffix,'category':classify(name),'status':'待识别','pages':0,'error':''})
            elif resource and suffix in ('.htm','.html'):
                # Linked offline HTML may contain the actual form. Treat as plan pages, not new business files.
                row['text']=html_text(data)[:100000]
            manifest.append(row)
    if not docs:raise ValueError('未发现支持的PDF、HTML或图片业务材料')
    # Stable evidence identifiers make packaging variants comparable without trusting names.
    docs.sort(key=lambda d:d['sha256'])
    mapping={d['id']:f'D{i+1:03d}' for i,d in enumerate(docs)}
    for d in docs:d['id']=mapping[d['id']]
    for row in manifest:
        if row['status'] in ('业务文件','重复业务文件'):row['document_id']=mapping[row['document_id']]
    return manifest,docs

def thumbnail(im,path,limit=1900):
    im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((limit,limit));im.save(path,'JPEG',quality=88)
    return im.size

def recognize(docs,manifest,folder,progress):
    evidence=[];ocr_jobs=[];cache_dir=DATA/'recognition-cache';cache_dir.mkdir(exist_ok=True)
    for idx,d in enumerate(docs):
        progress(idx,len(docs),d['name'])
        file=Path(d['path']);pages=[]
        try:
            if d['type']=='.pdf':
                reader=PdfReader(file);pdf=pdfium.PdfDocument(str(file))
                if len(reader.pages)>MAX_PAGES:raise ValueError('PDF页数超出限制')
                for j,page in enumerate(reader.pages):
                    txt=page.extract_text() or '';p=pdf[j];w,h=p.get_size()
                    dest=folder/f'{d["id"]}_p{j+1}.jpg'
                    bitmap=p.render(scale=min(2.0,1900/max(w,h)));image=bitmap.to_pil();thumbnail(image,dest);bitmap.close();p.close()
                    pages.append((txt,dest,'PDF文本' if len(txt.strip())>25 else 'Windows OCR'))
                pdf.close()
            elif d['type'] in ('.htm','.html'):
                txt=html_text(file.read_bytes())
                resources=sorted([(Path(m['name']).name,m['text']) for m in manifest if m.get('text') and len(m['text'].strip())>50],key=lambda x:(x[0],digest(x[1].encode())))
                # Keep each distinct linked body as an explicit virtual page with its own source path.
                pages.append((txt,None,'HTML静态文本'))
                seen={txt}
                for name,t in resources:
                    if t not in seen:pages.append((f'离线配套正文来源：{name}\n{t}',None,'HTML配套正文'));seen.add(t)
            else:
                dest=folder/f'{d["id"]}_p1.jpg'
                with Image.open(file) as im:
                    exif=im.getexif();d['exif_present']=bool(exif);d['image_size']=list(im.size)
                    d['exif']={str(k):str(v)[:200] for k,v in exif.items() if k in (271,272,306,36867,34853)}
                    thumbnail(im,dest)
                pages.append(('',dest,'Windows OCR'))
            for j,(txt,img,method) in enumerate(pages):
                e={'id':f'{d["id"]}-P{j+1}','document_id':d['id'],'name':d['name'],'page':j+1,'category':d['category'],'text':txt,'image':str(img) if img else '', 'method':method,'lines':[],'error':'','confirmed':False,'sha256':d['sha256']}
                if method=='Windows OCR':
                    key=digest(img.read_bytes())+'_windows_v1';e['cache_key']=key
                    cached=cache_dir/(key+'.json')
                    if cached.exists():
                        v=json.loads(cached.read_text(encoding='utf-8'));e.update(text=v['text'],lines=v['lines'],error=v['error']);e['cached']=True
                    else:ocr_jobs.append({'id':e['id'],'path':str(img.resolve())})
                evidence.append(e)
            d.update(status='已识别',pages=len(pages))
        except Exception as exc:
            d.update(status='识别失败',error=str(exc)[:300])
            if not any(e['document_id']==d['id'] for e in evidence):
                evidence.append({'id':d['id']+'-P1','document_id':d['id'],'name':d['name'],'page':1,'category':d['category'],'text':'','image':'','method':'识别失败','lines':[],'error':d['error'],'confirmed':False,'sha256':d['sha256']})
    if ocr_jobs:
        inp=folder/'ocr-input.json';out=folder/'ocr-output.json';inp.write_text(json.dumps(ocr_jobs,ensure_ascii=False),encoding='utf-8')
        ps=Path(os.environ.get('WINDIR','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            p=subprocess.run([str(ps),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'app/ocr.ps1'),'-InputFile',str(inp.resolve()),'-OutputFile',str(out.resolve())],capture_output=True,timeout=360,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if p.returncode or not out.exists():raise RuntimeError('Windows OCR不可用；请安装中文OCR语言包或配置识别服务')
            vals=json.loads(out.read_text(encoding='utf-8-sig'));vals=vals if isinstance(vals,list) else [vals]
            by={x['id']:x for x in vals}
            for e in evidence:
                if e['id'] in by:
                    v=by[e['id']];e.update(text=v['text'],lines=v.get('lines') or [],error=v.get('error',''))
                    if not e['error']:(cache_dir/(e['cache_key']+'.json')).write_text(json.dumps(v,ensure_ascii=False),encoding='utf-8')
        except Exception as exc:
            ids={j['id'] for j in ocr_jobs}
            for e in evidence:
                if e['id'] in ids:e['error']=str(exc)[:250]
    for d in docs:
        es=[e for e in evidence if e['document_id']==d['id']]
        d['category']=classify(d['name'],'\n'.join(e['text'] for e in es))
        for e in es:e['category']=d['category']
        if any(e['error'] for e in es):d['status']='部分识别失败';d['error']='存在未成功识别页面，请重试或人工核对'
    return evidence
