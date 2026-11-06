import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

const origin='https://hikari.example';
const html=await readFile(new URL('../web/index.html',import.meta.url),'utf8');
const worker=await readFile(new URL('../web/sw.js',import.meta.url),'utf8');

function harness(){
  const stores=new Map(),listeners={};
  let offline=false,failPath='',activated=false;
  const key=request=>new URL(typeof request==='string'?request:request.url,origin).href;
  const fetch=async request=>{
    if(offline)throw Error('offline');
    const url=new URL(key(request));
    if(url.pathname===failPath)return new Response('unavailable',{status:503});
    return new Response(url.pathname==='/'?html:'asset '+url.pathname+url.search);
  };
  const caches={
    async open(name){
      if(!stores.has(name))stores.set(name,new Map());
      const entries=stores.get(name);
      return {
        async addAll(requests){
          const responses=await Promise.all(requests.map(fetch));
          if(responses.some(response=>!response.ok))throw Error('failed precache');
          requests.forEach((request,i)=>entries.set(key(request),responses[i]));
        },
        async put(request,response){entries.set(key(request),response)},
        async match(request){return entries.get(key(request))?.clone()}
      };
    },
    async keys(){return [...stores.keys()]},
    async delete(name){return stores.delete(name)}
  };
  vm.runInNewContext(worker,{URL,Response,fetch,caches,self:{location:{origin},
    addEventListener(name,handler){listeners[name]=handler},
    async skipWaiting(){activated=true},clients:{async claim(){}}
  }});
  return {stores,caches,
    offline(){offline=true},fail(path){failPath=path},activated:()=>activated,
    async event(name){let pending;listeners[name]({waitUntil(promise){pending=promise}});await pending},
    request(path,mode='cors',method='GET'){
      let result;listeners.fetch({request:{url:new URL(path,origin).href,mode,method},respondWith(promise){result=promise}});return result;
    }
  };
}

test('precache exact versioned URLs and all HTML script dependencies',async()=>{
  const h=harness();await h.event('install');h.offline();
  const urls=[...html.matchAll(/(?:src|href)="(\/static\/[^\"]+)"/g)].map(match=>match[1]);
  assert(urls.includes('/static/style.css?v=6'));
  assert(urls.includes('/static/vendor/marked.umd.js?v=2'));
  for(const url of urls){
    const response=await h.request(url);
    assert.equal(response.status,200,url);
    assert.match(await response.text(),/^asset /);
  }
  assert(h.activated());
});

test('HTTP errors use cached assets as well as network disconnections',async()=>{
  const h=harness();await h.event('install');h.fail('/static/style.css');
  assert.equal((await h.request('/static/style.css?v=6')).status,200);
});

test('offline navigation has its own styles and no uninitialised workspace',async()=>{
  const h=harness();await h.event('install');h.offline();
  const response=await h.request('/','navigate');
  assert.equal(response.status,503);
  assert.match(response.headers.get('content-type'),/text\/html/);
  const body=await response.text();
  assert.match(body,/<style>/);
  assert.match(body,/Tailscale/);
  assert(!body.includes('id="workspace"'));
  assert(!body.includes('演示环境'));
});

test('API, downloads, other origins and write requests bypass the cache',async()=>{
  const h=harness();await h.event('install');
  assert.equal(h.request('/api/chats'),undefined);
  assert.equal(h.request('/api/sources/private','navigate'),undefined);
  assert.equal(h.request('/static/style.css','cors','POST'),undefined);
  assert.equal(h.request('/other-page','navigate'),undefined);
  assert.equal(h.request('https://other.example/static/style.css'),undefined);
});

test('hidden panels have a minimal inline style even when the main CSS fails',()=>{
  assert.match(html,/<style>\.hidden\{display:none!important\}<\/style>/);
});

test('newly loaded fonts are cached without preloading the whole font library',async()=>{
  const h=harness();await h.event('install');
  const font='/static/vendor/katex/fonts/KaTeX_Main-Regular.woff2';
  await h.request(font);h.offline();
  assert.equal((await h.request(font)).status,200);
});

test('incomplete installation never activates or removes the old cache',async()=>{
  const h=harness();await h.caches.open('hikari-shell-v2');h.fail('/static/app.js');
  await assert.rejects(h.event('install'));
  assert(!h.activated());
  assert(h.stores.has('hikari-shell-v2'));
});

test('activation removes only old Hikari caches',async()=>{
  const h=harness();await h.caches.open('other-app');await h.caches.open('hikari-shell-v2');
  await h.event('install');await h.event('activate');
  assert(h.stores.has('other-app'));
  assert(!h.stores.has('hikari-shell-v2'));
});
