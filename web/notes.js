'use strict';
const relationNames={supersedes:'取代',superseded_by:'被取代',corrects:'纠正',corrected_by:'被纠正',derived_from:'依据',supports:'支持',related_to:'相关'};
const natureNames={user_statement:'用户陈述',user_feedback:'用户反馈',source_statement:'资料陈述',inference:'推测'};
function noteStatus(s){
  const lines=[natureNames[s.nature]||s.nature,`有效时间：${s.valid_from||'起点未注明'} → ${s.valid_until||'终点未注明'}`];
  for(const e of s.successors)lines.push(`${relationNames[e.type]} → ${e.target}（后继起点：${e.valid_from||'未注明'}）`);
  if(!s.successors.length)lines.push('未记录取代或纠正去向');
  lines.push(...s.review_hints,...s.issues);
  return lines.join('；');
}
let noteRequest=0;
$('#note-search').onsubmit=async e=>{
  e.preventDefault();const request=++noteRequest;
  $('#note-search-status').textContent='正在检索…';$('#note-results').innerHTML='';
  const mode=$('#note-mode').value;
  try{
    const hits=await api('/notes/search?'+new URLSearchParams({q:$('#note-query').value,mode,limit:5}));
    if(request!==noteRequest)return;
    $('#note-search-status').textContent=`${{keyword:'关键词',semantic:'本地语义',hybrid:'混合'}[mode]} · ${hits.length} 条候选。${mode==='keyword'?'':'语义相近可能与问题无关，请读正文判断。'}`;
    $('#note-results').innerHTML=hits.map(h=>`<article class="memory-card"><h3><button class="quiet" data-note="${esc(h.id)}">${esc(h.title)}</button></h3><p>${esc(noteStatus(h.status))}</p><small>${esc(h.id)}</small></article>`).join('')||'<p class="empty">没有可用命中。可改写查询，或按 UUID 查看异常记录。</p>';
    $$('#note-results [data-note]').forEach(b=>b.onclick=()=>openNote(b.dataset.note));
  }catch(error){if(request===noteRequest)$('#note-search-status').textContent=error.message;}
};
$('#note-open').onsubmit=e=>{e.preventDefault();openNote($('#note-id').value.trim())};
async function openNote(id){
  modal('读取记忆','<p role="status">正在读取…</p>');
  const request=markdownRequest;
  try{
    const row=await api('/notes/'+encodeURIComponent(id));if(request!==markdownRequest||!$('#modal').open)return;
    modal(row.title,`<p class="notice">${esc(noteStatus(row.status))}</p><p class="document-path">${esc(row.id)}</p><div class="markdown-document">${markdown(row.body)}</div><h3>关联记忆</h3><div class="note-relations">${row.relations.map(e=>`<div><button class="secondary" data-related-note="${esc(e.target)}">${esc(relationNames[e.type]||e.type)} → ${esc(e.target)}</button><p>${esc(e.reason)}</p></div>`).join('')||'<p>未记录关联</p>'}</div><details><summary>来源线索（历史保存，本次未重新核验原文）</summary><pre>${esc(JSON.stringify(row.sources,null,2))}</pre></details><details><summary>完整 Markdown（含元信息）</summary><pre>${esc(row.markdown)}</pre></details>`);
    $('#modal').classList.add('markdown-reader');
    $$('#modal-content [data-related-note]').forEach(b=>b.onclick=()=>openNote(b.dataset.relatedNote));
  }catch(error){if(request===markdownRequest&&$('#modal').open)modal('无法读取记忆',`<p role="alert">${esc(error.message)}</p>`)}
}
