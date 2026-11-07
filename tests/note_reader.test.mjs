import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const code=await readFile(new URL('../web/notes.js',import.meta.url),'utf8');
function reader(api){
  const nodes={},views=[];
  const context={api,URLSearchParams,markdownRequest:0,esc:s=>String(s??'').replaceAll('<','&lt;'),markdown:t=>'rendered:'+t,
    $:s=>nodes[s]??=(s==='#modal'?{open:true,classList:{add(){}}}:{}),$$:()=>[],
    modal(title,body){context.markdownRequest++;context.$('#modal').open=true;views.push({title,body})}};
  vm.createContext(context);vm.runInContext(code,context);
  return {context,nodes,views};
}
const note=id=>({id,title:id,body:'完整正文',markdown:'---\n元信息\n---\n完整正文',
  status:{nature:'inference',successors:[],review_hints:[],issues:[]},
  relations:[{type:'derived_from',target:'source-id',reason:'<script>unsafe</script>'}],sources:[]});
test('note reader displays full Markdown, inference and relation IDs safely',async()=>{
  const h=reader(async path=>{assert.equal(path,'/notes/id');return note('id')});
  await h.context.openNote('id');
  assert.match(h.views.at(-1).body,/推测/);
  assert.match(h.views.at(-1).body,/data-related-note="source-id"/);
  assert.match(h.views.at(-1).body,/&lt;script>/);
  assert.match(h.views.at(-1).body,/元信息/);
});
test('late note response cannot replace another reader or reopen a closed dialog',async()=>{
  for(const action of ['close','another']){
    let resolve;const h=reader(()=>new Promise(done=>resolve=done));
    const pending=h.context.openNote('id');
    if(action==='close')h.context.$('#modal').open=false;
    else h.context.modal('another','other document');
    resolve(note('id'));await pending;
    assert(!h.views.some(v=>v.title==='id'));
  }
});
test('failed note read displays an error without keeping the previous body',async()=>{
  const h=reader(async()=>{throw Error('ID 不存在或歧义')});
  await h.context.openNote('missing');
  assert.equal(h.views.at(-1).title,'无法读取记忆');
  assert.match(h.views.at(-1).body,/ID 不存在或歧义/);
});
