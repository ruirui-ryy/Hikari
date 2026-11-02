'use strict';
const mailLabels={needs_review:'新来信 · 有手工草稿待复核',new:'待分类',history:'历史背景',requested:'已请求拟稿',processed:'已处理',draft:'已拟稿 · 待确认',incomplete:'待补充',possible:'可能需要回复',no_reply:'无需回复',external_reply:'已在其他地方回复',superseded:'已被后续来信取代',review:'需要复核',unknown:'发送结果待核实',submitting:'正在提交',sent:'已发送（服务器已接受或经核实）',discarded:'这封不用回复 / 已丢弃',excluded:'排除规则生效',cleaned:'已清理',source_deleted:'源邮件已删除',processing:'正在分类拟稿',failed:'明确未发出',risk_released:'已由用户承担重复风险'};
let mailData=null,mailDetail=null,mailDirty=false,mailBusy=false;
const mailPending=new Set(['draft','incomplete','possible','review','unknown','submitting','processing','new','needs_review','requested']);
async function loadEmail(){
  const data=await api('/email');mailData=data;
  $('#email-status').textContent=(data.connected?'邮箱已配置':'邮箱未连接：请在主机停止 Hikari，运行 .venv\\Scripts\\python.exe -m hikari email-setup 后重启。')+' · '+(data.paused?'自动拟稿已暂停，同步继续':'约每两分钟同步，空闲时拟稿')+' · '+date(data.last_sync)+(data.error?' · '+data.error:'');
  $('#email-pause').textContent=data.paused?'恢复自动拟稿':'暂停自动拟稿';
  $('#email-badge').textContent=data.pending?' '+data.pending:'';
  const rows=data.messages.filter(m=>m.direction==='in'&&($('#email-filter').value==='all'||mailPending.has(m.draft_state||m.state)));
  $('#email-list').innerHTML=rows.map((m,i)=>`<article class="document"><div class="info"><button class="quiet" data-mail-index="${i}">${esc(m.subject)}</button><small>${esc(m.sender||'内容已清理')} · ${date(m.received)}</small><span class="badge">${esc(mailLabels[m.draft_state||m.state]||m.state)}</span>${m.deleted?'<span class="badge warn">源邮件已删除</span>':''}${m.error?`<p class="warning">${esc(m.error)}</p>`:''}</div></article>`).join('')||'<p class="empty">暂无此类邮件。历史与无需回复邮件可在“全部邮件”中查看。</p>';
  $$('[data-mail-index]').forEach(b=>b.onclick=()=>openMail(rows[Number(b.dataset.mailIndex)].id).catch(e=>toast(e.message)));
  if(mailDetail){
    // Polling warns about stale editors without replacing unsaved text.
    const row=data.messages.find(m=>m.id===mailDetail.message.id);
    if(row&&(row.version!==mailDetail.draft?.version||row.draft_state!==mailDetail.draft?.state)){$('#mail-version-notice').textContent='主机上的草稿或线程状态有变化，请重新打开最新版本。';$('#mail-confirm')?.setAttribute('disabled','');}
  }
}
$('#email-filter').onchange=()=>loadEmail().catch(e=>toast(e.message));
async function mailAction(path,body){const r=await api('/email/'+path,{method:'POST',body});await loadEmail();return r;}
$('#email-sync').onclick=async e=>{e.target.disabled=true;try{await mailAction('sync');toast('同步完成')}catch(e){toast(e.message)}finally{e.target.disabled=false}};
$('#email-pause').onclick=()=>mailAction('pause',{paused:!mailData?.paused}).catch(e=>toast(e.message));
$('#email-disconnect').onclick=async()=>{if(!confirm('停止邮箱收发并删除本机凭证？已有内容保留。邮箱端客户端密码需在邮箱网站自行撤销。'))return;try{const r=await mailAction('disconnect');toast(r.notice)}catch(e){toast(e.message)}};
$('#email-cleanup').onclick=async()=>{if(!confirm('删除本地全部邮件正文、附件和草稿，包括待确认草稿？最小处理与发送状态仍保留，服务器邮件和旧备份不受影响。已提交的发送无法撤回。'))return;try{const r=await mailAction('cleanup');mailDetail=null;$('#email-detail').classList.add('hidden');$('#email-inbox').classList.remove('hidden');toast(r.notice)}catch(e){toast(e.message)}};
async function mailDocument(name){
  const d=await api('/email/documents/'+name);
  modal(name==='profile'?'邮件个人资料':'邮件排除规则',`<p class="notice">${name==='profile'?'只填写你明确允许用于邮件的署名、身份、表达偏好和信息。需要课程或记忆信息时，请仅将你指定的相关部分复制到这里，并写明可参考或可披露的范围。不会自动继承学习记忆。':'每条规则使用一行：- {"address":"完整地址","scope":"sender","quote":"你的原话","active":true}。删除该行或改为 active:false 可撤销。线程内也可直接排除此发件人。'}</p><form id="mail-document-form"><textarea aria-label="Markdown 内容" name="content" rows="16">${esc(d.content)}</textarea><button class="primary">保存 Markdown 与修订</button></form>`);
  $('#mail-document-form').onsubmit=async e=>{e.preventDefault();try{await api('/email/documents/'+name,{method:'PUT',body:{content:e.target.elements.content.value,expected_hash:d.hash}});$('#modal').close();toast('邮件资料已保存')}catch(e){toast(e.message)}};
}
$('#email-profile').onclick=()=>mailDocument('profile').catch(e=>toast(e.message));
$('#email-rules').onclick=()=>mailDocument('rules').catch(e=>toast(e.message));
$('#email-revisions').onclick=async()=>{try{const rs=await api('/email/revisions');modal('邮件资料修订',rs.map(r=>`<details><summary>${esc(r.name)} · ${date(r.created)}</summary><h4>修改前</h4><pre>${esc(r.before_text)}</pre><h4>修改后</h4><pre>${esc(r.after_text)}</pre></details>`).join('')||'<p>暂无修订</p>')}catch(e){toast(e.message)}};
async function excludeSender(quote){
  const address=mailDetail.message.sender;
  if(!address)throw Error('请先重新获取邮件以核对完整发件地址');
  const d=await api('/email/documents/rules');
  await api('/email/documents/rules',{method:'PUT',body:{expected_hash:d.hash,content:d.content+'\n- '+JSON.stringify({address,scope:'sender',quote,active:true})+'\n'}});
  toast('已记录排除规则：'+address);
}
function mailPayload(){
  const f=$('#mail-editor').elements;
  return {to:f.to.value.split(/[,;\n]/).map(s=>s.trim()).filter(Boolean),cc:f.cc.value.split(/[,;\n]/).map(s=>s.trim()).filter(Boolean),subject:f.subject.value,body:f.body.value,questions:f.questions.value.split('\n').map(s=>s.trim()).filter(Boolean),attachments:[...mailDetail.draft.payload.attachments||[]]};
}
function markMailDirty(){mailDirty=true;$('#mail-confirm')?.setAttribute('disabled','');$('#mail-version-notice').textContent='有未保存修改，保存并核对最终内容后才能发送。';}
async function openMail(mid,force=false){
  if(!force&&mailDirty&&!confirm('放弃当前未保存修改并打开邮件？'))return;
  const d=await api('/email/message?'+new URLSearchParams({mid}));mailDetail=d;mailDirty=false;
  const m=d.message,p=m.payload,dr=d.draft,dp=dr?.payload||{};
  const editable=dr&&!['superseded','external_reply'].includes(m.state)&&!['submitting','unknown','sent','cleaned','external_reply','superseded','source_deleted'].includes(dr.state);
  $('#email-inbox').classList.add('hidden');$('#email-detail').classList.remove('hidden');
  $('#email-detail').innerHTML=`<div class="mail-actions"><button id="mail-back" class="secondary">← 邮件列表</button><button id="mail-reload" class="quiet">重新打开最新版本</button></div><h2>${esc(p.subject||'已清理的邮件')}</h2><p>${esc(m.sender)} · ${date(m.received)} · ${esc(mailLabels[dr?.state||m.state]||m.state)}</p>${m.deleted?'<p class="warning">源邮件已删除，停止自动处理。</p>':''}<details open><summary>来信原文</summary><pre class="mail-body">${esc(p.body||'正文已清理或不可用')}</pre>${(p.attachments||[]).map(a=>`<p>${a.file?`<a href="/api/email/attachments/${esc(a.id)}" download>${esc(a.name)}</a>`:esc(a.name)} ${esc(a.warning||'已提取文字')}</p><details><summary>提取文字</summary><pre>${esc(a.text)}</pre></details>`).join('')}</details><div class="mail-actions">${m.cleaned?'<button id="mail-restore" class="secondary">主动重新获取这封邮件</button>':''}<button id="mail-history" class="quiet">补取更早的关联邮件</button><button id="mail-request" class="secondary">请求 AI 拟稿 / 更新</button><button id="mail-discard" class="quiet">这封不用回 / 丢弃草稿</button><button id="mail-exclude" class="quiet">以后不自动回 ${esc(m.sender)}</button></div><label>对这封邮件的要求<input id="mail-instruction" maxlength="16000" placeholder="例如：改得简短些；以后不用回这个地址"></label><button id="mail-instruct" class="secondary">提交要求</button><p id="mail-version-notice" class="notice" role="status">${dr?`当前保存版本 ${dr.version}。所有修改保存后，完整核对下方内容，再确认发送。`:'AI 在空闲时处理，你也可以主动请求拟稿。'}</p>${dr?.error?`<p class="warning">${esc(dr.error)}</p>`:''}${dr&&!m.cleaned?`<form id="mail-editor" class="settings-card"><h3>最终回复 · 版本 ${dr.version}</h3><fieldset ${editable?'':'disabled'}><label>收件人（逗号分隔）<input name="to" value="${esc((dp.to||[]).join(', '))}" required></label><div class="mail-actions"><button id="mail-reply-all" type="button" class="quiet">明确选择回复全部</button></div><label>抄送（逗号分隔）<input name="cc" value="${esc((dp.cc||[]).join(', '))}"></label><label>主题<input name="subject" value="${esc(dp.subject||'')}" maxlength="998"></label><label>正文（纯文本）<textarea name="body" rows="14">${esc(dp.body||'')}</textarea></label><label>待补充问题（每行一条；补齐正文后，删除已解决的问题）<textarea name="questions" rows="3">${esc((dp.questions||[]).join('\n'))}</textarea></label><h4>发送附件</h4><p class="muted">来信附件不会自动附回。仅发送下面明确添加的附件。</p><div id="mail-outgoing">${(dp.attachments||[]).map(a=>`<p><a href="/api/email/attachments/${esc(a)}" download>${esc(dr.attachment_names[a]||a)}</a> <button data-remove-attachment="${esc(a)}" type="button" class="quiet">移除</button></p>`).join('')||'<p>无发送附件</p>'}</div><input id="mail-upload" type="file" aria-label="添加发送附件"><p id="mail-upload-status" class="muted"></p><div class="actions"><button class="primary">保存全部修改</button><button id="mail-confirm" type="button" class="primary" ${dr.state==='draft'&&!(dp.questions||[]).length&&dp.body?.trim()?'':'disabled'}>确认并发送此版本</button></div></fieldset><p class="notice">确认仅适用于上述完整保存版本；不会追加确认弹窗。服务器接受发送不代表对方收到或已读。</p></form>`:''}<div id="mail-send-records">${d.sends.map(s=>`<div class="settings-card"><p>版本 ${s.version} · ${esc(mailLabels[s.state]||s.state)} · ${date(s.created)}</p><p>${esc(s.error)}</p>${s.state==='unknown'?`<p class="warning">未自动重发。已发送文件夹暂时没有找到邮件，也不能认定发送失败。</p><button data-resolve-sent="${s.id}" class="secondary">我已检查邮箱，确认此封已发送</button><button data-resolve-risk="${s.id}" class="quiet">我承担重复发送风险，重新准备确认</button>`:''}</div>`).join('')}</div><details><summary>实际参考邮件 / 同线程往来</summary>${[...new Map([...d.history,...d.references||[]].map(h=>[h.id,h])).values()].map(h=>`<article><h4>${esc(h.payload.subject||'已清理')} · ${date(h.received)}</h4><p>${esc(h.sender)} · ${esc(h.id)}</p><pre>${esc(h.payload.body||'正文已清理')}</pre></article>`).join('')}<p>AI 实际参考标识：${esc((dp.references||[]).join('、')||'尚无模型参考记录')}</p></details><details><summary>草稿旧版本</summary>${d.versions.map(v=>`<details><summary>版本 ${v.version} · ${date(v.created)}</summary><pre>${esc(v.payload)}</pre></details>`).join('')||'<p>暂无旧版本</p>'}</details>`;
  $('#mail-back').onclick=async()=>{if(mailDirty&&!confirm('放弃未保存修改并返回？'))return;mailDirty=false;mailDetail=null;$('#email-detail').classList.add('hidden');$('#email-inbox').classList.remove('hidden');await loadEmail()};
  $('#mail-reload').onclick=()=>openMail(mid).catch(e=>toast(e.message));
  const act=async(path,body={mid})=>{try{await mailAction(path,body);await openMail(mid,true)}catch(e){toast(e.message)}};
  if($('#mail-restore'))$('#mail-restore').onclick=()=>act('restore');
  $('#mail-history').onclick=()=>act('history');
  $('#mail-request').onclick=()=>{if(mailDirty){toast('请先保存修改');return}if(dr?.manual&&!confirm('允许 AI 基于当前手工草稿更新？旧版本将保留。'))return;act('draft',{mid,instruction:$('#mail-instruction').value})};
  $('#mail-discard').onclick=()=>act('discard');
  $('#mail-exclude').onclick=()=>excludeSender('以后不用回这个地址').catch(e=>toast(e.message));
  $('#mail-instruct').onclick=()=>{const text=$('#mail-instruction').value.trim();if(/^(以后不用回|以后不回复)(这个人|这个地址|这个发件人)[。！!]?$/u.test(text)){excludeSender(text).catch(e=>toast(e.message));return}if(mailDirty){toast('请先保存修改');return}act('draft',{mid,instruction:text})};
  if($('#mail-editor')){
    $('#mail-editor').oninput=markMailDirty;
    $('#mail-editor').onsubmit=async e=>{e.preventDefault();if(mailBusy)return;mailBusy=true;try{await api('/email/draft',{method:'PUT',body:{mid,version:dr.version,thread_version:d.thread_version,payload:mailPayload()}});await openMail(mid,true);toast('完整版本已保存，请核对后确认发送')}catch(e){toast(e.message)}finally{mailBusy=false}};
    $('#mail-confirm').onclick=async()=>{if(mailDirty||mailBusy)return;mailBusy=true;$('#mail-confirm').disabled=true;try{const result=await mailAction('send',{mid,version:dr.version,thread_version:d.thread_version,request_id:dr.id+':'+dr.version});toast(mailLabels[result.state]||result.state);await openMail(mid,true)}catch(e){toast(e.message);await openMail(mid,true).catch(()=>{})}finally{mailBusy=false}};
    $('#mail-reply-all').onclick=()=>{const f=$('#mail-editor').elements;f.to.value=[...new Set([m.sender,...p.to||[]])].join(', ');f.cc.value=(p.cc||[]).join(', ');markMailDirty()};
    $$('[data-remove-attachment]').forEach(b=>b.onclick=()=>{dp.attachments=dp.attachments.filter(a=>a!==b.dataset.removeAttachment);b.closest('p').remove();markMailDirty()});
    $('#mail-upload').onchange=async e=>{const file=e.target.files[0];if(!file)return;markMailDirty();mailBusy=true;try{const form=new FormData();form.append('file',file);const a=await api('/email/attachments',{method:'POST',body:form});dp.attachments=[...dp.attachments||[],a.id];$('#mail-upload-status').textContent='已添加 '+a.name+'，请保存后核对。';}catch(e){toast(e.message)}finally{mailBusy=false;e.target.value=''}};
  }
  $$('[data-resolve-sent]').forEach(b=>b.onclick=()=>act('resolve',{send_id:b.dataset.resolveSent,action:'sent'}));
  $$('[data-resolve-risk]').forEach(b=>b.onclick=()=>act('resolve',{send_id:b.dataset.resolveRisk,action:'risk_retry'}));
}
