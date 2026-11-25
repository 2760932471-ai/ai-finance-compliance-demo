/* Record a business event and the source of each field or relationship. */
const captureFieldLabels = {
  unit:'所属单位', supplier:'供应商', bank_account:'收款账户', person:'经办人',
  document:'凭证 / 合同编号', amount:'支付金额（元）', contract_amount:'合同金额（元）',
  contract_date:'签约日期', payment_date:'付款日期', accepted:'验收状态',
};
const captureRelationLabels = {
  supplier_account:'供应商使用收款账户', event_supplier:'本业务向供应商付款', event_person:'经办人办理本业务',
};

async function captureFacts(){
  if(!state.run?.evidence?.length)return toast('请先完成材料识别');
  const run=state.run;
  const snapshot=await api('/compliance/state');
  state.captureSnapshot=snapshot;
  const demo=snapshot.dataset.source==='内置虚构演示数据';
  const field=(key,label,type='text',value='')=>`<div class="field"><label for="fact-${key}">${esc(label)}</label><input id="fact-${key}" name="${key}" type="${type}" value="${esc(value)}" required maxlength="120" ${type==='number'?`min="${key==='contract_amount'?'0.01':'0'}" step="0.01"`:''}></div>`;
  const source=(key,label)=>`<div class="field"><label for="proof-${key}">${esc(label)}的依据</label><input id="proof-${key}" data-proof="${key}" list="capture-evidence-options" placeholder="选择或输入原件证据编号" autocomplete="off" required></div>`;
  const options=run.evidence.map(e=>`<option value="${esc(e.id)}">${esc(e.name)} · ${esc(evidenceLocation(e))}</option>`).join('');
  const entry=(key,label)=>{
    if(key==='accepted')return `<div class="field"><label for="fact-accepted">验收状态</label><select id="fact-accepted" name="accepted" required><option value="">请选择</option><option value="true">原件明确已验收</option><option value="false">原件明确尚未验收</option><option value="unknown">未知，待补验收材料</option></select></div>`;
    const type=['amount','contract_amount'].includes(key)?'number':key.endsWith('_date')?'date':'text';
    return field(key,label,type);
  };
  modal('确认支付业务事实',`<form id="capture-form" data-run="${esc(run.id)}">
    <datalist id="capture-evidence-options">${options}</datalist>
    <div class="two-columns">${field('id','业务单号','text',run.business_id||'BUS-'+run.id)}${field('name','业务名称','text',projectName())}
    <div class="field"><label for="fact-business_type">业务类型</label><select id="fact-business_type" name="business_type">${snapshot.active_config.ontology.classifications.map(c=>`<option>${esc(c)}</option>`).join('')}</select></div>${field('actor','事实确认人')}</div>
    <h3>业务字段与原件</h3><p class="tiny muted">每个字段选择对应材料。可用逗号分隔多个证据编号，验收情况未知时可留空其依据。</p>
    <div class="two-columns">${Object.entries(captureFieldLabels).map(([key,label])=>entry(key,label)+source(key,label)).join('')}</div>
    <h3>业务关系与原件</h3><div class="two-columns">${Object.entries(captureRelationLabels).map(([key,label])=>source(key,label)).join('')}</div>
    <div class="field"><label for="fact-note">确认说明</label><textarea id="fact-note" name="note" required minlength="5" maxlength="500" placeholder="记录核对原件的过程及待补事项"></textarea></div>
    ${demo?'<label class="fact-consent"><input type="checkbox" name="replace_demo" required> 以本项目业务替换虚构演示数据，保留已有筛查历史</label>':''}
    <div id="capture-error" class="error-box" role="alert" hidden></div></form>`,btn('保存业务事实','save-capture','primary'));
  $('#fact-accepted').addEventListener('change',e=>{
    const input=$('#proof-accepted');
    input.required=e.target.value!=='unknown';
    input.disabled=!input.required;
    if(input.disabled){input.value='';input.setCustomValidity('');}
  });
  $('#capture-form').addEventListener('input',e=>{if(e.target.hasAttribute('data-proof'))e.target.setCustomValidity('');});
}

async function saveCapture(){
  const form=$('#capture-form');
  if(!form.reportValidity())return;
  const data=new FormData(form);
  const row={};
  for(const key of ['id','name','business_type',...Object.keys(captureFieldLabels)])row[key]=data.get(key).trim();
  row.amount=Number(row.amount);
  row.contract_amount=Number(row.contract_amount);
  row.accepted=row.accepted==='unknown'?null:row.accepted==='true';
  const fieldEvidence={},relationEvidence={},known=new Set(state.run.evidence.map(e=>e.id));
  for(const input of form.querySelectorAll('[data-proof]:enabled')){
    const ids=[...new Set(input.value.trim().split(/[，,、\s]+/u))];
    if(!ids.every(id=>known.has(id))){
      input.setCustomValidity('请填写本次审核中的有效证据编号');
      input.reportValidity();
      return;
    }
    const key=input.dataset.proof;
    (key in captureRelationLabels?relationEvidence:fieldEvidence)[key]=ids;
  }
  const ids=[...new Set([...Object.values(fieldEvidence),...Object.values(relationEvidence)].flat())];
  const button=$('[data-action="save-capture"]');
  button.disabled=true;
  try{
    await api('/compliance/capture','POST',{
      row,source_run:form.dataset.run,proof_version:2,field_evidence:fieldEvidence,relation_evidence:relationEvidence,
      evidence_ids:ids,actor:data.get('actor').trim(),note:data.get('note').trim(),
      revision:state.captureSnapshot.dataset.revision,replace_demo:data.has('replace_demo'),
    });
    $('#modal').close();toast('业务事实与原件依据已保存');openCompliance('risks');
  }catch(e){const box=$('#capture-error');box.hidden=false;box.textContent=e.message;button.disabled=false;}
}
