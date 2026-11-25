'use strict';

// A project is pinned to a publication. Only published schemes enter the upload selector.
async function loadSchemes() {
    const response = await fetch('/api/schemes');
    if (response.status === 404) { state.schemes = []; return; }
    if (!response.ok) throw Error('读取已发布审核方案失败');
    const data = await response.json();
    if (!Array.isArray(data)) throw Error('审核方案列表格式错误');
    state.schemes = data;
}

function getScheme(id = 'finance-t1') {
    const current = state.schemes.find(item => item.id === id);
    if (current) return current;
    const managed = (state.managedSchemes || []).find(item => item.id === id);
    if (managed) return {...managed, rules: []};
    if (id && id !== 'finance-t1') return {id, name: id, rules: [], scope: '', version: ''};
    return {id: 'finance-t1', name: '活动费用审核', scope: '经销商活动费用核验',
        version: state.rules[0]?.version || 'T1-2026.10-v1', rules: state.rules};
}

function enabledRules(scheme) { return (scheme.rules || []).filter(rule => rule.enabled !== false); }
function ruleGroups(rules) {
    const found = new Map();
    for (const rule of rules || []) {
        const id = rule.group || '通用';
        if (!found.has(id)) found.set(id, [id, rule.group_name || groups.find(row => row[0] === id)?.[1] || id, 0]);
        found.get(id)[2]++;
    }
    return [...found.values()];
}
function runExpected(run) {
    return run.summary?.expected ?? run.quality?.expected ?? (run.scheme_snapshot ? enabledRules(run.scheme_snapshot).length :
        (run.scheme_id && run.scheme_id !== 'finance-t1' ? (run.rules?.length ?? '—') : state.rules.length || '—'));
}

function schemeOptions(selected, management = false) {
    const items = new Map((state.schemes.length ? state.schemes : [getScheme()]).map(item => [item.id, item]));
    if (management) for (const item of state.managedSchemes || []) items.set(item.id, item);
    return [...items.values()].map(item => `<option value="${esc(item.id)}" ${selected === item.id ? 'selected' : ''}>${esc(item.name)} · ${item.version ? 'v' + esc(item.version) : '草稿'}</option>`).join('');
}
function schemePicker(id) {
    return `<div class="scheme-picker"><label for="center-scheme">审核方案</label><select id="center-scheme">${schemeOptions(id, ['方案管理','发布记录','知识库管理'].includes(state.rulesTab))}</select></div>`;
}

