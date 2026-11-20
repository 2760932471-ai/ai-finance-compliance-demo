/* Confirm business facts against original evidence before cross-business screening. */
async function captureFacts(){
  if(!state.run?.evidence?.length)return toast('请先完成原材料识别，再确认业务事实');
  const snapshot=await api('/compliance/state');
  state.captureSnapshot=snapshot;
  const row=(name,label,type='text',value='')=>`<div class="field"><label for="fact-${name}">${label}</label><input id="fact-${name}" name="${name}" type="${type}" value="${esc(value)}" required maxlength="160" ${type==='number'?'min="0" step="0.01"':''}></div>`;
  const demo=snapshot.dataset.source==='内置虚构演示数据';
  modal('确认业务事实并关联原件',`<form id="capture-form"><p class="intro-line">请对照原件填写。未知金额、日期或验收情况应先补证；本操作不会将原审核判定改为通过。</p><div class="two-columns">
    ${row('id','业务单号','text','BUS-'+state.run.id)}${row('name','业务名称','text',projectName())}
    ${row('unit','所属单位')}<div class="field"><label for="fact-business_type">业务类型</label><select id="fact-business_type" name="business_type">${snapshot.active_config.ontology.classifications.map(c=>`<option>${esc(c)}</option>`).join('')}</select></div>
    ${row('supplier','供应商')}${row('bank_account','收款账户')}${row('person','经办人')}${row('document','凭证 / 合同编号')}
    ${row('amount','支付金额（元）','number')}${row('contract_amount','合同金额（元，大于0）','number')}
    ${row('contract_date','签约日期','date')}${row('payment_date','付款日期','date')}
    <div class="field"><label for="fact-accepted">验收事实</label><select id="fact-accepted" name="accepted" required><option value="">请对照原件选择</option><option value="true">已确认验收</option><option value="false">已确认尚未验收 / 缺少验收凭证</option></select></div>
    ${row('actor','事实确认人')}</div><fieldset class="fact-evidence"><legend>关联原件证据（至少一项）</legend>${state.run.evidence.map(e=>`<label><input type="checkbox" name="evidence" value="${esc(e.id)}" ${e.id===state.selectedEvidence?'checked':''}> ${esc(e.id)} · 第 ${e.page} 页 · ${esc(e.name||e.method||'原始凭证')}</label>`).join('')}</fieldset>
    <div class="field"><label for="fact-note">事实确认依据</label><textarea id="fact-note" name="note" required minlength="5" maxlength="500" placeholder="说明金额、账户、日期和验收状态分别依据哪些原件确认"></textarea></div>
    ${demo?'<label class="fact-consent"><input type="checkbox" name="replace_demo" required> 以本项目业务替换虚构演示数据，保留已有筛查历史</label>':'<p class="tiny muted">追加至当前业务数据集，不覆盖已有业务单号。</p>'}
    <div id="capture-error" class="error-box" role="alert" hidden></div></form>`,btn('保存业务事实','save-capture','primary'));
}

async function saveCapture(){
  const form=$('#capture-form');
  if(!form.reportValidity())return;
  const f=new FormData(form),ids=f.getAll('evidence');
  const box=$('#capture-error');
  if(!ids.length){box.hidden=false;box.textContent='请至少勾选一条原件证据';return;}
  const row={};
  for(const key of ['id','name','unit','business_type','supplier','bank_account','person','document','contract_date','payment_date'])row[key]=f.get(key).trim();
  row.amount=Number(f.get('amount'));row.contract_amount=Number(f.get('contract_amount'));row.accepted=f.get('accepted')==='true';
  const button=$('[data-action="save-capture"]');button.disabled=true;
  try{
    await api('/compliance/capture','POST',{row,source_run:state.run.id,evidence_ids:ids,actor:f.get('actor').trim(),note:f.get('note').trim(),revision:state.captureSnapshot.dataset.revision,replace_demo:f.has('replace_demo')});
    $('#modal').close();toast('业务事实及原件引用已保存，请执行风险筛查');openCompliance('risks');
  }catch(e){box.hidden=false;box.textContent=e.message;button.disabled=false;}
}
