import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const app=await readFile(new URL('../web/app.js',import.meta.url),'utf8');
function setup(open=[]){
  const context={$$:()=>open,date:()=> 'date'};
  vm.createContext(context);
  vm.runInContext(app.slice(app.indexOf('const esc='),app.indexOf('const date='))+
    app.slice(app.indexOf('function libraryTree('),app.indexOf('async function loadLibrary(')),context);
  return context;
}
const doc=(path,status='ready')=>({id:path, path,status,units:2,updated:1});

test('nested folders keep identical filenames separate and count descendants',()=>{
  const h=setup();
  const tree=h.libraryTree([doc('数学/讲义.md'),doc('数学/练习/讲义.md'),doc('物理/练习/讲义.md'),doc('介绍.md'),doc('旧资料/a.md','deleted')]);
  assert.equal(tree.count,4);
  assert.equal(tree.folders.get('数学').count,2);
  assert.equal(tree.folders.get('数学').folders.get('练习').files[0].path,'数学/练习/讲义.md');
  assert.equal(tree.folders.get('物理').folders.get('练习').count,1);
  assert.equal(tree.files[0].path,'介绍.md');
  assert(!tree.folders.has('旧资料'));
});

test('rendered folder and file labels are escaped and root files remain accessible',()=>{
  const h=setup();
  const html=h.renderLibrary([doc('<img src=x>/a&b.md'),doc('介绍.md')]);
  assert(!html.includes('<img src=x>'));
  assert(html.includes('&lt;img src=x&gt;'));
  assert(html.includes('a&amp;b.md'));
  assert(html.includes('根目录'));
  assert(html.includes('data-doc='));
});

test('refresh preserves expanded folders by full path without opening same-name siblings',()=>{
  const h=setup([{dataset:{folder:'数学/练习'},open:true},{dataset:{folder:'物理/练习'},open:false}]);
  const html=h.renderLibrary([doc('数学/练习/a.md'),doc('物理/练习/a.md')]);
  assert.match(html,/data-folder="数学\/练习" open/);
  assert.match(html,/data-folder="物理\/练习" >/);
});

test('empty library and indexing failures retain their messages',()=>{
  const h=setup();
  assert.match(h.renderLibrary([]),/上传第一份资料/);
  const html=h.renderLibrary([{...doc('课程/失败.md','error'),warning:'无法提取'}]);
  assert.match(html,/需检查/);
  assert.match(html,/无法提取/);
  assert(!html.includes('data-doc='));
});
