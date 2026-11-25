/* The static host only serves precomputed, explicitly public fixtures. */
(() => {
  const nativeFetch = window.fetch.bind(window);
  const site = new URL('./', location.href);
  const snapshot = nativeFetch(new URL('preview-data.json', site)).then(response => {
    if (!response.ok) throw Error('预览数据未加载，请刷新页面。');
    return response.json();
  });
  const offline = '这里是只读预览。请在本地完整版中保存、上传或执行业务操作。';
  const json = (data, status = 200) => new Response(JSON.stringify(data), {
    status, headers: {'Content-Type':'application/json; charset=utf-8'}
  });
  function pathBetween(graph, source, target) {
    if (!graph.nodes.some(node => node.id === source) || !graph.nodes.some(node => node.id === target)) return [];
    const previous = new Map([[source, null]]), queue = [source];
    for (let i=0; i<queue.length; i++) {
      const node = queue[i];
      if (node===target) {
        const path = [];
        for(let current=node; current!==null; current=previous.get(current)) path.unshift(current);
        return path;
      }
      for (const edge of graph.edges) {
        const next = edge.source===node ? edge.target : (edge.target===node ? edge.source : null);
        if (next!==null && !previous.has(next)) {
          previous.set(next,node);
          queue.push(next);
        }
      }
    }
    return [];
  }
  function publicCalculation(data, path, args) {
    const reads = data.reads;
    switch (path) {
      case '/api/model/diagnostic':
        return json(reads['/api/bootstrap'].model_diagnostic);
      case '/api/knowledge/recall': {
        const corpus = data.corpora[args.scheme_id || 'finance-t1'] || [];
        try {
          return json(window.previewRecall(args.query, args.top_k || 6, args.method || 'bm25', corpus));
        } catch (error) {
          return json({error:error.message},400);
        }
      }
      case '/api/compliance/lab/run': {
        const id=data.models[args.model_id];
        if(args.start==='2026-10' && args.end==='2026-10' && id)
          return json(reads['/api/compliance/lab/run?id='+id]);
        return json({error:'预览已备好 2026-10 的评测结果。其他期间请在本机运行。'},400);
      }
      case '/api/compliance/run':
        return json(reads[path]);
      case '/api/compliance/tools/call': {
        if(args.name==='graph') return json(data.graph);
        if(Object.hasOwn(data.tools,args.name) && !args.filters) return json(data.tools[args.name]);
        if(args.name==='path') return json({path:pathBetween(data.graph,args.source,args.target)});
        break;
      }
    }
    return json({error:offline},403);
  }
  function key(url) {
    const params = new URLSearchParams(url.search);
    params.sort();
    const query = params.toString();
    return url.pathname + (query ? '?'+query : '');
  }
  let table;
  window.fetch = async (resource, options={}) => {
    const url = new URL(resource?.url || resource,location.href);
    const method = (options.method || resource?.method || 'GET').toUpperCase();
    const api = url.origin===location.origin && url.pathname.startsWith('/api/');
    // A Pages preview must never forward a proposed upload to the host or a remote service.
    if(!api) {
      return ['GET','HEAD'].includes(method) ? nativeFetch(resource,options) : json({error:offline},403);
    }
    if(!['GET','POST'].includes(method) || (method==='POST' && typeof options.body!=='string'))
      return json({error:offline},403);
    try {
      const data=await snapshot;
      if(!table) table=new Map(Object.entries(data.reads).map(([route,value])=>[key(new URL(route,site)),value]));
      if(method==='GET' && table.has(key(url))) {
        const value=table.get(key(url));
        if(typeof value==='string') return new Response(value,{headers:{'Content-Type':'text/plain; charset=utf-8'}});
        return json(value);
      }
      if(method==='GET' && url.pathname==='/api/knowledge/search') {
        const corpus=data.corpora[url.searchParams.get('scheme_id') || 'finance-t1'] || [];
        return json(window.previewRecall(url.searchParams.get('q'),6,'bm25',corpus).results);
      }
      if(method==='POST') {
        let args={};
        if(typeof options.body==='string') {
          try { args=JSON.parse(options.body); } catch { return json({error:'请求格式错误'},400); }
        }
        return publicCalculation(data,url.pathname,args || {});
      }
      return json({error:offline},404);
    } catch (error) {
      return json({error:error.message || '无法读取预览数据。'},503);
    }
  };
  // Handle normal links (also in shadow roots) entirely within the GitHub Pages base path.
  snapshot.then(data=>{
    function translate(event) {
      const target = event.composedPath().find(el=>el instanceof Element && el.matches('[data-action="report"]'));
      if(target && event.type==='click' && typeof state!=='undefined' && state.run?.reports?.length) {
        event.preventDefault();
        event.stopPropagation();
        const report=state.run.reports[0];
        const links=[];
        for(const [fmt, metadata] of Object.entries(report.formats)) {
          const staticUrl=data.downloads[metadata.url];
          if(!staticUrl) continue;
          const link=document.createElement('a');
          link.className='btn';
          link.href=new URL(staticUrl,site).href;
          link.download='虚构审核样例.'+fmt;
          link.textContent='下载 '+fmt.toUpperCase();
          links.push(link.outerHTML);
        }
        modal('下载样例审核报告','<p>以下文件来自该虚构业务的同一次审核快照。</p>',links.join(' '));
        return;
      }
      const anchor = event.composedPath().find(el=>el instanceof HTMLAnchorElement);
      if(!anchor) return;
      const url = new URL(anchor.href,site);
      if(url.origin!==location.origin) return;
      if(/^#workspace\/supervision\/HCD-\d+$/.test(url.hash)) {
        event.preventDefault();
        const message='此操作需要本地完整版。可展开“查看已发起的审核”浏览已保存的定向核验。';
        const note = anchor.closest('section')?.querySelector('[data-public-note]') || document.createElement('p');
        note.dataset.publicNote='1';
        note.setAttribute('role','status');
        note.textContent=message;
        anchor.closest('section')?.append(note);
        return;
      }
      if(url.pathname==='/' && /^#run\/[a-f\d]+(?:\/evidence\/[A-Z\d-]+)?$/.test(url.hash)) {
        anchor.href=new URL(url.hash,site).href;
        return;
      }
      if(!url.pathname.startsWith('/api/')) return;
      const file=data.downloads[url.pathname+url.search];
      if(file) {
        anchor.href=new URL(file,site).href;
        return;
      }
      event.preventDefault();
      if(typeof window.toast==='function') window.toast(offline);
      else alert(offline);
    }
    document.addEventListener('click',translate,true);
    document.addEventListener('auxclick',translate,true);
  }).catch(()=>{});
})();
