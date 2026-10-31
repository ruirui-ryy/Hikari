import { mkdir, copyFile, cp } from 'node:fs/promises';
await mkdir('web/vendor/katex',{recursive:true});
for(const [src,dst] of [['marked/lib/marked.umd.js','marked.umd.js'],['dompurify/dist/purify.min.js','purify.min.js'],['katex/dist/katex.min.js','katex/katex.min.js'],['katex/dist/katex.min.css','katex/katex.min.css'],['katex/dist/contrib/auto-render.min.js','katex/auto-render.min.js'],['marked/LICENSE','MARKED-LICENSE'],['dompurify/LICENSE','DOMPURIFY-LICENSE'],['katex/LICENSE','KATEX-LICENSE']])await copyFile('node_modules/'+src,'web/vendor/'+dst);
await cp('node_modules/katex/dist/fonts','web/vendor/katex/fonts',{recursive:true});
await mkdir('web/vendor/pdfjs',{recursive:true});
for(const file of ['pdf.mjs','pdf.worker.mjs'])await copyFile('node_modules/pdfjs-dist/legacy/build/'+file,'web/vendor/pdfjs/'+file);
for(const dir of ['cmaps','standard_fonts'])await cp('node_modules/pdfjs-dist/'+dir,'web/vendor/pdfjs/'+dir,{recursive:true});
await copyFile('node_modules/pdfjs-dist/LICENSE','web/vendor/pdfjs/LICENSE');