function reviewQueue() {
    const items = [];
    for (const project of state.projects.filter(p => !p.archived)) {
        const runs = [...(project.runs || [])].sort((a,b) => String(b.created_at).localeCompare(String(a.created_at)));
        const superseded = new Set(runs.map(run => run.parent_run).filter(Boolean));
        for (const run of runs) {
            if (superseded.has(run.id) || run.confirmed_at || ['queued','processing'].includes(run.status)) continue;
            items.push({project, run});
        }
    }
    items.sort((a,b) => String(b.run.updated_at).localeCompare(String(a.run.updated_at)));
    const rows = items.map(({project,run}) => `<tr><td><strong>${esc(project.name)}</strong><small>${esc(run.business_id || run.id)}</small></td><td>${esc(run.scheme_name || project.scheme_name || getScheme(project.scheme_id).name)}</td><td>${badge(run.summary?.verdict || run.phase)}</td><td>${date(run.updated_at)}</td><td>${btn('复核材料','open-run','small',`data-id="${esc(run.id)}"`)}</td></tr>`).join('');
    return `<section class="panel"><div class="panel-head"><div><h2>待复核业务 · ${items.length}</h2><p class="section-description">打开当前审核版本，查看原件、核实结论并确认复核。</p></div><a class="btn small" href="#supervision/tasks">监管核查事项 ↗</a></div>${rows ? `<div class="table-wrap"><table class="table"><thead><tr><th>项目 / 业务</th><th>审核方案</th><th>当前结论</th><th>最近更新</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : empty('当前没有待复核业务','已完成审核且尚待确认的业务将在此列出。')}</section>`;
}

function timingView(timing) {
    const rows = [['cold','未命中模型缓存'], ['mixed','部分复用缓存'], ['cached','全部复用缓存'], ['no_model','无模型调用'], ['failed','模型调用或连接失败']];
    const timingRows = rows.filter(([k]) => timing?.[k]).map(([key,label]) => {
        const b = timing[key];
        return `<div class="bar-row"><span>${label}</span><strong>${b.count ? esc(b.avg_seconds)+' 秒 · '+esc(b.count)+' 次' : '暂无记录'}</strong></div>`;
    });
    const elapsed = state.historyOpenMs;
    timingRows.push(`<div class="bar-row"><span>打开历史结果</span><strong>${elapsed ? esc(elapsed)+' 毫秒 · 本次会话最近一次' : '尚未打开'}</strong></div>`);
    return timingRows.join('');
}

async function renderReviewTasks(host) {
    try {
        const response = await api('/supervision/state');
        if(!host.isConnected)return;
        const tasks = (response.tasks || []).filter(task=>!['已办结','已排除'].includes(task.status));
        const table = tasks.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>核查问题</th><th>付款流水</th><th>责任人</th><th>截止日期</th><th>进度</th><th></th></tr></thead><tbody>${tasks.map(task=>`<tr><td><strong>${esc(task.question)}</strong><small>${esc(task.id)}</small></td><td>${esc(task.event_id)}</td><td>${esc(task.owner)}</td><td>${esc(task.due)}</td><td>${badge(task.status,'code')}</td><td><a class="btn small" href="#supervision/tasks/${encodeURIComponent(task.id)}">办理核查</a></td></tr>`).join('')}</tbody></table></div>` : '<p class="panel-pad scheme-empty">当前没有在办的核查事项。</p>';
        host.innerHTML=`<section class="panel"><div class="panel-head"><h2>监管交办的核查事项 · ${tasks.length}</h2></div>${table}</section>`;
    }catch(error){if(host.isConnected)host.textContent='读取核查事项失败 · '+error.message;}
}

