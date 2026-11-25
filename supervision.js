/* Local supervisory workbench. Every drill-down is based on an event or task id. */
'use strict';
(() => {
  const route = '/supervision';
  const tabNames = {overview:'监管概览',ledger:'付款台账',tasks:'核查事项'};
  const filters = {all:'全部流水',audited:'已审',unaudited:'已关联未审',unlinked:'待关联',clues:'有线索'};
  const actionNames = {feedback:'提交核验反馈',return:'退回补充',rectify:'转交整改',dismiss:'排除线索',resubmit:'提交整改材料',verify:'复验并办结',assign:'派发核查'};
  const runStatuses = {queued:'等待处理',processing:'审核中',completed:'审核完成',needs_attention:'待人工判断',interrupted:'已中断',failed:'执行失败'};
  const taskActions = {'待反馈':['feedback'],'待复核':['dismiss','rectify','return'],'待整改':['resubmit'],'待复验':['verify','return']};
  const money = value => value == null || value === '' || !Number.isFinite(Number(value)) ? '—' :
    '¥' + Number(value).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
  const number = value => Number(value || 0).toLocaleString('zh-CN');
  const field = (label, value) => `<div><dt>${esc(label)}</dt><dd>${esc(value ?? '—')}</dd></div>`;
  const pill = (text, color = '') => `<span class="sv-pill ${color}">${esc(text)}</span>`;
  const act = (action,label,key='',cls='btn') => `<button type="button" class="${cls}" data-sv="${esc(action)}" data-key="${esc(key)}">${esc(label)}</button>`;
  const noRows = text => `<p class="sv-empty">${esc(text)}</p>`;
  const statuses = row => !row.linked ? pill('待关联','sv-warn') : row.audited ? pill('已审','sv-green') : pill('未审');
  const stamp = iso => iso ? String(iso).replace('T',' ').slice(0,16) : '—';
  function dataSource(row) {
    const kind = row.source_type === 'demo' ? '内置示例' : '导入台账';
    return row.source && row.source !== kind ? kind + ' · ' + row.source : kind;
  }
  function rowFilter(row, filter) {
    return filter === 'all' || (filter === 'clues' && row.clue) || (filter === 'unlinked' && !row.linked) ||
      (filter === 'audited' && row.audited && row.linked) || (filter === 'unaudited' && row.linked && !row.audited);
  }
  function localDay(delta) {
    const day = new Date();
    day.setDate(day.getDate() + delta);
    return [day.getFullYear(),String(day.getMonth()+1).padStart(2,'0'),String(day.getDate()).padStart(2,'0')].join('-');
  }
  function sourceSummary(rows) {
    const demo = rows.filter(r => r.source_type === 'demo').length;
    return [demo && ('内置示例 '+number(demo)+' 笔'),rows.length-demo && ('导入记录 '+number(rows.length-demo)+' 笔')].filter(Boolean).join(' · ') || '暂无台账';
  }
  function scopeText(scope) {
    return [scope.org_name || scope.org_id, scope.period].filter(Boolean).join(' · ');
  }
  function sourceLink(url, label) {
    if (typeof url !== 'string' || !(/^\/api\/supervision\/evidence\?id=[A-Za-z0-9_%.:-]+$/.test(url) ||
        /^\/#run\/[a-f0-9]{16}\/evidence\/[A-Z0-9-]+$/.test(url))) return '';
    return `<a class="sv-link" target="_blank" rel="noopener" href="${esc(url)}">${esc(label)}</a>`;
  }
  function auditEvidenceView(records) {
    return (records || []).map(r => `<div class="sv-reference"><b>${esc(r.label)} · ${esc(r.locator)}</b>
      <small>审核 ${esc(r.run_id)} · 证据 ${esc(r.id)} · ${esc(r.scope_basis)}</small>
      ${r.excerpt ? `<p>${esc(r.excerpt.slice(0,300))}</p>` : ''}${sourceLink(r.url,'打开审核证据页')}</div>`).join('');
  }
  function evidenceView(row) {
    const records = (row.evidence || []).map(e => `<article class="sv-evidence"><b>${esc(e.field || e.title)}</b>
      <div class="sv-evidence-content"><strong>${esc(e.value ?? '—')}</strong>
      <small>${[e.title,e.locator].filter(Boolean).map(esc).join(' · ')}</small>
      ${e.excerpt ? `<p>${esc(e.excerpt)}</p>` : ''}${e.source ? `<small>来源　${esc(e.source)}</small>` : ''}${sourceLink(e.url, '查看来源记录')}</div></article>`).join('');
    const relations = (row.relations || []).map(r=>`<article class="sv-evidence"><b>${esc(r.type)}</b><div class="sv-evidence-content"><strong>${esc(r.from)} → ${esc(r.to)}</strong><small>关联依据　${esc(r.basis || '待核实')}</small></div></article>`).join('');
    return `<div class="sv-section"><h3>事实与证据</h3>${records || noRows('这条记录没有可引用的字段凭证。')}</div>
      <div class="sv-section"><h3>关联依据</h3>${relations || noRows('当前没有已确认的关联关系。')}</div>`;
  }

  window.renderSupervision = async function renderSupervision(host, options={}) {
    const app=document.createElement('section');
    app.className='sv-app'; host.replaceChildren(app);
    const main=document.createElement('div'), dialog=document.createElement('dialog');
    dialog.className='sv-dialog'; dialog.setAttribute('aria-labelledby','sv-dialog-heading');
    const notice=document.createElement('div'); notice.setAttribute('role','status');notice.className='sv-sr';
    app.append(main,dialog,notice);
    let data, tab=Object.hasOwn(tabNames,options.tab)?options.tab:'overview', filter='all',
        taskStatus='全部', page=0, search='', busy=false, currentRow='',currentTask='', pendingImport=null;
    const findRow=id=>data.rows.find(row=>row.id===id);
    const findTask=id=>data.tasks.find(t=>t.id===id);
    const taskFor=id=>data.tasks.find(t=>t.event_id===id);
    const back=()=>{if(dialog.open)dialog.close();};
    const announce=message=>{notice.textContent=message;toast(message);};
    const tabs=()=>`<nav class="sv-tabs" aria-label="监管工作台分区">${Object.entries(tabNames).map(([key,name])=>
      `<button type="button" data-sv="tab" data-key="${key}" aria-current="${tab===key?'page':'false'}">${name}</button>`).join('')}</nav>`;
    function open(title,html) {
      dialog.innerHTML=`<div class="sv-dialog-head"><h2 id="sv-dialog-heading">${esc(title)}</h2>
        <button type="button" aria-label="关闭" data-sv="close" class="sv-close">×</button></div><div class="sv-dialog-body">${html}
        <p data-sv-error class="sv-form-error" role="alert"></p></div>`;
      if(!dialog.open)dialog.showModal();
      dialog.scrollTop=0; dialog.querySelector('.sv-close').focus({preventScroll:true});
    }
    function metric(label,value,key) {
      return `<button type="button" class="sv-metric" data-sv="metric" data-key="${key}"><span>${label}</span><strong>${value}</strong></button>`;
    }
    function coverItem(key,label,count,cls) {
      return `<button type="button" data-sv="metric" data-key="${key}" class="sv-cover-item"><i class="${cls}"></i>${label}<b>${number(count)}</b></button>`;
    }
    function overview() {
      const m=data.metrics, clues=data.rows.filter(r=>r.clue), openTasks=data.tasks.filter(t=>!['已排除','已办结'].includes(t.status));
      const share=n=> (m.total ? Math.max(0,Math.min(100,100*(n||0)/m.total)) : 0)+'%';
      return `<div class="sv-metrics">${metric('台账付款总额',money(m.amount),'all')}${metric('付款流水',number(m.total)+' 笔','all')}
        ${metric('超限付款线索',number(m.clues)+' 条','clues')}${metric('在办核查',number(openTasks.length)+' 项','open-tasks')}</div>
        <section class="sv-panel"><div class="sv-panel-title"><h2>审核覆盖</h2><small>统计范围内 ${number(m.total)} 笔付款</small></div>
        <div class="sv-coverage" aria-hidden="true"><i class="sv-covered" style="width:${share(m.audited)}"></i><i class="sv-linked" style="width:${share(m.linked_unaudited)}"></i><i class="sv-unlinked" style="width:${share(m.unlinked)}"></i></div>
        <div class="sv-cover-legend">${coverItem('audited','已审',m.audited,'sv-covered')}${coverItem('unaudited','已关联未审',m.linked_unaudited,'sv-linked')}${coverItem('unlinked','待关联',m.unlinked,'sv-unlinked')}</div></section>
        <section class="sv-panel"><div class="sv-panel-title"><div><h2>累计付款线索</h2><small>${esc(data.scope.rule || '按合同及付款日期核对验收前累计付款')}</small></div>${act('metric','全部流水','all','btn small')}</div>
          ${clues.length ? clues.map(r=>`<article class="sv-clue"><div><div class="sv-line">${pill(taskFor(r.id)?.status || '待派核查','sv-warn')}<b>${esc(r.contract_id || r.id)}</b></div>
            <p>${esc(r.reason || '累计付款超过当前适用基准')}</p><small>${esc(r.date)} · ${esc(r.supplier)} · ${esc(dataSource(r))}</small></div>
            <div class="sv-clue-value"><small>超出基准</small><strong>${money(r.excess)}</strong></div>
            ${act('row','查看构成与依据',r.id,'btn small')}</article>`).join('') : noRows('当前台账未触发累计付款线索。')}</section>`;
    }
    function ledgerTable() {
      const query=search.trim().toLowerCase();
      const rows=data.rows.filter(r=>rowFilter(r,filter) && (!query || [r.id,r.contract_id,r.supplier,r.source].some(v=>String(v||'').toLowerCase().includes(query))));
      const limit=20, pages=Math.max(1,Math.ceil(rows.length/limit));page=Math.min(page,pages-1);
      return `<div class="sv-scroll"><table class="sv-table"><thead><tr><th>付款流水 / 日期</th><th>供应商 / 合同</th><th>金额</th><th>审核覆盖</th><th>来源</th><th><span class="sv-sr">操作</span></th></tr></thead>
        <tbody>${rows.slice(page*limit,(page+1)*limit).map(r=>`<tr><td><b>${esc(r.id)}</b><small>${esc(r.date)}</small></td><td>${esc(r.supplier)}<small>${esc(r.contract_id||'待确认归属')}</small></td>
        <td class="sv-num">${money(r.amount)}</td><td>${statuses(r)}${r.clue?' '+pill('线索','sv-warn'):''}</td><td>${esc(dataSource(r))}</td><td>${act('row','查看',r.id,'sv-link')}</td></tr>`).join('')}</tbody></table></div>
        ${rows.length?'':noRows('没有符合当前条件的付款。')}<div class="sv-pagination"><small>共 ${number(rows.length)} 笔</small>${pages>1?`${act('page','上一页',String(Math.max(0,page-1)),'btn small')}<small>${page+1} / ${pages}</small>${act('page','下一页',String(Math.min(pages-1,page+1)),'btn small')}`:''}</div>`;
    }
    function ledger() {
      return `<section class="sv-panel"><div class="sv-panel-title"><h2>付款流水</h2>${act('import','导入付款台账','','btn primary small')}</div>
        <div class="sv-filters"><label>覆盖状态<select id="sv-filter">${Object.entries(filters).map(([k,v])=>`<option value="${k}" ${k===filter?'selected':''}>${v}</option>`).join('')}</select></label>
        <label>查找流水<input id="sv-search" type="search" value="${esc(search)}" placeholder="流水号、合同号或供应商"></label></div>
        <div id="sv-ledger-table">${ledgerTable()}</div></section>`;
    }
    function taskList() {
      const statuses=['全部','在办',...Object.keys(taskActions),'已办结','已排除'];
      const rows=data.tasks.filter(t=> taskStatus==='全部'||(taskStatus==='在办'?Object.hasOwn(taskActions,t.status):taskStatus===t.status));
      return `<section class="sv-panel"><div class="sv-panel-title"><div><h2>核查事项</h2><small>派单时保存查证范围、触发流水和金额依据。</small></div>
        <label class="sv-label">事项状态<select id="sv-task-filter">${statuses.map(s=>`<option ${s===taskStatus?'selected':''}>${esc(s)}</option>`).join('')}</select></label></div>
        ${rows.length?`<div class="sv-scroll"><table class="sv-table"><thead><tr><th>核查问题 / 编号</th><th>责任人</th><th>到期日期</th><th>处理状态</th><th>触发流水</th><th><span class="sv-sr">操作</span></th></tr></thead><tbody>
        ${rows.map(t=>`<tr><td><strong>${esc(t.question)}</strong><small>${esc(t.id)}</small></td><td>${esc(t.owner)}</td><td>${esc(t.due)}</td><td>${pill(t.status)}</td><td>${esc(t.event_id)}</td><td>${act('task','办理详情',t.id,'sv-link')}</td></tr>`).join('')}</tbody></table></div>`:
        noRows(taskStatus==='全部'?'暂无核查事项，可在累计付款线索详情中派发。':'没有符合当前状态的事项。')}</section>`;
    }
    function draw() {
      main.innerHTML=`<header class="sv-header"><div><h1>监管工作台</h1><p>${esc(scopeText(data.scope))}</p></div><small class="sv-source">${esc(sourceSummary(data.rows))}</small></header>
        ${tabs()}${tab==='overview'?`<p class="sv-scope-note">统计截至 ${esc(data.as_of)} · ${esc(data.scope.basis)} <span>本机单组织演示</span></p>`:''}${tab==='ledger'?ledger():tab==='tasks'?taskList():overview()}`;
    }
    function paymentsTable(rows,interactive) {
      if(!rows.length)return noRows('暂无构成流水。');
      return `<div class="sv-scroll"><table class="sv-table"><thead><tr><th>流水</th><th>付款日期</th><th>金额</th><th>${interactive?'审核覆盖':'台账来源'}</th></tr></thead><tbody>${rows.map(r=>
        `<tr><td>${interactive?act('row',r.id,r.id,'sv-link'):esc(r.id)}</td><td>${esc(r.date)}</td><td class="sv-num">${money(r.amount)}</td><td>${interactive?statuses(r):esc(r.source)}</td></tr>`).join('')}</tbody></table></div>`;
    }
    function showRow(id) {
      const row=findRow(id);if(!row)return;
      currentRow=id;
      const task=taskFor(id), contributors=(row.contributors || []).map(findRow).filter(Boolean);
      inspectRow(row,task,contributors);
    }
    function inspectRow(r,task,contributors) {
      open('付款依据与累计明细',`<div class="sv-detail-head"><div><h3>${esc(r.id)}</h3><p>${esc(r.supplier)} · ${esc(dataSource(r))}</p></div>${statuses(r)}</div>
        <dl class="sv-facts">${field('付款日期',r.date)}${field('本笔金额',money(r.amount))}${field('合同',r.contract_id || '待确认归属')}${field('审核记录',r.audit_ref || '无关联审核记录')}</dl>
        ${r.contract_id?`<div class="sv-calculation"><div><span>截至本笔累计</span><b>${money(r.cumulative)}</b></div><div><span>验收前限额</span><b>${money(r.cap)}</b></div><div><span>超出基准</span><b>${money(r.excess)}</b></div></div>`:''}
        <p class="sv-detail-note">${esc(r.reason || r.check_state || '')}</p>
        ${contributors.length?`<section class="sv-section"><h3>累计金额的构成流水</h3>${paymentsTable(contributors,true)}</section>`:''}${evidenceView(r)}
        <div class="sv-actions">${task?act('task','查看已派核查单',task.id,'btn primary'):(r.clue || r.check_state==='待核验')?act('assign','派发核查',r.id,'btn primary'):''}${act('close','关闭')}</div>`);
    }
    function assignForm(id) {
      const r=findRow(id);if(!r)return;
      currentRow=id;
      open('派发核查',`<p class="sv-detail-note">${esc(r.id)} · ${esc(r.contract_id || '待确认归属')} · ${r.clue?'超出基准 '+money(r.excess):esc(r.reason)}</p>
        <form data-sv-form="assign" class="sv-form">
        <label>核查问题<textarea name="question" rows="3" required maxlength="500">${r.clue?'核实付款当日的验收状态，以及是否存在已生效并覆盖此笔付款的补充协议。':'核实本笔付款的供应商主键、合同编号及付款依据。'}</textarea></label>
        <div class="sv-form-cols"><label>责任人<input name="owner" required maxlength="60" autocomplete="off"></label><label>完成期限<input type="date" name="due" value="${localDay(3)}" required></label></div>
        <label>派单人<input name="actor" required maxlength="60" autocomplete="off"></label>
        <p class="sv-help">本次派单将冻结当前指标、构成流水与证据。姓名用于本地演示留痕。</p>
        <div class="sv-actions">${act('row','返回流水',id)}<button class="btn primary" type="submit">确认派单</button></div></form>`);
    }
    function taskView(id) {
      const t=findTask(id);if(!t)return;
      currentTask=id;
      const snap=t.snapshot||{},row=snap.row||{},entries=snap.contributors||[];
      const canAudit=['待反馈','待整改'].includes(t.status);
      open('核查事项详情',`<div class="sv-detail-head"><div><h3>${esc(t.question)}</h3><p>${esc(t.id)}</p></div>${pill(t.status)}</div>
        <dl class="sv-facts">${field('责任人',t.owner)}${field('完成期限',t.due)}${field('触发流水',t.event_id)}${field('取证时间',stamp(snap.captured_at))}</dl>
        <section class="sv-task-audit"><div class="sv-panel-title"><h3>本事项定向审核</h3>${canAudit?`<a class="btn small" href="#workspace/supervision/${encodeURIComponent(t.id)}">发起合同核验</a>`:''}</div>
        <details data-sv-task-runs="${esc(t.id)}"><summary>查看已发起的审核</summary><div data-sv-run-list></div></details></section>
        <details class="sv-snapshot"><summary>查看派单时的指标与证据</summary><div class="sv-section"><p>${esc(snap.rule || data.scope.rule || '')}</p>
        <dl class="sv-facts">${field('合同',row.contract_id)}${field('累计付款',money(row.cumulative))}${field('验收前限额',money(row.cap))}${field('超出基准',money(row.excess))}</dl>
        ${paymentsTable(entries,false)}${evidenceView(row)}</div></details>
        <section class="sv-section"><h3>处理记录</h3><ol class="sv-history">${(t.history||[]).map(h=>`<li><div><b>${esc(actionNames[h.action]||h.action)}</b> ${pill(h.to_status || h.status || t.status)}</div><small>${esc(stamp(h.at))} · ${esc(h.actor)}</small>${h.note?`<p>${esc(h.note)}</p>`:''}${h.evidence?`<p><strong>依据</strong>　${esc(h.evidence)}</p>`:''}${auditEvidenceView(h.audit_evidence)}${h.observed?`<p>办理时累计 ${money(h.observed.cumulative)} · 当日限额 ${money(h.observed.cap)} · 超出 ${money(h.observed.excess)}</p>`:''}</li>`).join('')}</ol></section>
        <div class="sv-actions">${(taskActions[t.status]||[]).map((a,i)=>act('transition',actionNames[a],a,i===0?'btn primary':'btn')).join('')}${act('close','关闭')}</div>`);
    }
    function transitionForm(action) {
      const t=findTask(currentTask);if(!t || !(taskActions[t.status]||[]).includes(action))return;
      const actorLabel=action==='feedback'||action==='resubmit'?'办理人':action==='verify'?'复验人':'复核人';
      open(actionNames[action],`<p class="sv-detail-note">${esc(t.question)} · ${esc(t.status)}</p>
        <form data-sv-form="transition" class="sv-form" data-kind="${esc(action)}"><label>${actorLabel}<input name="actor" required maxlength="60" autocomplete="off" value="${esc(action==='feedback'||action==='resubmit'?t.owner:'')}"></label>
        <label>办理意见<textarea name="note" rows="3" required maxlength="1000" placeholder="写明核实的事实、理由或需补充的事项"></textarea></label>
        <label>证据依据<textarea name="evidence" rows="2" required maxlength="800" placeholder="填写材料名称、条款页码或权威记录编号"></textarea></label>
        ${['feedback','resubmit'].includes(action)?`<div id="sv-audit-picker">${act('audit-picker','引用已完成审核的证据（可选）','','btn small')}</div>`:''}
        <div class="sv-actions">${act('task','返回事项',currentTask)}<button type="submit" class="btn primary">${esc(actionNames[action])}</button></div></form>`);
    }
    async function auditPicker(button) {
      const box=button.parentElement;button.disabled=true;
      try {
        const projects=await api('/projects');
        if(!box.isConnected)return;
        const linked=[], other=[];
        for(const project of projects){
          if(project.org_id && project.org_id!==data.scope.org_id)continue;
          const options=[];
          for(const run of project.runs||[]){
            if(!['completed','needs_attention'].includes(run.status))continue;
            const option=`<option value="${esc(run.id)}">${esc(run.business_id || run.id)} · ${esc(run.scheme_name || '')} · ${esc(run.id)}</option>`;
            if(run.supervision_task?.id===currentTask)linked.push(option);
            else options.push(option);
          }
          if(options.length)other.push(`<optgroup label="${esc(project.name || project.id)}">${options.join('')}</optgroup>`);
        }
        const groups=(linked.length?`<optgroup label="本事项发起的审核">${linked.join('')}</optgroup>`:'')+other.join('');
        box.innerHTML=groups?`<label>选择已完成审核<select id="sv-audit-run" name="run_id"><option value="">选择审核（可选）</option>${groups}</select></label><div id="sv-audit-pages"></div>`:
          '<p class="sv-help">当前组织下暂无可引用的已完成审核。可以填写上方证据依据继续反馈。</p>';
      }finally{button.disabled=false;}
    }
    async function selectAudit(select) {
      const slot=select.parentElement.parentElement.querySelector('#sv-audit-pages'),id=select.value;
      slot.replaceChildren();if(!id)return;
      slot.textContent='正在读取审核证据…';
      const run=await api('/runs/'+encodeURIComponent(id));
      if(!slot.isConnected || select.value!==id)return;
      const items=run.evidence||[];
      slot.innerHTML=items.length?`<fieldset class="sv-audit-items"><legend>引用证据页（最多 12 页）</legend>${items.map(e=>
        `<label class="sv-check"><input type="checkbox" name="evidence_id" value="${esc(e.id)}"><span>${esc(e.name || e.id)} · 第 ${esc(e.page)} 页<small>${esc(e.id)}${e.text?' · '+esc(e.text.slice(0,80)):''}</small></span></label>`).join('')}</fieldset>
        <label class="sv-check"><input type="checkbox" name="scope_confirmed" required><span>我已核对所选项目属于${esc(data.scope.org_name)}，且勾选页与本次核查有关。</span></label>`:
        '<p class="sv-help">这次审核没有可引用的证据页，请更换审核或取消选择。</p>';
    }
    async function taskRuns(details) {
      const slot=details.querySelector('[data-sv-run-list]');
      slot.textContent='正在读取…';
      try{
        const projects=await api('/projects'), rows=[];
        if(!details.isConnected)return;
        for(const project of projects){
          if(project.org_id && project.org_id!==data.scope.org_id)continue;
          for(const run of project.runs||[]){
            if(run.supervision_task?.id!==details.dataset.svTaskRuns)continue;
            rows.push(`<article class="sv-directed-run"><span><b>${esc(project.name || project.id)}</b><small>${esc(run.business_id || run.id)} · ${esc(stamp(run.created_at))}</small></span>
              ${pill(runStatuses[run.status]||run.phase)}<a class="sv-link" href="#run/${encodeURIComponent(run.id)}">查看审核</a></article>`);
          }
        }
        slot.innerHTML=rows.join('')||noRows('尚未从本事项发起定向审核。');
      }catch(err){slot.textContent=err.message || '读取失败，请重新展开。';}
    }
    function importForm() {
      pendingImport=null;
      open('导入付款台账',`<p class="sv-detail-note">${esc(scopeText(data.scope))}。上传符合当前组织与期间的台账，先核对明细再写入。</p>
        <form data-sv-form="import" class="sv-form"><div>${act('template','下载 JSON 示例','','btn small')}</div>
        <label>台账文件<input type="file" id="sv-import-file" accept=".json,application/json" required></label>
        <label>台账来源<input name="source" required maxlength="100" placeholder="例如：财务系统三季度付款导出"></label>
        <div id="sv-import-preview"></div><div class="sv-actions">${act('close','取消')}<button type="submit" class="btn primary" disabled>确认导入</button></div></form>`);
    }
    async function previewImport(file) {
      const form=dialog.querySelector('form[data-sv-form="import"]');
      const preview=form.querySelector('#sv-import-preview'),button=form.querySelector('[type=submit]');
      pendingImport=null;button.disabled=true;preview.textContent='';
      if(!file)return;
      if(file.size>2*1024*1024)throw Error('请选择小于 2 MB 的台账文件');
      const parsed=JSON.parse((await file.text()).replace(/^\uFEFF/,''));
      const rows=Array.isArray(parsed)?parsed:parsed?.rows;
      if(!Array.isArray(rows)||!rows.length||rows.some(r=>!r||typeof r!=='object'||Array.isArray(r)))throw Error('文件应包含非空的 rows 流水数组，请参照 JSON 示例');
      if(!form.isConnected||form.querySelector('#sv-import-file').files[0]!==file)return;
      for(const r of rows)if(!r.id||!r.org_id||!r.date||r.amount==null||!r.supplier)throw Error('流水缺少 id、org_id、date、amount 或 supplier');
      pendingImport=rows;form.elements.source.value=parsed.source||file.name;
      preview.innerHTML=`<p>已读取 ${number(rows.length)} 笔，前 ${Math.min(rows.length,5)} 笔如下。</p><div class="sv-scroll"><table class="sv-table"><thead><tr><th>流水号</th><th>日期</th><th>供应商</th><th>金额</th></tr></thead><tbody>${rows.slice(0,5).map(r=>`<tr><td>${esc(r.id)}</td><td>${esc(r.date)}</td><td>${esc(r.supplier)}</td><td>${money(r.amount)}</td></tr>`).join('')}</tbody></table></div>`;
      button.disabled=false;
    }
    async function template() {
      const content=await api(route+'/template'),blob=new Blob([JSON.stringify(content,null,2)],{type:'application/json'});
      const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='付款台账导入示例.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    function displayError(err) {
      const message=err instanceof SyntaxError?'JSON 格式错误，请检查文件内容。':(err.message||'操作失败，请重试');
      const slot=dialog.open&&dialog.querySelector('[data-sv-error]');
      if(slot)slot.textContent=message;else announce(message);
    }
    async function load() {
      main.innerHTML='<p class="sv-empty">正在读取监管台账…</p>';
      try{data=await api(route+'/state');draw();if(options.taskId && findTask(options.taskId))taskView(options.taskId);}
      catch(err){main.innerHTML=`<p class="sv-empty" role="alert">${esc(err.message)}</p>${act('reload','重新读取')}`;}
    }
    app.addEventListener('click',async event=>{
      const button=event.target.closest('button[data-sv]');if(!button||!app.contains(button)||busy)return;
      const {sv:action,key}=button.dataset;
      try{
        if(action==='tab'){tab=key;draw();}
        else if(action==='metric'){if(key==='open-tasks'){tab='tasks';taskStatus='在办';}else{tab='ledger';filter=key;page=0;search='';}draw();}
        else if(action==='page'){page=Number(key);main.querySelector('#sv-ledger-table').innerHTML=ledgerTable();}
        else if(action==='row')showRow(key);
        else if(action==='task')taskView(key);
        else if(action==='assign')assignForm(key);
        else if(action==='transition')transitionForm(key);
        else if(action==='import')importForm();
        else if(action==='template')await template();
        else if(action==='audit-picker')await auditPicker(button);
        else if(action==='close')back();
        else if(action==='reload')await load();
      }catch(err){displayError(err);}
    });
    dialog.addEventListener('toggle',event=>{
      const details=event.target;
      if(details.open && details.matches('details[data-sv-task-runs]'))taskRuns(details);
    },true);
    app.addEventListener('change',async event=>{
      try{
        if(event.target.id==='sv-filter'){filter=event.target.value;page=0;draw();}
        else if(event.target.id==='sv-audit-run')await selectAudit(event.target);
        else if(event.target.id==='sv-task-filter'){taskStatus=event.target.value;draw();}
        else if(event.target.id==='sv-import-file'){dialog.querySelector('[data-sv-error]').textContent='';await previewImport(event.target.files[0]);}
      }catch(err){displayError(err);}
    });
    app.addEventListener('input',event=>{
      if(event.target.id==='sv-search'){search=event.target.value;page=0;main.querySelector('#sv-ledger-table').innerHTML=ledgerTable();}
    });
    dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
    app.addEventListener('submit',async event=>{
      const form=event.target;if(!form.dataset.svForm)return;event.preventDefault();
      if(busy||!form.reportValidity())return;
      const operation=form.dataset.svForm,values=Object.fromEntries(new FormData(form));
      if(operation==='import'&&!pendingImport)return;
      const button=form.querySelector('[type=submit]');busy=true;button.disabled=true;
      try{
        const payload={...values,revision:data.revision};
        if(operation==='assign')payload.event_id=currentRow;
        else if(operation==='transition'){
          Object.assign(payload,{task_id:currentTask,action:form.dataset.kind,version:findTask(currentTask).version});
          if(payload.run_id){
            payload.evidence_ids=new FormData(form).getAll('evidence_id');
            if(!payload.evidence_ids.length || payload.evidence_ids.length>12)throw Error('请选择 1 至 12 条审核证据');
            payload.scope_confirmed=form.elements.scope_confirmed?.checked===true;
          }
          delete payload.evidence_id;
        }
        else payload.rows=pendingImport;
        data=await api(route+'/'+operation,'POST',payload);
        if(operation==='import'){
          pendingImport=null;tab='ledger';filter='all';search='';page=0;back();draw();announce(`台账已读取，新增 ${number(data.result.added)} 笔，重复 ${number(data.result.duplicates)} 笔。`);
        }else{
          tab='tasks';taskStatus='全部';draw();const t=operation==='assign'?taskFor(currentRow):findTask(currentTask);
          if(t)taskView(t.id);else back();announce(operation==='assign'?'核查事项已保存':'办理记录已保存');
        }
      }catch(err){displayError(err);}finally{busy=false;button.disabled=false;}
    });
    await load();
  };
})();
