'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const state={view:'chat',chat:null,chats:[],snapshot:'',online:true,settings:null};
const statuses={mail_drafting:'邮件拟稿中',idle:'闲置 · 随时可以开始',queued:'排队中',thinking:'生成中',searching:'检索中',reading:'读取资料中',remembering:'更新记忆中',completed:'已完成',failed:'失败',interrupted:'已中断',cancelled:'已取消'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=t=>t?new Date(t*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未完成检查';
function toast(text){$('#toast').textContent=text;$('#toast').classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.add('hidden'),4500)}
async function api(path,options={}){
  const headers={'X-Hikari':'1',...(options.headers||{})};
  if(options.body&&!(options.body instanceof FormData)){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body)}
  let r;try{r=await fetch('/api'+path,{...options,headers})}catch(e){setOnline(false);throw Error('无法连接主机，请确认电脑开机且服务正在运行')}
  setOnline(true);
  if(r.status===401&&path!=='/login'){showLogin();throw Error('登录已过期，请重新登录')}
  const data=await r.json().catch(()=>({detail:'服务器返回异常'}));
  if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'输入内容无效，请检查后重试');
  return data;
}
function setOnline(online){state.online=online;$('#offline').classList.toggle('hidden',online);if(!online){$('#agent-status').className='status off';$('#agent-status span').textContent='主机离线'}}
function showLogin(){$('#workspace').classList.add('hidden');$('#login').classList.remove('hidden')}
function markdown(text){
  // Preserve TeX delimiters while Markdown handles ordinary text.
  const math=[];text=text.replace(/\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|(?<!\$)\$[^\n$]+\$(?!\$)/g,m=>{math.push(m);return `HIKARIMATHPLACEHOLDER${math.length-1}END`});
  let rendered=marked.parse(text,{breaks:true});
  rendered=rendered.replace(/HIKARIMATHPLACEHOLDER(\d+)END/g,(_,i)=>esc(math[Number(i)]));
  return DOMPurify.sanitize(rendered,{FORBID_TAGS:['img','iframe','style','form'],FORBID_ATTR:['style'],ADD_ATTR:[]});
}
function math(el){if(window.renderMathInElement)renderMathInElement(el,{delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'\\(',right:'\\)',display:false},{left:'$',right:'$',display:false}],throwOnError:false,trust:false})}
let markdownRequest=0;
function modal(title,content){markdownRequest++;$('#modal').classList.remove('markdown-reader');$('#modal-title').textContent=title;$('#modal-content').innerHTML=content;if(!$('#modal').open)$('#modal').showModal();math($('#modal-content'))}
$('#modal-close').onclick=()=>$('#modal').close();
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal'))$('#modal').close()});
document.addEventListener('click',e=>{const a=e.target.closest('a');if(!a)return;if(a.dataset.mdDoc){e.preventDefault();openMarkdown(a.dataset.mdDoc,a.dataset.mdVersion,a.dataset.mdPath).catch(e=>toast(e.message));return;}const url=new URL(a.href,location.href);if(url.origin===location.origin&&url.pathname.startsWith('/api/sources/')){e.preventDefault();openSource(url.pathname.split('/').pop()).catch(e=>toast(e.message))}else if(url.origin!==location.origin){a.target='_blank';a.rel='noopener noreferrer'}});
$('#login-form').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{await api('/login',{method:'POST',body:Object.fromEntries(f)});e.target.reset();await enter()}catch(e){$('#login-error').textContent=e.message}};
$('#logout').onclick=async()=>{try{await api('/logout',{method:'POST'});state.chat=null;state.snapshot='';$('#messages').replaceChildren();showLogin()}catch(e){toast(e.message)}};
async function enter(){
  $('#login').classList.add('hidden');$('#workspace').classList.remove('hidden');
  await loadChats();const previous=localStorage.getItem('hikari-chat');state.chat=state.chats.some(c=>c.id===previous)?previous:state.chats[0]?.id||null;
  await switchView('chat');await poll();
}
async function loadChats(){state.chats=await api('/chats');$('#chat-list').innerHTML=state.chats.map(c=>`<button data-chat="${esc(c.id)}" class="${c.id===state.chat?'active':''}">${esc(c.title)}</button>`).join('');$$('[data-chat]').forEach(b=>b.onclick=()=>selectChat(b.dataset.chat))}
async function selectChat(id){state.chat=id;state.snapshot='';localStorage.setItem('hikari-chat',id);await switchView('chat');await loadChat();await loadChats()}
async function newChat(){const c=await api('/chats',{method:'POST',body:{title:'新对话'}});await loadChats();await selectChat(c.id)}
$('#new-chat').onclick=()=>newChat().catch(e=>toast(e.message));
$$('#nav button').forEach(b=>b.onclick=()=>switchView(b.dataset.view).catch(e=>toast(e.message)));
async function switchView(view){state.view=view;$$('.view').forEach(el=>el.classList.toggle('hidden',el.id!=='view-'+view));$$('#nav button').forEach(b=>b.classList.toggle('selected',b.dataset.view===view));$('#page-title').textContent={chat:'对话',library:'资料库',memory:'长期记忆',settings:'设置',email:'邮件'}[view];if(view==='chat')await loadChat();if(view==='library')await loadLibrary();if(view==='memory')await loadMemories();if(view==='settings')await loadSettings();if(view==='email')await loadEmail()}
function welcome(){return `<div class="welcome"><div class="symbol">✳</div><p class="eyebrow">A LITTLE LIGHT, A NEW UNDERSTANDING</p><h1>今天，想弄懂些什么？</h1><p>从一个问题开始，连接你的资料与新的理解。</p><div class="suggestions"><button data-suggest="结合我的资料，解释一个我正在学习的概念">◌ 理清一个概念<small>从原文出发，找到直观的解释</small></button><button data-suggest="请先查看资料目录，帮我选择一个主题开始学习">▤ 开始一段学习<small>循序理解，再用练习检验</small></button><button data-suggest="请比较资料中对同一主题的不同讲解">⇄ 连接不同资料<small>寻找共同点，也看清差异</small></button><button id="welcome-chats">◇ 继续之前的对话<small>回到尚未完成的思考</small></button></div></div>`}
async function loadChat(){
  if(!state.chat){$('#messages').innerHTML=welcome();bindWelcome();$('#task-list').innerHTML='';$('#chat-title').textContent='新的开始 · 会话';$('#scope-label').textContent='◌ 全部资料';return}
  const cid=state.chat,c=await api('/chats/'+cid);if(cid!==state.chat)return;
  $('#chat-title').textContent=c.title+' ▾';$('#scope-label').textContent='◌ '+(c.scope||'全部资料');
  const signature=JSON.stringify(c);if(signature===state.snapshot)return;state.snapshot=signature;
  const atBottom=window.innerHeight+window.scrollY>=document.body.scrollHeight-160;
  $('#messages').innerHTML=c.messages.length?c.messages.map(m=>`<article class="message ${esc(m.role)}"><div class="role">${m.role==='user'?'我':'✳'}</div><div class="body"><div class="role-label">${m.role==='user'?'你':'Hikari'}</div><div class="${m.role==='user'?'body-text':'markdown'}">${m.role==='user'?esc(m.content):markdown(m.content)}</div></div></article>`).join(''):welcome();
  math($('#messages'));bindWelcome();
  const visibleTasks=c.tasks.filter(t=>!t.dismissed&&!['completed','cancelled'].includes(t.status));
  $('#task-list').innerHTML=visibleTasks.map(t=>{
    const failed=['failed','interrupted'].includes(t.status);
    return `<div class="task ${failed?'error':''}"><span>${esc(statuses[t.status]||t.status)}${t.error?' · '+esc(t.error):' · '+esc(t.prompt.slice(0,40))}</span><div class="task-actions">${t.status==='queued'?`<button class="quiet" data-cancel="${t.id}">取消</button>`:''}${failed?`<button class="quiet" data-retry="${t.id}">重试</button><button class="task-dismiss" data-dismiss="${t.id}" aria-label="关闭任务提示" title="关闭提示，保留聊天内容">×</button>`:''}</div></div>`;
  }).join('');
  $$('[data-cancel]').forEach(b=>b.onclick=async()=>{try{await api('/tasks/'+b.dataset.cancel+'/cancel',{method:'POST'});await loadChat()}catch(e){toast(e.message)}});
  $$('[data-retry]').forEach(b=>b.onclick=async()=>{try{await api('/tasks/'+b.dataset.retry+'/retry',{method:'POST'});await loadChat()}catch(e){toast(e.message)}});
  $$('[data-dismiss]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;
    try{await api('/tasks/'+b.dataset.dismiss+'/dismiss',{method:'POST'});await loadChat()}
    catch(e){b.disabled=false;toast(e.message)}
  });
  if(atBottom&&c.messages.length)window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'});
}
function bindWelcome(){$$('[data-suggest]').forEach(b=>b.onclick=()=>{$('#prompt').value=b.dataset.suggest;$('#prompt').focus()});if($('#welcome-chats'))$('#welcome-chats').onclick=chatPicker}
function chatPicker(){modal('你的对话',`<button id="modal-new-chat" class="primary">＋ 新对话</button><div class="document-list">${state.chats.map(c=>`<div class="document"><button data-pick="${c.id}">${esc(c.title)}</button></div>`).join('')||'<p class="empty">还没有对话</p>'}</div>`);$('#modal-new-chat').onclick=async()=>{$('#modal').close();await newChat()};$$('[data-pick]').forEach(b=>b.onclick=async()=>{$('#modal').close();await selectChat(b.dataset.pick)})}
$('#chat-title').style.cursor='pointer';$('#chat-title').onclick=chatPicker;
$('#composer').onsubmit=async e=>{e.preventDefault();const text=$('#prompt').value.trim();if(!text)return;$('#send').disabled=true;try{if(!state.chat)await newChat();const pending=JSON.parse(sessionStorage.getItem('hikari-pending')||'null');const requestId=pending&&pending.chat===state.chat&&pending.content===text?pending.id:crypto.randomUUID();sessionStorage.setItem('hikari-pending',JSON.stringify({chat:state.chat,content:text,id:requestId}));await api('/chats/'+state.chat+'/messages',{method:'POST',body:{content:text,request_id:requestId}});sessionStorage.removeItem('hikari-pending');$('#prompt').value='';await loadChats();await loadChat();window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'})}catch(e){toast(e.message)}finally{$('#send').disabled=false}};
$('#prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&window.innerWidth>600){e.preventDefault();$('#composer').requestSubmit()}});
$('#chat-options').onclick=async()=>{if(!state.chat)await newChat();const c=state.chats.find(c=>c.id===state.chat)||{title:'新对话',scope:''};const docs=await api('/documents');const scopes=[...new Set(docs.filter(d=>d.status!=='deleted').flatMap(d=>[d.path,...d.path.split('/').slice(0,-1).map((_,i)=>d.path.split('/').slice(0,i+1).join('/'))]))];modal('对话设置',`<form id="chat-edit"><label>名称<input name="title" value="${esc(c.title)}" maxlength="100" required></label><label>资料范围<input name="scope" list="scope-options" value="${esc(c.scope)}" placeholder="留空搜索全库；填写完整课程目录或文件路径"></label><datalist id="scope-options">${scopes.map(s=>`<option value="${esc(s)}">`).join('')}</datalist><p class="notice">范围用于此后发送的消息；已经排队的任务保留原范围。</p><button class="primary">保存</button></form>`);$('#chat-edit').onsubmit=async e=>{e.preventDefault();try{await api('/chats/'+state.chat,{method:'PUT',body:Object.fromEntries(new FormData(e.target))});$('#modal').close();await loadChats();state.snapshot='';await loadChat()}catch(e){toast(e.message)}}};
$('#delete-chat').onclick=async()=>{if(!state.chat||!confirm('删除此聊天？已形成的长期记忆仍然保留。'))return;try{await api('/chats/'+state.chat,{method:'DELETE'});state.chat=null;state.snapshot='';localStorage.removeItem('hikari-chat');await loadChats();await loadChat()}catch(e){toast(e.message)}};
async function poll(){if($('#workspace').classList.contains('hidden'))return;try{const s=await api('/status');const busy=s.agent.status!=='idle';$('#email-badge').textContent=s.email?.pending?' '+s.email.pending:'';if(state.view==='email')await loadEmail();$('#agent-status').className='status'+(busy?' busy':'');$('#agent-status span').textContent=(statuses[s.agent.status]||s.agent.status)+(s.queued?` · ${s.queued} 条排队`:'');$('#index-status').textContent=s.index_error|| (s.indexing?'正在检查与更新索引':'最近检查：'+date(s.last_scan));if(state.view==='chat')await loadChat();if(state.view==='memory'&&!$('#modal').open)await loadMemories();if(state.view==='library'&&s.indexing!==poll.wasIndexing)await loadLibrary();poll.wasIndexing=s.indexing}catch(e){if(state.online)toast(e.message)}}
async function loadLibrary(){const docs=await api('/documents');const active=docs.filter(d=>d.status!=='deleted');$('#library-count').textContent=`${active.length} 份资料 · ${active.reduce((n,d)=>n+d.units,0)} 个原文位置`;$('#documents').innerHTML=active.map(d=>`<div class="document"><span class="file-icon">${esc(d.path.split('.').pop().toUpperCase())}</span><div class="info"><strong>${esc(d.path)}</strong><small>${d.units} 个位置 · ${date(d.updated)}</small>${d.warning?`<div class="warning">${esc(d.warning)}</div>`:''}</div><span class="badge ${d.status==='error'?'warn':''}">${({ready:'已索引',indexing:'处理中',error:'需检查'})[d.status]||esc(d.status)}</span>${d.status==='ready'?`<button data-doc="${d.id}" class="quiet">查看 ↗</button>`:''}</div>`).join('')||'<div class="empty">上传第一份资料，或将文件放入主机资料目录。</div>';$$('[data-doc]').forEach(b=>b.onclick=()=>openDoc(b.dataset.doc).catch(e=>toast(e.message)));const generated=await api('/generated');$('#generated').innerHTML=generated.map(f=>`<div class="document"><a href="/api/generated/${encodeURIComponent(f)}" download>${esc(f)}</a></div>`).join('')||'<p class="muted">需要时，可在对话中要求保存总结或练习。</p>'}
$('#refresh-library').onclick=async()=>{try{await api('/documents/refresh',{method:'POST'});toast('已安排资料检查');await loadLibrary()}catch(e){toast(e.message)}};
$('#upload-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{for(const file of $('#upload-file').files){const f=new FormData();f.append('file',file);f.append('folder',$('#upload-folder').value);await api('/documents/upload',{method:'POST',body:f})}toast('上传完成，主机将自动建立索引');$('#upload-file').value='';await loadLibrary()}catch(e){toast(e.message)}finally{button.disabled=false}};
function pdfURL(doc,version,page){return '/static/pdf.html?'+new URLSearchParams({doc,version,page:String(page)})}
async function openMarkdown(id,version,path){
  const name=path.split('/').pop();
  modal(name,'<p class="muted">正在读取全文…</p>');
  const request=markdownRequest;
  const url='/api/documents/'+encodeURIComponent(id)+'/file?'+new URLSearchParams({version});
  try{
    const response=await fetch(url,{credentials:'same-origin'});
    if(!response.ok){if(response.status===401)throw Error('登录已过期，请重新登录。');if(response.status===409)throw Error('原件已变化，请关闭后重新打开资料。');throw Error('读取失败，请检查主机连接后重试。')}
    const text=(await response.text()).replace(/^\uFEFF/,'');
    if(request!==markdownRequest||!$('#modal').open)return;
    modal(name,`<p class="muted document-path">${esc(path)}</p><a class="quiet" href="${esc(url)}" download="${esc(name)}">下载 Markdown 原件</a><article class="markdown markdown-document">${markdown(text)}</article>`);
    $('#modal').classList.add('markdown-reader');$('#modal').scrollTop=0;
  }catch(error){if(request===markdownRequest&&$('#modal').open)modal(name,`<p role="alert">${esc(error.message)}</p>`)}
}
async function openSource(id){const s=await api('/sources/'+id);if(s.current&&s.path.toLowerCase().endsWith('.pdf')){location.assign(pdfURL(s.doc,s.version,s.position));return;}let body=`<p class="muted">${esc(s.label)} · 来源版本 ${esc(s.version.slice(0,12))}</p>${s.current?'':'<p class="notice">原件已变化或不可用。下方保留回答当时读取的文字，不代表当前版本。</p>'}<pre>${esc(s.text)}</pre>`;if(s.current&&s.path.toLowerCase().endsWith('.md'))body+=`<a class="secondary" href="/api/documents/${s.doc}/file?version=${s.version}" data-md-doc="${esc(s.doc)}" data-md-version="${esc(s.version)}" data-md-path="${esc(s.path)}">阅读 Markdown 全文</a>`;else if(s.current)body+=`<a class="secondary" href="/api/documents/${s.doc}/file?version=${s.version}${s.path.toLowerCase().endsWith('.pdf')?'#page='+s.position:''}" target="_blank" rel="noopener">打开${s.path.toLowerCase().endsWith('.pdf')?'原 PDF 页面':'原件'}</a>`;modal(s.path,body)}
async function openDoc(id,position=1){const d=await api(`/documents/${id}/content?position=${position}`);if(d.path.toLowerCase().endsWith('.md')){await openMarkdown(d.id,d.version,d.path);return;}const pdf=d.path.toLowerCase().endsWith('.pdf');if(pdf){location.assign(pdfURL(d.id,d.version,position));return;}modal(d.path,`<div class="source-nav"><span>页 / 幻灯片 / 段落</span><input id="doc-position" type="number" value="${position}" min="1" max="${d.units}"><button id="doc-go" class="secondary">定位</button><a href="/api/documents/${id}/file?version=${d.version}" target="_blank" rel="noopener">打开原件 ↗</a></div>${pdf?`<iframe title="PDF 原文" src="/api/documents/${id}/file?version=${d.version}#page=${position}"></iframe>`:''}<pre>${esc(d.chunks.map(c=>c.label+'\n'+c.text).join('\n\n')||'此位置没有可提取的文字。')}</pre>`);$('#doc-go').onclick=()=>{const p=Number($('#doc-position').value);if(p>=1&&p<=d.units)openDoc(id,p).catch(e=>toast(e.message))}}
const categories={profile:'偏好与背景',learning:'学习记录',understanding:'资料理解'};
async function loadMemories(){const all=await api('/memories');$('#memories').innerHTML=all.map(m=>`<article class="memory-card" data-memory="${m.id}"><div class="tags"><span class="badge">${categories[m.path.split('/')[0]]||'手写记忆'}</span>${m.protected?'<span class="badge">用户编辑保护</span>':''}${m.stale?'<span class="badge warn">待复核</span>':''}</div><h3>${esc(m.path.split('/').pop().replace(/\.md$/,''))}</h3><p>${esc(m.content||'内容已删除或清空；保留保护记录。')}</p><small>${date(m.updated)}</small></article>`).join('')||'<div class="empty">随着对话深入，值得保留的偏好与理解会出现在这里。</div>';$$('[data-memory]').forEach(b=>b.onclick=()=>editMemory(all.find(m=>m.id===b.dataset.memory)))}
function editMemory(m){modal(m?'编辑记忆':'新建记忆',`<form id="memory-edit"><label>文件路径<input name="path" value="${esc(m?.path||'profile/我的偏好.md')}" ${m?'readonly':''} required></label><p class="notice">允许 profile/、learning/、understanding/ 下的 Markdown。手动编辑后将保护该文件，AI 遇到新证据会提出建议。</p><textarea name="content" aria-label="记忆内容">${esc(m?.content||'')}</textarea><div class="actions"><button class="primary">保存修改</button>${m?.content?'<button id="memory-delete" type="button" class="secondary">清空并保护</button>':''}</div></form>`);$('#memory-edit').onsubmit=async e=>{e.preventDefault();try{await api('/memories',{method:'PUT',body:{...Object.fromEntries(new FormData(e.target)),expected_hash:m?.hash||null}});$('#modal').close();toast('记忆已保存，你的编辑已受保护');await loadMemories()}catch(e){toast(e.message)}};if($('#memory-delete'))$('#memory-delete').onclick=async()=>{if(!confirm('清空此记忆并阻止 AI 自动恢复？修改前内容仍在修订记录中。'))return;try{await api('/memories/'+m.id+'?expected_hash='+m.hash,{method:'DELETE'});$('#modal').close();await loadMemories()}catch(e){toast(e.message)}}}
$('#new-memory').onclick=()=>editMemory(null);
$('#revisions-button').onclick=async()=>{try{const rs=await api('/revisions');modal('记忆修订',rs.map(r=>`<details class="settings-card"><summary>${esc(r.path)} · ${r.actor==='agent'?'AI':r.actor==='undo'?'撤销':'你'} · ${date(r.created)}</summary><p class="muted">${esc(r.state)}</p><h4>修改前</h4><pre>${esc(r.before_text)}</pre><h4>修改后</h4><pre>${esc(r.after_text)}</pre>${r.state==='applied'?`<button data-undo="${r.id}" class="secondary">撤销此次修改</button>`:''}</details>`).join('')||'<p class="empty">还没有修订记录</p>');$$('[data-undo]').forEach(b=>b.onclick=async()=>{try{await api('/revisions/'+b.dataset.undo+'/undo',{method:'POST'});b.disabled=true;toast('已撤销，并保护恢复后的内容');await loadMemories()}catch(e){toast(e.message)}})}catch(e){toast(e.message)}};
async function loadSettings(){const s=await api('/settings');state.settings=s;const f=$('#settings-form');for(const [key,value]of Object.entries(s)){if(f.elements[key]){if(f.elements[key].type==='checkbox')f.elements[key].checked=value;else f.elements[key].value=value}}f.elements.api_key.value='';$('#key-status').textContent=s.has_api_key?'✓ 主机已配置密钥；留空即可保留。':'尚未配置密钥。保存后可以验证连接。'}
$('#settings-form').onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));d.max_tools=Number(d.max_tools);d.max_seconds=Number(d.max_seconds);d.secure_cookie=e.target.elements.secure_cookie.checked;if(d.secure_cookie&&location.protocol!=='https:'&&!confirm('当前使用 HTTP。启用后，此地址下重新登录可能不可用，请使用 HTTPS 网址。继续？'))return;try{await api('/settings',{method:'PUT',body:d});toast('设置已保存');await loadSettings()}catch(e){toast(e.message)}};
$('#test-model').onclick=async e=>{e.target.disabled=true;try{const r=await api('/model/test',{method:'POST'});toast(r.reply||'模型连接成功')}catch(e){toast(e.message)}finally{e.target.disabled=false}};
$('#backup').onclick=async e=>{e.target.disabled=true;try{const r=await api('/backup',{method:'POST'});const a=document.createElement('a');a.href='/api/backups/'+r.filename;a.download=r.filename;a.click();toast('备份已创建')}catch(e){toast(e.message)}finally{e.target.disabled=false}};
async function boot(){try{const a=await api('/auth');$('#demo-banner').classList.toggle('hidden',!a.demo);if(a.logged_in)await enter();else{showLogin();if(!a.configured)$('#setup-hint').textContent='请先在主机运行 scripts/start.ps1 创建个人账户，然后在这里登录。'}}catch(e){showLogin();$('#login-error').textContent=e.message}if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});setInterval(poll,2000)}
boot();


$('#settings-logout').onclick=()=>$('#logout').click();

$('#manage-backups').onclick=async()=>{try{const data=await api('/backups');modal('备份管理',`<p class="notice">${esc(data.notice)}</p>${data.files.map(f=>`<div class="document"><div class="info"><a href="/api/backups/${encodeURIComponent(f.filename)}" download>${esc(f.filename)}</a><small>${date(f.created)} · ${(f.size/1024/1024).toFixed(1)} MB</small></div><button data-delete-backup="${esc(f.filename)}" class="quiet">删除此备份</button></div>`).join('')||'<p>暂无备份</p>'}`);$$('[data-delete-backup]').forEach(b=>b.onclick=async()=>{if(!confirm('永久删除此旧备份？请确认不再需要其中的聊天、记忆和邮件内容。'))return;try{await api('/backups/'+encodeURIComponent(b.dataset.deleteBackup),{method:'DELETE'});b.closest('.document').remove();toast('此备份已删除')}catch(e){toast(e.message)}})}catch(e){toast(e.message)}};
