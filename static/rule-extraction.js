'use strict';

function ruleSourceLocation(locator) {
        if(!locator)return '';
        if(typeof locator==='string')return locator;
        if(locator.type==='row')return '表 '+locator.table+' · 第 '+locator.row+' 行';
        if(locator.clause)return locator.clause;
        if(locator.page)return '第 '+locator.page+' 页';
        return Object.values(locator).filter(v=>typeof v==='string'||typeof v==='number').join(' · ');
    }

function createRuleExtraction(schemeId, editor) {
    const route='/schemes/'+encodeURIComponent(schemeId)+'/extractions';
    const panel=document.createElement('section');
    panel.className='panel extract-panel';
    panel.id='rule-extraction';
    const mirror=!!document.querySelector('.public-mirror');
    const offline=state.capabilities.offline===true;
    const labels={queued:'排队中',processing:'提炼中',completed:'已完成',failed:'未完成'};
    const selected=new Set();
    let jobs=[],job=null,active='',request=0,timer,uploading=false,loading=false,uploadedFile=null,started=false;
    const find=selector=>panel.querySelector(selector);
    const setMessage=(message,error=false)=>{
        const out=find('.extract-message');out.textContent=message;out.classList.toggle('extract-error',error);
    };
    function availability(){
        const file=find('#rule-source-file');
        if(!file)return;
        file.disabled=mirror||offline||uploading||editor.busy();
        find('[data-ex="start"]').disabled=file.disabled||!uploadedFile;
        find('[data-ex="refresh"]').disabled=uploading||editor.busy();
        const adopt=find('[data-ex="adopt"]');
        if(adopt) {
            adopt.disabled=mirror||editor.busy()||editor.dirty()||loading||!selected.size;
            adopt.textContent='加入方案草稿'+(selected.size?'（'+selected.size+' 项）':'');
        }
        const help=find('[data-ex-note]');
        if(help)help.textContent=mirror?'公开镜像仅供查看。':editor.dirty()?'先保存方案草稿中已修改的内容，再加入所选规则。':'加入后可继续编辑规则，并用样本验证。';
    }
    function sourceMarkup(items) {
        return (items || []).map(s=>`<figure class="extract-source"><figcaption>${esc([s.name,ruleSourceLocation(s.locator)||s.segment_id].filter(Boolean).join(' · '))}</figcaption><blockquote>${esc(s.quote)}</blockquote></figure>`).join('');
    }
    function candidatesMarkup() {
        const items=job.candidates || [],draftIds=editor.ids();
        if(!items.length)return '';
        return `<fieldset class="extract-candidates"><legend>候选审核项 · ${items.length}</legend>${items.map(c=>{
            const rule=c.rule,already=draftIds.has(rule.id);
            if(already)selected.delete(c.id);
            return `<article><label class="extract-choice"><input type="checkbox" data-candidate="${esc(c.id)}" ${selected.has(c.id)?'checked':''} ${mirror||already?'disabled':''}><span><strong>${esc(rule.name)}</strong><small>${esc(rule.id)} · ${esc(rule.group)} · ${esc(rule.risk)}${already?' · 草稿已有同编号规则':''}</small></span></label>
            <p>${esc(rule.principle)}</p><details class="scheme-samples"><summary>建议判定条件</summary>${[['通过',rule.pass_condition],['不通过',rule.fail_condition],['转人工',rule.manual_condition]].filter(([,v])=>v).map(([k,v])=>`<p><b>${k}</b> · ${esc(v)}</p>`).join('')}</details>${sourceMarkup(c.sources)}
            ${c.questions?.length?`<p class="extract-question"><b>待确认</b> · ${esc(c.questions.join('；'))}</p>`:''}
            </article>`;
        }).join('')}</fieldset><div class="scheme-row"><span class="tiny muted" data-ex-note></span><button class="btn primary" data-ex="adopt">加入方案草稿</button></div>`;
    }
    function showJob(){
        const target=find('.extract-result');
        if(!job){target.innerHTML='';return;}
        const c=job.coverage;
        target.innerHTML=`<div class="extract-job-head"><strong>${esc(job.filename)}</strong>${!mirror||job.demo?`<a class="btn small" href="/api${route}/${encodeURIComponent(job.id)}/original" download="${esc(job.filename)}">下载来源文件</a>`:''}${badge(job.demo?'演示样例':labels[job.status]||job.status,'code')}</div>
        <p class="tiny muted">${esc(job.stage||'')}${job.progress?.total?' · '+esc(job.progress.done)+' / '+esc(job.progress.total)+' 批':''}${job.updated_at?' · '+date(job.updated_at):''}</p>
        ${['queued','processing'].includes(job.status)?`<progress aria-label="规则提炼进度" ${job.progress?.total?`max="${Number(job.progress.total)}" value="${Number(job.progress.done)||0}"`:''}></progress>`:''}
        ${c ? `<p class="tiny muted">处理 ${esc(c.processed_segments ?? '—')} / ${esc(c.input_segments ?? '—')} 段 · ${esc(c.processed_chars ?? '—')} / ${esc(c.input_chars ?? '—')} 字符${c.rejected?' · '+esc(c.rejected)+' 条未通过出处校验':''}${c.truncated?' · 原文有截断':''}</p>`:''}
        ${job.error?`<p class="extract-error">${esc(job.error)}</p>`:''}
        ${job.warnings?.length?`<ul class="extract-warnings">${job.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}
        ${job.status==='completed'&&!job.candidates?.length?'<p>此文件没有可采纳的候选规则。</p>':''}
        ${job.status==='completed'?candidatesMarkup():''}
        ${job.ambiguities?.length?`<details class="scheme-samples"><summary>待澄清的条款 · ${job.ambiguities.length}</summary>${job.ambiguities.map(a=>`<p>${esc(a.question)}</p>${sourceMarkup(a.sources)}`).join('')}</details>`:''}`;
        availability();
    }
    function showList(){
        const selector=find('[data-ex-history]');
        selector.innerHTML=jobs.length?jobs.map(j=>`<option value="${esc(j.id)}" ${j.id===active?'selected':''}>${esc(j.filename)} · ${esc(j.demo?'演示样例':labels[j.status]||j.status)}${j.created_at?' · '+date(j.created_at):''}</option>`).join(''):'<option>暂无提炼记录</option>';
        selector.disabled=!jobs.length||uploading;
    }
    async function open(id,reset=true){
        const token=++request;
        clearTimeout(timer);
        active=id;loading=true;
        if(reset){selected.clear();job=null;showJob();setMessage('正在读取任务…');}
        try{
            const response=await api(route+'/'+encodeURIComponent(id));
            if(token!==request||!panel.isConnected)return;
            job=response;loading=false;
            const summary=jobs.find(j=>j.id===id);
            if(summary)summary.status=job.status;
            showList();showJob();setMessage('');
            if(['queued','processing'].includes(job.status))timer=setTimeout(()=>{if(panel.isConnected)open(id,false);},1800);
        }catch(error){if(token===request&&panel.isConnected){loading=false;setMessage('读取任务失败 · '+error.message,true);availability();}}
    }
    async function list(){
        const token=++request;
        clearTimeout(timer);setMessage('正在读取提炼记录…');
        try{
            const response=await api(route);
            if(token!==request||!panel.isConnected)return;
            jobs=response;
            active=jobs.some(j=>j.id===active)?active:jobs[0]?.id||'';
            showList();
            if(active)await open(active);
            else {job=null;selected.clear();showJob();setMessage('');}
        }catch(error){if(token===request&&panel.isConnected)setMessage('读取提炼记录失败 · '+error.message,true);}
    }
    async function start(){
        if(mirror||offline||!uploadedFile||uploading)return;
        const file=uploadedFile;
        uploading=true;availability();
        setMessage('正在提交 '+file.name+'…');
        clearTimeout(timer);++request;
        try{
            const result=await api(route,'POST',file,{'X-Filename':encodeURIComponent(file.name)});
            if(!panel.isConnected)return;
            jobs=[result,...jobs.filter(j=>j.id!==result.id)];
            active=result.id;uploadedFile=null;find('#rule-source-file').value='';
            showList();await open(result.id);
        }catch(error){if(panel.isConnected)setMessage('提交失败 · '+error.message,true);}
        finally{uploading=false;if(panel.isConnected){showList();availability();}}
    }
    panel.innerHTML=`<div class="panel-head"><div><h2>从文件提炼审核规则</h2><p class="section-description">上传制度、标准或合同模板，对照原文确认可执行的审核项。</p></div></div>
    <div class="panel-pad scheme-editor">
    ${mirror?'<p class="scheme-empty">公开预览可查看样例文件的候选规则。上传和采纳请使用本地版。</p>':offline?'<p class="scheme-empty">当前为离线模式。已完成的提炼记录可查看和采纳，启用 AI 模式后可上传新文件。</p>':''}
    <div class="extract-upload"><label>规则来源文件<input type="file" id="rule-source-file" accept=".docx,.pdf,.txt,.md,.png,.jpg,.jpeg"></label><button class="btn" data-ex="start">开始提炼</button></div><small class="tiny muted">支持 DOCX、PDF、TXT、Markdown、PNG 和 JPG，最大 12 MiB。</small>
    <div class="extract-upload"><label>最近提炼记录<select data-ex-history aria-label="最近提炼记录"><option>正在读取…</option></select></label><button class="btn small" data-ex="refresh">刷新记录</button></div>
    <p class="extract-message" role="status"></p><div class="extract-result" aria-live="polite"></div></div>`;
    availability();
    panel.addEventListener('change',event=>{
        const el=event.target;
        if(el.matches('#rule-source-file')){
            const f=el.files[0];
            uploadedFile=null;setMessage('');
            if(f){
                if(!/\.(docx|pdf|txt|md|png|jpe?g)$/i.test(f.name)||f.size>12*1024*1024||!f.size){
                    el.value='';setMessage('请选择支持格式的非空文件，大小须在 12 MiB 以内。',true);
                }else uploadedFile=f;
            }
            availability();
        }
        if(el.matches('[data-ex-history]'))open(el.value);
        if(el.matches('[data-candidate]')){if(el.checked)selected.add(el.dataset.candidate);else selected.delete(el.dataset.candidate);availability();}
    });
    panel.addEventListener('click',async event=>{
        const action=event.target.closest('[data-ex]')?.dataset.ex;
        if(action==='refresh'&&!uploading)await list();
        if(action==='start')await start();
        if(action==='adopt'&&!mirror&&!editor.dirty()&&!editor.busy()&&selected.size&&job?.status==='completed'){
            try{await editor.adopt(route+'/'+encodeURIComponent(job.id)+'/adopt',[...selected]);selected.clear();showJob();setMessage('已加入草稿。可在下方审核项中编辑并添加试跑样本。');}
            catch(error){if(panel.isConnected){setMessage(error.message,true);availability();}}
        }
    });
    return {
        mount(target){target.replaceWith(panel);if(!started){started=true;list();}else showJob();},
        sync:availability
    };
}
