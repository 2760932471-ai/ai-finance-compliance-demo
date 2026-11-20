"""Local, traceable paragraph chunks and lexical retrieval; no remote embedding calls."""
import hashlib
import math
import re
from collections import Counter
from . import store

DEFAULT={'size':500,'overlap':80,'mode':'paragraph'}

def config(value=None):
    if value is not None and not isinstance(value,dict):raise ValueError('切片配置须为对象')
    c={**DEFAULT,**(value or {})}
    if type(c['size']) is not int or not 100<=c['size']<=2000:raise ValueError('切片长度须为100—2000字符')
    if type(c['overlap']) is not int or not 0<=c['overlap']<c['size']//2:raise ValueError('重叠须小于切片长度的一半')
    if c['mode'] not in ('paragraph','fixed'):raise ValueError('切片方式无效')
    return {k:c[k] for k in DEFAULT}

def chunks(doc,settings=None):
    c=config(settings or doc.get('chunk_config'));text=doc['text'];out=[];start=0
    while start<len(text):
        end=min(len(text),start+c['size'])
        if c['mode']=='paragraph' and end<len(text):
            breaks=[text.rfind(mark,start+c['size']//2,end) for mark in ('\n','。','；')]
            cut=max(breaks)
            if cut>=0:end=cut+1
        part=text[start:end]
        if part.strip():
            digest=hashlib.sha256((doc['id']+doc['version']+str(start)+part).encode()).hexdigest()[:16]
            out.append({'id':doc['id'],'chunk_id':digest,'title':doc['title'],'source':doc.get('source',''),
                'version':doc['version'],'index':len(out)+1,'start':start,'end':end,'line':text.count('\n',0,start)+1,'text':part})
        if end==len(text):break
        start=max(start+1,end-c['overlap'])
    return out

def tokens(text):
    result=re.findall(r'[a-z0-9]+',text.lower())
    for phrase in re.findall(r'[\u4e00-\u9fff]+',text):
        result.extend(phrase[i:i+2] for i in range(len(phrase)-1))
        if len(phrase)==1:result.append(phrase)
    return result

def retrieve(query,top_k=6,method='bm25',docs=None):
    if not isinstance(query,str) or not query.strip() or len(query)>2000:raise ValueError('检索问题须为1—2000字符')
    if type(top_k) is not int or not 1<=top_k<=20:raise ValueError('Top K须为1—20')
    if method not in ('bm25','tfidf','hybrid'):raise ValueError('召回方式无效')
    docs=docs if docs is not None else store.all_items('knowledge')
    corpus=[ch for d in docs if d['status']=='published' for ch in chunks(d)]
    terms=set(tokens(query));counts=[Counter(tokens(c['text'])) for c in corpus];n=len(corpus)
    if not n or not terms:return {'query':query,'method':method,'total_chunks':n,'results':[]}
    df=Counter(t for count in counts for t in count);avg=sum(sum(c.values()) for c in counts)/n or 1
    idf=lambda t:math.log(1+(n-df[t]+.5)/(df[t]+.5))
    qi={t:idf(t) for t in terms if t in df};qnorm=math.sqrt(sum(v*v for v in qi.values())) or 1
    ranked=[]
    for ch,count in zip(corpus,counts):
        hits=terms&count.keys()
        if not hits:continue
        size=sum(count.values());bm=sum(idf(t)*count[t]*2.5/(count[t]+1.5*(.25+.75*size/avg)) for t in hits)
        vec={t:(1+math.log(v))*idf(t) for t,v in count.items()};norm=math.sqrt(sum(v*v for v in vec.values())) or 1
        cosine=sum(vec[t]*qi[t] for t in hits)/(norm*qnorm)
        score=bm if method=='bm25' else cosine if method=='tfidf' else .5*bm/(1+bm)+.5*cosine
        ranked.append({**ch,'score':round(score,6),'bm25':round(bm,6),'cosine':round(cosine,6),'matched_terms':sorted(hits)})
    ranked.sort(key=lambda x:(-x['score'],x['chunk_id']))
    return {'query':query,'method':method,'total_chunks':n,'results':ranked[:top_k],
            'definition':'中文双字词/英文词的词法召回；TF-IDF为本地稀疏向量，非语义Embedding；分数不是置信概率。'}

def resegment(id,settings,revision):
    doc=store.get('knowledge',id)
    if doc.get('chunk_revision',1)!=revision:raise ValueError('切片配置已更新，请刷新')
    c=config(settings);before={'config':doc.get('chunk_config',DEFAULT),'revision':doc.get('chunk_revision',1)}
    doc.update(chunk_config=c,chunk_revision=revision+1)
    doc.setdefault('chunk_history',[]).append({**before,'changed_at':store.now()})
    store.put('knowledge',doc)
    return {'document':doc,'chunks':chunks(doc)}
