"""Download public model artifacts only. Never send memory text to a server."""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hikari.core import ROOT, digest, atomic_write
from hikari.note_retrieval import MODEL, MODEL_DIR, MODEL_REVISION


def prepare():
    os.environ['HF_HOME'] = str(ROOT / 'local' / 'huggingface')
    from huggingface_hub import snapshot_download
    repo = 'Qdrant/bge-small-zh-v1.5'
    revision = MODEL_REVISION
    path = Path(snapshot_download(repo, revision=revision,
                cache_dir=str(ROOT / 'local' / 'huggingface'),
                allow_patterns=['model_optimized.onnx', 'tokenizer.json', 'config.json',
                                'tokenizer_config.json', 'special_tokens_map.json']))
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    files = {}
    for source in path.iterdir():
        if source.is_file():
            target = MODEL_DIR / source.name
            shutil.copyfile(source, target)
            files[source.name] = digest(target.read_bytes())
    atomic_write(MODEL_DIR / 'manifest.json', json.dumps(dict(model=MODEL, repository=repo,
                 revision=revision, files=files), indent=2))
    from hikari.note_retrieval import LocalEmbedding
    LocalEmbedding().encode(['合成中文模型检查'])
    print(json.dumps(dict(model=MODEL, revision=revision, bytes=sum((MODEL_DIR/f).stat().st_size for f in files))))


if __name__ == '__main__':
    prepare()