const schemeMethods = {model:'模型与证据核验', compare_fields:'两个字段一致性', required_fields:'必填字段完整性'};
const splitList = text => text.split(/[\n,，、]/).map(v => v.trim()).filter(Boolean);
const draftCache = new Map();
function schemeHistory(detail) {
    const records = [...(detail.history || [])].sort((a,b) => b.version - a.version);
    return `<section class="panel"><div class="panel-head"><h2>发布记录</h2></div>${records.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>版本</th><th>方案</th><th>发布时间</th><th>发布说明</th></tr></thead><tbody>${records.map(v=>`<tr><td>${badge('v'+v.version,'code')}</td><td>${esc(v.name)}</td><td>${date(v.published_at)}</td><td>${esc(v.note || '—')}</td></tr>`).join('')}</tbody></table></div>` : empty('尚无发布记录','草稿通过样本试跑后可发布。')}</section>`;
}
function trialSummary(trial) {
    if (!trial) return '<div class="scheme-empty">运行样本可查看预期结论与实际结论。每个变更的启用项都需覆盖适用样本。</div>';
    return `<div class="scheme-trial-result"><strong>${trial.passed?'试跑通过':'试跑待核实'}</strong> · 预期一致 ${esc(trial.matched)} / ${esc(trial.total)} 条 · 草稿修订 ${esc(trial.revision)}</div>${trial.uncovered_changes?.length?'<p class="scheme-empty">仍需覆盖的变更项 · '+esc(trial.uncovered_changes.join('、'))+'</p>':''}<details class="scheme-samples"><summary>查看样本明细</summary><div class="table-wrap"><table class="table"><thead><tr><th>样本 / 审核项</th><th>预期</th><th>实际</th><th>依据</th></tr></thead><tbody>${(trial.cases || []).map(c=>`<tr><td>${esc(c.sample)}<small>${esc(c.rule_id)}</small></td><td>${esc(c.expected)}</td><td>${esc(c.actual)}</td><td>${esc(c.basis || '—')}</td></tr>`).join('')}</tbody></table></div></details>`;
}

function editorRule(rule,idx,draft) {
    const options=(xs,key) => xs.map(([v,n])=>`<option value="${esc(v)}" ${v===key?'selected':''}>${esc(n)}</option>`).join('');
    const types = draft.material_types || [];
    return `<div class="scheme-rule-fields" data-rule-index="${idx}">
    <div class="scheme-form-grid"><label>审核项编号<input data-r="id" value="${esc(rule.id)}" ${rule.provenance?.kind==="rule_extraction"?"readonly":""}></label><label>审核项名称<input data-r="name" value="${esc(rule.name)}"></label><label>审核组编号<input data-r="group" value="${esc(rule.group)}"></label><label>审核组名称<input data-r="group_name" value="${esc(rule.group_name || rule.group)}"></label>
    <label>风险等级<select data-r="risk">${options(['P0','P1','P2','未指定'].map(v=>[v,v]),rule.risk)}</select></label>
    <label>核验方式<select data-r="method">${options(Object.entries(schemeMethods),rule.method||'model')}</select></label></div>
    ${rule.provenance?.kind==='rule_extraction'?`<details class="scheme-samples" open><summary>提炼来源 · ${esc(rule.provenance.filename)}</summary>${rule.provenance.sources.map(s=>`<figure class="extract-source"><figcaption>${esc(ruleSourceLocation(s.locator)||s.segment_id)}</figcaption><blockquote>${esc(s.quote)}</blockquote></figure>`).join('')}</details>`:''}
    <label>核验要求<textarea data-r="principle" rows="2">${esc(rule.principle)}</textarea></label>
    ${rule.provenance?.kind==='rule_extraction'?`<div class="scheme-form-grid">${[['pass_condition','通过条件'],['fail_condition','不通过条件'],['manual_condition','转人工条件']].map(([k,label])=>`<label>${label}<textarea data-r="${k}" rows="2">${esc(rule[k]||'')}</textarea></label>`).join('')}</div>`:''}
    <label class="rule-field-names" ${!rule.method || rule.method==='model'?'hidden':''}>核验字段（每行一个）<textarea data-r="fields" rows="2">${esc((rule.fields || []).join('\n'))}</textarea></label>
    ${types.length ? `<fieldset><legend>限定适用材料（留空时核验整笔业务）</legend><div class="scheme-checks">${types.map(type=>`<label><input type="checkbox" data-r-apply value="${esc(type)}" ${rule.applies_to?.includes(type)?'checked':''}>${esc(type)}</label>`).join('')}</div></fieldset>` : ''}
    <div class="scheme-rule-actions"><label class="scheme-inline"><input type="checkbox" data-r="enabled" ${rule.enabled===false?'':'checked'}>参与审核</label><button type="button" class="btn small" data-cmd="remove-rule">移除此项</button></div></div>`;
}

async function mountScheme(host, id, historyOnly) {
    const route = '/schemes/'+encodeURIComponent(id);
    host.innerHTML = '<p class="scheme-empty" role="status">正在读取方案版本…</p>';
    try {
        const [detail, managed] = await Promise.all([api(route), api('/schemes/manage')]);
        if (!host.isConnected) return;
        state.managedSchemes=managed;
        const select = document.getElementById('center-scheme');
        if (select) select.innerHTML=schemeOptions(id,true);
        if (historyOnly) { host.innerHTML=schemeHistory(detail); return; }
        const knowledge = await api('/knowledge?scheme_id='+encodeURIComponent(id));
        if (!host.isConnected) return;
        setupSchemeEditor(host, detail, knowledge);
    } catch (error) {
        if (host.isConnected) host.innerHTML=`<div class="error-box">${esc(error.message)}</div>`;
    }
}

function setupSchemeEditor(host, detail, knowledge) {
    const cached = draftCache.get(detail.id);
    let draft = cached?.draft || structuredClone(detail.draft || detail.published);
    let revision = cached?.revision ?? detail.revision;
    let dirty = !!cached, selected = 0, trialText = JSON.stringify(detail.sample_examples || [],null,2), samplesChanged=false, note='';
    const route='/schemes/'+encodeURIComponent(detail.id);
    let busy=false;
    const extraction=createRuleExtraction(detail.id,{
        dirty:()=>dirty,busy:()=>busy,ids:()=>new Set(draft.rules.map(r=>r.id)),
        async adopt(path,candidates){
            if(dirty||busy)throw Error('请先保存草稿中已修改的内容。');
            busy=true;host.inert=true;extraction.sync();
            const before=new Set(draft.rules.map(rule=>rule.id));
            try{
                detail=await api(path,'POST',{revision,candidate_ids:candidates});
                revision=detail.revision;draft=structuredClone(detail.draft);
                selected=Math.max(0,draft.rules.findIndex(rule=>!before.has(rule.id)));
                if(host.isConnected)paint();
            }finally{
                busy=false;host.inert=false;if(host.isConnected)syncButtons();
            }
        }
    });
    const docs = (knowledge || []).filter(k => k.status==='published' || draft.knowledge_ids?.includes(k.id));
    function markDirty() {
        dirty=true;
        draftCache.set(detail.id,{draft,revision});
        syncButtons();
    }
    function syncButtons() {
        extraction.sync();
        host.querySelector('[data-cmd="trial"]').disabled=dirty;
        host.querySelector('[data-cmd="publish"]').disabled=dirty || samplesChanged || !detail.trial?.passed || detail.trial.revision!==revision || (detail.published && detail.published.version===draft.version);
        host.querySelector('.scheme-save-state').textContent=dirty?'有未保存的修改':'草稿修订 '+revision;
    }
    function updateRulePanel() {
        const fieldset=host.querySelector('#scheme-rule-editor');
        const rule=draft.rules[selected];
        fieldset.innerHTML=rule ? editorRule(rule,selected,draft) : '<p class="scheme-empty">添加一个审核项开始配置。</p>';
    }
    function paint() {
        const published=detail.published;
        host.innerHTML=`<div class="scheme-stack"><div data-extraction-mount></div><section class="panel"><div class="panel-head"><div><h2>方案草稿</h2><p class="section-description">${published?'已发布 v'+esc(published.version)+' · 现有项目沿用创建时的版本。':'首次发布后，经办人即可选择该方案。'}</p></div><button class="btn small" data-cmd="copy">复制为新方案</button></div>
        <div class="panel-pad scheme-editor"><div class="scheme-form-grid"><label>方案名称<input maxlength="80" data-s="name" value="${esc(draft.name)}" required></label><label>报告标题<input data-s="report" value="${esc(draft.report?.title)}"></label></div>
        <label>适用范围<textarea data-s="scope" rows="2">${esc(draft.scope)}</textarea></label><label>材料类型（每行一个）<textarea data-s="material_types" rows="3">${esc((draft.material_types||[]).join('\n'))}</textarea></label>
        <fieldset><legend>绑定已发布知识</legend><p class="scheme-empty">留空时，审核可检索本方案的全部已发布知识。</p>${docs.length?`<div class="scheme-checks">${docs.map(k=>`<label><input type="checkbox" data-k value="${esc(k.id)}" ${draft.knowledge_ids?.includes(k.id)?'checked':''}>${esc(k.title)}</label>`).join('')}</div>`:'<p class="scheme-empty">当前场景暂无已发布知识。</p>'}</fieldset>
        <div class="scheme-row"><h3>审核项</h3><button class="btn small" data-cmd="add-rule">添加审核项</button></div><label>选择审核项<select id="scheme-rule-selector">${draft.rules.map((r,i)=>`<option value="${i}" ${i===selected?'selected':''}>${esc(r.id)} · ${esc(r.name)}${r.enabled===false?'（停用）':''}</option>`).join('')}</select></label>
        <div id="scheme-rule-editor"></div><div class="scheme-row"><div><span class="scheme-save-state tiny muted"></span> <button class="btn small" data-cmd="reload">读取服务器草稿</button></div><button class="btn primary" data-cmd="save">保存草稿</button></div></div></section>
        <section class="panel"><div class="panel-head"><div><h2>样本试跑与发布</h2><p class="section-description">对照样本的预期结论核验当前修订，核实差异后发布。</p></div></div><div class="panel-pad scheme-editor">
        <div class="scheme-row"><span class="tiny muted">可使用方案内置样本，也可添加业务核实过的样本。</span><button class="btn small" data-cmd="add-sample">添加标注样本</button></div><details class="scheme-samples"><summary>查看或编辑完整样本集</summary><label>样本 JSON<textarea data-trial rows="9" spellcheck="false">${esc(trialText)}</textarea></label></details>
        <div><button class="btn" data-cmd="trial">运行样本试跑</button></div>${trialSummary(detail.trial)}
        <label>发布说明<input data-note value="${esc(note)}" placeholder="记录本版调整的适用范围或规则"></label><div><button class="btn primary" data-cmd="publish">发布版本</button></div></div></section>
        <p role="status" class="scheme-message"></p></div>`;
        extraction.mount(host.querySelector('[data-extraction-mount]'));updateRulePanel();syncButtons();
    }
    paint();
    host.addEventListener('input', e => {
        const el=e.target, key=el.dataset.s, field=el.dataset.r;
        if(el.matches('[data-trial]')){trialText=el.value;samplesChanged=true;syncButtons();return;}
        if(el.matches('[data-note]')){note=el.value;return;}
        if (key) {
            if(key==='report') draft.report={...draft.report,title:el.value};
            else draft[key]=key==='material_types'?splitList(el.value):el.value;
        } else if(el.matches('[data-k]')) {
            draft.knowledge_ids=[...host.querySelectorAll('[data-k]:checked')].map(el=>el.value);
        } else if(el.matches('[data-r-apply]')) {
            draft.rules[selected].applies_to=[...host.querySelectorAll('[data-r-apply]:checked')].map(el=>el.value);
        } else if(field) {
            const rule=draft.rules[selected];
            rule[field]=field==='enabled'?el.checked:field==='fields'?splitList(el.value):el.value;
            if(field==='method'){host.querySelector('.rule-field-names').hidden=el.value==='model';if(el.value==='model')delete rule.fields;}
            if(['id','name','enabled'].includes(field))host.querySelector('#scheme-rule-selector').options[selected].textContent=rule.id+' · '+rule.name+(rule.enabled===false?'（停用）':'');
        } else return;
        markDirty();
    });
    host.addEventListener('change',e=>{
        if(e.target.id==='scheme-rule-selector'){selected=Number(e.target.value);updateRulePanel();}
        if(e.target.dataset.s==='material_types') updateRulePanel();
    });
    host.addEventListener('click',async e=>{
        const button=e.target.closest('[data-cmd]');
        if (!button || busy) return;
        const cmd=button.dataset.cmd;
        if(cmd==='add-rule'){
            let next=draft.rules.length+1;
            while(draft.rules.some(r=>r.id==='R-'+next))next++;
            draft.rules.push({id:'R-'+next,name:'新审核项',group:'GENERAL',group_name:'通用审核',risk:'P1',principle:'',method:'model',enabled:true});
            selected=draft.rules.length-1;markDirty();paint();return;
        }
        if(cmd==='remove-rule'){
            draft.rules.splice(selected,1);selected=Math.min(selected,Math.max(0,draft.rules.length-1));markDirty();paint();return;
        }
        if(cmd==='copy'){copyScheme(draft);return;}
        if(cmd==='reload'){
            const replace=async()=>{
                const latest=await api(route);
                if(!host.isConnected)return;
                detail=latest;draftCache.delete(detail.id);dirty=false;revision=latest.revision;
                draft=structuredClone(detail.draft);selected=0;paint();toast('已读取最新草稿');
            };
            if(dirty){
                modal('读取服务器草稿',`<p>此操作会以服务器草稿替换本页尚未保存的修改。</p><details><summary>查看并复制本地草稿</summary><pre class="ocr">${esc(JSON.stringify(draft,null,2))}</pre></details><p><button class="btn primary" id="replace-scheme-draft">放弃本页修改并读取</button></p>`);
                document.getElementById('replace-scheme-draft').onclick=async()=>{
                    try{await replace();$('#modal').close();}catch(error){toast(error.message);}
                };
            }else{
                busy=true;host.inert=true;try{await replace();}catch(error){toast(error.message);}
                finally{busy=false;host.inert=false;if(host.isConnected)syncButtons();}
            }
            return;
        }
        if(cmd==='add-sample'){
            addSampleDialog(draft,sample=>{
                let list;
                try{list=JSON.parse(trialText);}catch{throw Error('先修正完整样本集中的 JSON 格式。');}
                if(!Array.isArray(list))throw Error('完整样本集须为数组。');
                list.push(sample);trialText=JSON.stringify(list,null,2);samplesChanged=true;
                host.querySelector('[data-trial]').value=trialText;syncButtons();
            });
            return;
        }
        busy=true;
        const controls=[...host.querySelectorAll('button,input,textarea,select')],previous=controls.map(el=>el.disabled);
        controls.forEach(el=>el.disabled=true);
        try {
            let body={revision};
            if(cmd==='save')body.draft=draft;
            if(cmd==='trial'){
                try{body.samples=JSON.parse(trialText);}catch{throw Error('试跑样本 JSON 格式有误，请检查引号和括号。');}
                if(!Array.isArray(body.samples) || !body.samples.length)throw Error('请至少填写一份含材料和预期结论的试跑样本。');
            }
            if(cmd==='publish'){if(!note.trim())throw Error('请填写发布说明。');body.note=note;}
            detail=await api(route+'/'+cmd,'POST',body);
            if(cmd==='trial')samplesChanged=false;
            draftCache.delete(detail.id);
            revision=detail.revision;dirty=false;draft=structuredClone(detail.draft || detail.published);
            if(cmd==='publish'){
                await loadSchemes();state.managedSchemes=await api('/schemes/manage');note='';
                if(host.isConnected)document.getElementById('center-scheme').innerHTML=schemeOptions(detail.id,true);
            }
            if(host.isConnected){paint();toast({save:'草稿已保存',trial:'样本试跑已完成',publish:'新版本已发布'}[cmd]);}
        }catch(error){if(host.isConnected){host.querySelector('.scheme-message').textContent=error.message;toast(error.message);}}
        finally{busy=false;controls.forEach((el,i)=>{if(el.isConnected)el.disabled=previous[i];});if(host.isConnected)syncButtons();}
    });
}

function addSampleDialog(draft,onAdd){
    const rules=enabledRules(draft);
    modal('添加标注样本',`<form id="add-scheme-sample" class="scheme-editor"><label>样本名称<input name="name" required maxlength="100"></label><label>材料类型<select name="category">${draft.material_types.map(type=>`<option>${esc(type)}</option>`).join('')}</select></label><label>材料原文<textarea name="text" rows="4" required maxlength="10000" placeholder="粘贴用于核验的业务材料文字"></textarea></label><label>验证审核项<select name="rule" required>${rules.map(rule=>`<option value="${esc(rule.id)}">${esc(rule.id)} · ${esc(rule.name)}</option>`).join('')}</select></label><label>标注的预期结论<select name="expected">${['通过','不通过','待人工判断','不适用'].map(v=>`<option>${v}</option>`).join('')}</select></label><button type="submit" class="btn primary">加入试跑样本集</button></form>`);
    const form=document.getElementById('add-scheme-sample');
    form.addEventListener('submit',e=>{
        e.preventDefault();
        const data=Object.fromEntries(new FormData(form));
        try{
            onAdd({name:data.name,pages:[{category:data.category,text:data.text}],expected:{[data.rule]:data.expected}});
            $('#modal').close();toast('样本已加入，请运行试跑');
        }catch(error){toast(error.message);}
    });
}

function copyScheme(source) {
    modal('复制为新方案',`<form id="copy-scheme-form" class="scheme-editor"><label>新方案编号<input name="id" required pattern="[A-Za-z0-9][A-Za-z0-9_\\-]{0,63}" placeholder="例如 service-contract" title="最多 64 位字母、数字、下划线或短横线，以字母或数字开头"></label><label>新方案名称<input name="name" maxlength="80" required value="${esc(source.name+'副本')}"></label><p class="tiny muted">复制材料类型、审核项和报告模板。新方案的知识与试跑样本可独立配置。</p><button type="submit" class="btn primary">创建草稿</button></form>`);
    const form=document.getElementById('copy-scheme-form');
    form.addEventListener('submit',async e=>{
        e.preventDefault();const data=new FormData(form),id=data.get('id').trim(),name=data.get('name').trim();
        const submit=form.querySelector('[type="submit"]');submit.disabled=true;
        try{
            if((await api('/schemes/manage')).some(item=>item.id===id))throw Error('此方案编号已使用');
            const draft={...structuredClone(source),id,name,version:0,knowledge_ids:[]};
            await api('/schemes/'+encodeURIComponent(id)+'/save','POST',{revision:0,draft});
            state.managedSchemes=await api('/schemes/manage');state.selectedScheme=id;state.rulesTab='方案管理';
            $('#modal').close();renderRules();toast('新方案草稿已创建');
        }catch(error){toast(error.message);}finally{submit.disabled=false;}
    });
}
