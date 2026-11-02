import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../web/email.js',import.meta.url),'utf8');
const copy=x=>JSON.parse(JSON.stringify(x));

function harness(){
  const elements=new Map(),toasts=[];
  let current={message:{id:'<sample@example.org>',sender:'sender@example.org',received:1,state:'processed',payload:{subject:'Synthetic',body:'Synthetic incoming',attachments:[]}},
    draft:{id:'draft-id',version:2,state:'incomplete',manual:0,requested:0,instruction:'',payload:{to:['sender@example.org'],cc:[],subject:'Re: Synthetic',body:'Old body',questions:['When?'],attachments:[]},attachment_names:{}},
    thread_version:'thread-snapshot',history:[],references:[],versions:[],sends:[]};
  let deferDetail=null;
  function element(selector){
    if(!elements.has(selector))elements.set(selector,{
      value:selector==='#email-filter'?'pending':'',textContent:'',disabled:false,
      classList:{add(){},remove(){}},setAttribute(name){if(name==='disabled')this.disabled=true},
      set innerHTML(value){
        this.html=value;
        if(selector==='#email-detail'){
          element('#mail-instruction').value='';
          const confirm=value.match(/<button id="mail-confirm"[^>]*>/)?.[0]||'';
          element('#mail-confirm').disabled=/\sdisabled/.test(confirm);
        }
      },get innerHTML(){return this.html||''}
    });
    return elements.get(selector);
  }
  const api=async(path,options={})=>{
    if(path==='/email')return {connected:true,paused:false,last_sync:1,error:'',pending:1,messages:[{
      id:current.message.id,direction:'in',subject:'Synthetic',sender:current.message.sender,
      received:1,state:current.message.state,version:current.draft.version,draft_state:current.draft.state,error:current.draft.error||''}]};
    if(path.startsWith('/email/message?')){
      if(deferDetail){const deferred=deferDetail;deferDetail=null;deferred.started();await deferred.wait;}
      return copy(current);
    }
    if(path==='/email/draft'&&options.method==='POST'){
      current.draft.version++;
      current.draft.state='possible';current.draft.requested=1;
      current.draft.instruction=options.body.instruction;
      return copy(current);
    }
    throw Error('Unexpected endpoint '+path);
  };
  const context=vm.createContext({$:element,$$:()=>[],api,URLSearchParams,
    state:{view:'email'},esc:s=>String(s??''),date:()=>'',toast:s=>toasts.push(s),
    confirm:()=>{throw Error('Automatic refresh must not open a confirmation')},
  });
  vm.runInContext(source,context);
  const run=code=>vm.runInContext(code,context);
  return {
    element,run,toasts,
    async open(){await run("openMail('<sample@example.org>')")},
    async submit(text='Please answer 21:00'){
      element('#mail-instruction').value=text;
      await element('#mail-instruct').onclick();
    },
    complete(body='Updated body at 21:00'){
      current.draft.version++;current.draft.state='draft';current.draft.requested=0;
      current.draft.manual=0;current.draft.payload.body=body;current.draft.payload.questions=[];
    },
    change(fn){fn(current)},
    deferNextDetail(){
      let release,started;
      const startedPromise=new Promise(resolve=>started=resolve);
      deferDetail={started,wait:new Promise(resolve=>release=resolve)};
      return {started:startedPromise,release};
    },
    body:()=>run('mailDetail.draft.payload.body'),
    html:()=>element('#email-detail').innerHTML,
    notice:()=>element('#mail-version-notice').textContent,
  };
}

test('submitted AI update replaces old body, questions and displayed version on poll',async()=>{
  const h=harness();await h.open();await h.submit();
  assert.match(h.notice(),/完成后会自动显示/);
  assert.equal(h.element('#mail-confirm').disabled,true);
  h.change(d=>{d.draft.state='processing';d.draft.requested=0});
  await h.run('loadEmail()');assert.match(h.notice(),/等待 AI/);
  h.complete();await h.run('loadEmail()');
  assert.equal(h.body(),'Updated body at 21:00');
  assert.match(h.html(),/最终回复 · 版本 4/);
  assert.match(h.html(),/Updated body at 21:00/);
  assert.doesNotMatch(h.html(),/Old body|When\?/);
  assert.equal(h.element('#mail-confirm').disabled,false);
  assert.match(h.toasts.at(-1),/草稿已按要求更新/);
});

test('typing while AI works preserves unsaved edits and invalidates old confirmation',async()=>{
  const h=harness();await h.open();await h.submit();
  h.run('markMailDirty()');const before=h.html();h.complete();
  await h.run('loadEmail()');
  assert.equal(h.html(),before);assert.equal(h.body(),'Old body');
  assert.equal(h.run('mailDirty'),true);
  assert.match(h.notice(),/未保存修改仍保留/);
  assert.equal(h.element('#mail-confirm').disabled,true);
});

test('edit arriving during detail fetch is preserved',async()=>{
  const h=harness();await h.open();await h.submit();h.complete();
  const deferred=h.deferNextDetail();const poll=h.run('loadEmail()');await deferred.started;
  h.run('markMailDirty()');deferred.release();await poll;
  assert.equal(h.body(),'Old body');assert.equal(h.run('mailDirty'),true);
  assert.equal(h.element('#mail-confirm').disabled,true);
});

test('another device manual edit is never silently substituted as this AI result',async()=>{
  const h=harness();await h.open();await h.submit();h.complete('Other device manual body');
  h.change(d=>{d.draft.manual=1});await h.run('loadEmail()');
  assert.equal(h.body(),'Old body');assert.match(h.notice(),/重新打开最新版本/);
  assert.equal(h.element('#mail-confirm').disabled,true);
});

test('a newly typed instruction is preserved while a prior request completes',async()=>{
  const h=harness();await h.open();await h.submit();
  h.element('#mail-instruction').value='Next instruction still typing';h.complete();
  await h.run('loadEmail()');
  assert.equal(h.body(),'Old body');
  assert.equal(h.element('#mail-instruction').value,'Next instruction still typing');
});

test('navigation while detail is loading prevents reopening the previous draft',async()=>{
  const h=harness();await h.open();await h.submit();h.complete();
  const deferred=h.deferNextDetail();const poll=h.run('loadEmail()');await deferred.started;
  h.run('mailOpenGeneration++;mailDetail=null;mailSubmittedUpdate=null');
  deferred.release();await poll;
  assert.equal(h.run('mailDetail'),null);
});

test('AI failure refreshes the visible error without requiring a version increment',async()=>{
  const h=harness();await h.open();await h.submit();
  h.change(d=>{d.draft.state='possible';d.draft.requested=0;d.draft.error='Synthetic model failure'});
  await h.run('loadEmail()');
  assert.match(h.html(),/Synthetic model failure/);
  assert.equal(h.run('mailSubmittedUpdate'),null);
  assert.equal(h.element('#mail-confirm').disabled,true);
});

test('thread changes require explicit review even when an AI request was pending',async()=>{
  const h=harness();await h.open();await h.submit();h.complete();
  h.change(d=>{d.thread_version='changed-thread'});await h.run('loadEmail()');
  assert.equal(h.body(),'Old body');assert.equal(h.run('mailSubmittedUpdate'),null);
  assert.match(h.notice(),/重新打开最新版本/);
});
