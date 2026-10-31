import subprocess
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from test_hikari import service


def test_pdf_reader_assets_and_private_original(service):
    app, client = service
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    target = app.state.library.root / 'reading.pdf'
    writer.write(target)
    app.state.library.scan()
    doc = app.state.library.catalog()[0]
    response = client.get(f'/api/documents/{doc["id"]}/file?version={doc["version"]}')
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'].startswith('inline')
    for path in ['pdf.html', 'pdf.js', 'pdf.css', 'vendor/pdfjs/pdf.mjs', 'vendor/pdfjs/pdf.worker.mjs']:
        response = client.get('/static/' + path)
        assert response.status_code == 200
        if path.endswith(('.js', '.mjs')):
            assert response.headers['content-type'].startswith(('application/javascript', 'text/javascript'))
    client.cookies.clear()
    assert client.get(f'/api/documents/{doc["id"]}/file').status_code == 401


def test_bundled_pdf_engine_renders_original_page(tmp_path):
    writer = PdfWriter()
    for color in ['1 0 0', '0 0 1']:
        page = writer.add_blank_page(width=200, height=200)
        stream = DecodedStreamObject()
        stream.set_data(f'{color} rg 0 0 200 200 re f'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    target = tmp_path / 'two-pages.pdf'
    writer.write(target)
    script = '''
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {createCanvas} from '@napi-rs/canvas';
import {getDocument} from './web/vendor/pdfjs/pdf.mjs';
const loading=getDocument({data:new Uint8Array(readFileSync(process.argv[1])),useWasm:false,isEvalSupported:false});const pdf=await loading.promise;
assert.equal(pdf.numPages,2);
for(const [number,expected] of [[1,[255,0,0]],[2,[0,0,255]]]){
 const page=await pdf.getPage(number),view=page.getViewport({scale:2});
 const canvas=createCanvas(view.width,view.height),context=canvas.getContext('2d');
 await page.render({canvasContext:context,viewport:view}).promise;
 assert.deepEqual(Array.from(context.getImageData(100,100,1,1).data).slice(0,3),expected);
}
await loading.destroy();
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script, str(target)],
                            cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
