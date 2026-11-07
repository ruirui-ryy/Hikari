"""Real local encoder, fixed synthetic data. No chat model or paid API calls."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop('DEEPSEEK_API_KEY', None)
from hikari.core import ROOT, Config, DB
from hikari.library import Library
from hikari.notes import NoteStore
from hikari.note_retrieval import NoteRetrieval, MODEL_DIR
from memory_samples import seed, identity, QUERIES
import psutil


def main():
    home = ROOT / 'local' / 'memory-benchmark'
    cfg = Config(home)
    db = DB(home / 'app.sqlite3')
    store = NoteStore(cfg, db, Library(cfg, db))
    seed(store)
    retrieval = NoteRetrieval(store)
    # Explicit isolated derived cache; reruns measure a cold index without deleting files.
    retrieval.cache = home / ('vectors-' + str(time.time_ns()) + '.sqlite3')
    process = psutil.Process()
    before = process.memory_info().rss
    cpu = sum(process.cpu_times()[:2])
    start = time.perf_counter()
    retrieval.embedder.load()
    result = dict(model=json.loads((MODEL_DIR / 'manifest.json').read_text('utf-8')),
                  load_seconds=time.perf_counter()-start, rss_before=before,
                  rss_loaded=process.memory_info().rss, notes=len(store.scan()), top_k=3, queries=[])
    start = time.perf_counter()
    retrieval.search('学习安排', 'semantic', 3)
    result['index_and_first_query_seconds'] = time.perf_counter()-start
    result['rss_indexed'] = process.memory_info().rss
    for query, expected in QUERIES:
        record = dict(query=query, expected=[identity(k) for k in expected], modes={})
        for mode in ['keyword', 'semantic', 'hybrid']:
            start = time.perf_counter()
            hits = retrieval.search(query, mode, 3)
            ids = [h['id'] for h in hits]
            record['modes'][mode] = dict(milliseconds=(time.perf_counter()-start)*1000,
                ids=ids, titles=[h['title'] for h in hits],
                recall=len(set(ids) & set(record['expected']))/len(expected) if expected else None)
        result['queries'].append(record)
    result.update(cpu_seconds=sum(process.cpu_times()[:2])-cpu,
                  peak_working_set=process.memory_info().peak_wset,
                  vector_bytes=retrieval.cache.stat().st_size,
                  model_bytes=sum(f.stat().st_size for f in MODEL_DIR.iterdir() if f.is_file()))
    target = home / 'results.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
