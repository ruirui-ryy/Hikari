const CACHE='hikari-shell-v12';
const OFFLINE=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hikari · 暂时无法连接</title><style>body{margin:0;padding:12vh 24px;background:#fafbf8;color:#283a33;font:17px/1.7 system-ui,sans-serif}main{max-width:480px;margin:auto}a{color:#36644d}</style><main><h1>暂时无法连接 Hikari</h1><p>请确认主机开机、Hikari 正在运行，且手机与主机的 Tailscale 已连接。</p><p>连接恢复后，<a href="/">点击重新打开</a>。聊天和资料仍保存在主机上。</p></main></html>`;

// Read actual HTML URLs, including version queries, so the cache cannot drift
// from the page's stylesheets, scripts and their local dependencies.
async function installShell(){
  const page=await fetch('/',{cache:'reload'});
  if(!page.ok)throw Error('Cannot load Hikari shell');
  const html=await page.clone().text();
  const assets=[...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)]
    .map(match=>new URL(match[1],self.location.origin))
    .filter(url=>url.origin===self.location.origin&&url.pathname.startsWith('/static/'))
    .map(url=>url.pathname+url.search);
  const cache=await caches.open(CACHE);
  await cache.addAll([...new Set(assets)]);
  await cache.put('/',page);
}
self.addEventListener('install',event=>{
  event.waitUntil(installShell().then(()=>self.skipWaiting()));
});
self.addEventListener('activate',event=>event.waitUntil(
  caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('hikari-shell-')&&key!==CACHE)
    .map(key=>caches.delete(key)))).then(()=>self.clients.claim())
));

async function shellResponse(request){
  const url=new URL(request.url);
  const cache=await caches.open(CACHE);
  try{
    const response=await fetch(request);
    if(response.ok){
      // Cache public assets only, never API responses, downloads or chat data.
      if(url.pathname.startsWith('/static/')){
        try{await cache.put(request,response.clone())}catch(error){/* Storage may be full. */}
      }
      return response;
    }
    // HTTP errors also mean failed asset loads, even if fetch did not reject.
  }catch(error){/* Fall back to this release's cached assets. */}
  if(request.mode==='navigate'){
    // This page cannot lose its CSS or expose uninitialised hidden panels.
    return new Response(OFFLINE,{status:503,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}});
  }
  const cached=await cache.match(request);
  return cached||new Response('Hikari resource unavailable',{status:503,headers:{'Content-Type':'text/plain; charset=utf-8'}});
}
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(url.origin!==self.location.origin||event.request.method!=='GET')return;
  if(!url.pathname.startsWith('/static/')&&!(event.request.mode==='navigate'&&url.pathname==='/'))return;
  event.respondWith(shellResponse(event.request));
});
