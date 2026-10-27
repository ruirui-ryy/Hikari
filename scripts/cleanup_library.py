"""Preview missing-original index records, or back up and remove them with --apply."""
import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hikari.core import Config, DB, uid
from hikari.library import Library


def main():
    parser = argparse.ArgumentParser(description='清理原件已不存在的 deleted 索引记录，保留历史引用。')
    parser.add_argument('--apply', action='store_true', help='先备份数据库，再实际清理；默认仅预览')
    args = parser.parse_args()
    cfg = Config()
    db = DB(cfg.home / 'app.sqlite3')
    library = Library(cfg, db)
    pending = [r['path'] for r in library.catalog(include_deleted=True) if r['status'] == 'deleted']
    if not args.apply or not pending:
        print(json.dumps({'pending': pending, 'applied': False}, ensure_ascii=False))
        return 0
    backup = cfg.home / 'backups' / ('before-library-cleanup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uid()[:6] + '.sqlite3')
    with closing(sqlite3.connect(db.path)) as source, closing(sqlite3.connect(backup)) as destination:
        source.backup(destination)
    result = library.purge_deleted()
    print(json.dumps(result | {'applied': True, 'backup': str(backup), 'historical_citations_retained': True}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
