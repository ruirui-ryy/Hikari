"""Synthetic scale check; does not call a model or read personal documents."""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hikari.core import Config, DB, ROOT, uid
from hikari.library import Library
from pptx import Presentation
from pptx.util import Inches
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

home = ROOT / '.test-scale' / uid()
cfg = Config(home)
db = DB(home / 'app.sqlite3')
lib = Library(cfg, db)
template = home / 'template.pptx'
deck = Presentation()
for i in range(100):
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(4)).text = (
        f'第{i+1}讲 矩阵与线性映射\n特征向量在变换后仍在同一直线上。特征值描述缩放。'
        '这是用于验证中文索引的合成课程资料。矩阵、向量与线性变换之间的关系。' * 4)
deck.save(template)
pdf = PdfWriter()
font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
font_id = pdf._add_object(font)
for i in range(100):
    page = pdf.add_blank_page(width=595, height=842)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font_id})})
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 12 Tf 50 750 Td (Page {i+1}: eigenvector matrix transformation learning notes.) Tj ET'.encode())
    page[NameObject('/Contents')] = pdf._add_object(stream)
pdf_template = home / 'template.pdf'
pdf.write(pdf_template)
for course in range(10):
    folder = lib.root / f'课程{course+1:02d}'
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(10):
        src = template if number % 2 else pdf_template
        shutil.copyfile(src, folder / (f'讲义{number+1:02d}' + src.suffix))
start = time.perf_counter()
lib.scan()
initial = time.perf_counter() - start
start = time.perf_counter()
hits = lib.search('特征向量', '课程01')
search_ms = (time.perf_counter() - start) * 1000
start = time.perf_counter()
lib.scan()
unchanged = time.perf_counter() - start
docs = lib.catalog()
report = {'synthetic': True, 'documents': len(docs), 'units': sum(d['units'] for d in docs),
          'ready': sum(d['status'] == 'ready' for d in docs), 'initial_scan_seconds': round(initial, 2),
          'unchanged_scan_seconds': round(unchanged, 2), 'scoped_search_ms': round(search_ms, 2),
          'search_hits': len(hits), 'database_mb': round(db.path.stat().st_size / 1024**2, 2)}
print(json.dumps(report, ensure_ascii=False))
(home / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
if report['ready'] != 100 or report['units'] != 10000 or not hits:
    raise SystemExit(1)
