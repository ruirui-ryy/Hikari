import { mkdir, copyFile, cp } from 'node:fs/promises';
await mkdir('web/vendor/katex',{recursive:true});
for(const [src,dst] of [['marked/lib/marked.umd.js','marked.umd.js'],['dompurify/dist/purify.min.js','purify.min.js'],['katex/dist/katex.min.js','katex/katex.min.js'],['katex/dist/katex.min.css','katex/katex.min.css'],['katex/dist/contrib/auto-render.min.js','katex/auto-render.min.js'],['marked/LICENSE','MARKED-LICENSE'],['dompurify/LICENSE','DOMPURIFY-LICENSE'],['katex/LICENSE','KATEX-LICENSE']])await copyFile('node_modules/'+src,'web/vendor/'+dst);
await cp('node_modules/katex/dist/fonts','web/vendor/katex/fonts',{recursive:true});
