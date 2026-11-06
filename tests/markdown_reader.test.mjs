import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../web/app.js',import.meta.url),'utf8');
const code=source.slice(source.indexOf('async function openMarkdown('),source.indexOf('async function openSource('));
function reader(fetch){
  const views=[],rendered=[];
  const dialog={open:true,scrollTop:99,classList:{add(){}}};
  const context={fetch,URLSearchParams,markdownRequest:0,$:()=>dialog,esc:String,
    markdown(text){rendered.push(text);return '<h1>rendered</h1>'},
    modal(title,body){context.markdownRequest++;views.push({title,body})}};
  vm.createContext(context);vm.runInContext(code,context);
  return {context,views,rendered,dialog};
}
test('reads and renders the entire Markdown with the selected version',async()=>{
  const full='\uFEFF# Title\n\n'+('Paragraph\n\n'.repeat(200))+'## Final section';
  const h=reader(async(url,options)=>{
    assert.equal(url,'/api/documents/doc/file?version=original');
    assert.equal(options.credentials,'same-origin');
    return new Response(full,{headers:{'Content-Disposition':'attachment'}});
  });
  await h.context.openMarkdown('doc','original','course/notes.md');
  assert(h.rendered[0].endsWith('## Final section'));
  assert(!h.rendered[0].startsWith('\uFEFF'));
  assert.equal(h.views.at(-1).title,'notes.md');
  assert.match(h.views.at(-1).body,/article class="markdown markdown-document"/);
  assert.match(h.views.at(-1).body,/download="notes.md"/);
});
test('stale versions and expired logins show errors without rendering response bodies',async()=>{
  for(const status of [401,409]){
    const h=reader(async()=>new Response('not the requested document',{status}));
    await h.context.openMarkdown('doc','old','notes.md');
    assert.equal(h.rendered.length,0);
    assert.match(h.views.at(-1).body,status===401?/登录已过期/:/原件已变化/);
  }
});
test('a late response cannot replace another open document',async()=>{
  let resolve;
  const h=reader(()=>new Promise(done=>{resolve=done}));
  const pending=h.context.openMarkdown('doc','original','notes.md');
  h.context.modal('another document','other content');
  resolve(new Response('# old content'));await pending;
  assert.equal(h.rendered.length,0);
  assert.equal(h.views.at(-1).title,'another document');
});
test('closing the reader during loading does not reopen it',async()=>{
  let resolve;
  const h=reader(()=>new Promise(done=>{resolve=done}));
  const pending=h.context.openMarkdown('doc','original','notes.md');
  h.dialog.open=false;resolve(new Response('# content'));await pending;
  assert.equal(h.rendered.length,0);
});
