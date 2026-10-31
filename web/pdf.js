import {getDocument,GlobalWorkerOptions} from './vendor/pdfjs/pdf.mjs';
GlobalWorkerOptions.workerSrc='/static/vendor/pdfjs/pdf.worker.mjs';
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search);
let loadingTask,pdf,page=1,zoom=1,sequence=0,renderTask;
const loading=$('status');
function message(text,error=false){loading.textContent=text;loading.classList.toggle('error',error)}
function controls(){for(const id of ['page','fit','in','out'])$(id).disabled=!pdf;$('prev').disabled=!pdf||page<=1;$('next').disabled=!pdf||page>=pdf.numPages;$('page').value=page}
async function render(){
  if(!pdf)return;
  const token=++sequence;
  if(renderTask){renderTask.cancel();try{await renderTask.promise}catch(error){}if(token!==sequence)return}
  controls();message('正在显示第 '+page+' 页…');
  try{
    const sheet=await pdf.getPage(page);if(token!==sequence)return;
    const natural=sheet.getViewport({scale:1});
    const width=Math.max(180,$('stage').clientWidth-24);
    const view=sheet.getViewport({scale:width/natural.width*zoom});
    // Limit bitmap memory on phones while allowing the page itself to zoom.
    const ratio=Math.min(devicePixelRatio||1,2,Math.sqrt(16000000/(view.width*view.height)));
    const canvas=$('canvas');canvas.width=Math.ceil(view.width*ratio);canvas.height=Math.ceil(view.height*ratio);
    canvas.style.width=view.width+'px';canvas.style.height=view.height+'px';canvas.hidden=true;
    renderTask=sheet.render({canvasContext:canvas.getContext('2d'),viewport:view,transform:[ratio,0,0,ratio,0,0]});
    await renderTask.promise;if(token!==sequence)return;
    canvas.hidden=false;message('第 '+page+' 页 · '+Math.round(zoom*100)+'% 适宽缩放');
    const url=new URL(location.href);url.searchParams.set('page',page);history.replaceState(null,'',url);
  }catch(error){if(error.name!=='RenderingCancelledException'&&token===sequence){$('canvas').hidden=true;message('这一页显示失败，请重试或下载原件查看。',true)}}
}
function go(value){const n=Number(value);if(Number.isInteger(n)&&n>=1&&n<=pdf.numPages){page=n;render()}else{controls();message('请输入 1 到 '+pdf.numPages+' 之间的页码。',true)}}
$('prev').onclick=()=>go(page-1);$('next').onclick=()=>go(page+1);
$('page').onchange=event=>go(event.target.value);
$('page').onkeydown=event=>{if(event.key==='Enter')go(event.target.value)};
$('in').onclick=()=>{zoom=Math.min(4,zoom*1.25);render()};$('out').onclick=()=>{zoom=Math.max(.5,zoom/1.25);render()};$('fit').onclick=()=>{zoom=1;render()};
$('back').onclick=event=>{if(document.referrer&&new URL(document.referrer).origin===location.origin){event.preventDefault();history.back()}};
let resizeTimer,lastWidth=0;new ResizeObserver(()=>{const width=$('stage').clientWidth;if(width===lastWidth)return;lastWidth=width;clearTimeout(resizeTimer);resizeTimer=setTimeout(render,150)}).observe($('stage'));
async function boot(){
  const doc=params.get('doc'),version=params.get('version');
  if(!doc||!version){message('缺少文件或来源版本，请返回对话重新打开。',true);return}
  try{
    const meta=await fetch('/api/documents/'+encodeURIComponent(doc)+'/content',{credentials:'same-origin'});
    if(meta.status===401)throw Error('登录已过期，请返回 Hikari 重新登录。');
    if(!meta.ok)throw Error('原件已变化或不可用，请重新检索后打开。');
    const info=await meta.json();if(info.version!==version)throw Error('原件已更新，不能用新版本替代这条引用。');
    const name=info.path.split('/').pop();$('title').textContent=name;$('title').title=info.path;document.title=name+' · Hikari';
    const url='/api/documents/'+encodeURIComponent(doc)+'/file?'+new URLSearchParams({version});
    loadingTask=getDocument({url,withCredentials:true,isEvalSupported:false,useWasm:false,cMapUrl:'/static/vendor/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/'});pdf=await loadingTask.promise;
    page=Math.max(1,Math.min(pdf.numPages,Number(params.get('page'))||1));page=Math.floor(page);
    $('total').textContent=pdf.numPages;$('page').max=pdf.numPages;
    $('download').href=url;$('download').download=name;$('download').hidden=false;
    await render();
  }catch(error){message(error.message?.startsWith('原件')||error.message?.startsWith('登录')?error.message:'PDF 加载失败，请检查主机与 Tailscale 连接，返回后重试。',true)}
}
window.addEventListener('pagehide',event=>{if(!event.persisted){sequence++;renderTask?.cancel();loadingTask?.destroy()}});
boot();
