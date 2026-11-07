"""Synthetic schema contracts; no personal data or model calls."""
from datetime import datetime
from uuid import UUID

import pytest

from hikari.note_format import create, filename, parse, render


def sample(**kw):
    return create('虚构决定', '自由正文\n\n用户表示理解仅为反馈。\n',
                  sources=[{'type': 'manual', 'description': '合成测试；无外部原文'}], **kw)


def test_roundtrip_identity_dates_and_nature():
    text = sample(valid_from='2026-04', valid_until='2027', nature='user_feedback')
    meta, body = parse(text)
    assert str(UUID(meta['id'])) == meta['id']
    assert datetime.fromisoformat(meta['recorded_at']).utcoffset() is not None
    assert meta['valid_from'] == '2026-04' and meta['valid_until'] == '2027'
    assert meta['nature'] == 'user_feedback'
    assert '"2026-04"' in text
    old_id = meta['id']
    meta['title'] = '改名后的决定'
    assert parse(render(meta, body))[0]['id'] == old_id
    assert filename(meta).endswith('--' + old_id + '.md')
    assert 'status' not in meta


@pytest.mark.parametrize('field,value', [('schema_version', 2), ('id', 'not-uuid'),
    ('recorded_at', '2026-04-01'), ('nature', 'mastered'), ('type', ['semantic']),
    ('valid_from', None), ('sources', []), ('relations', [{'type': 'made_up'}])])
def test_reject_invalid_fields(field, value):
    meta, body = parse(sample())
    meta[field] = value
    with pytest.raises(ValueError):
        render(meta, body)


def test_bad_yaml_duplicate_keys_missing_fields_and_source_identity():
    for text in ['body only', '---\nid: [\n---\nbody', '---\nid: x\nid: y\n---\nbody', '---\ntitle: x\n---\nbody']:
        with pytest.raises(ValueError):
            parse(text)
    meta, body = parse(sample())
    meta['sources'] = [{'type': 'document', 'doc': 'synthetic-doc', 'version': 'v1', 'position': 3,
                        'excerpt': '实际测试原文片段'}]
    assert parse(render(meta, body))[0]['sources'][0]['position'] == '3'
    meta['sources'] = [{'type': 'chat', 'conversation_id': 'synthetic-chat', 'message_id': 'synthetic-message',
                        'excerpt': '我表示理解了'}]
    assert parse(render(meta, body))[0]['sources'][0]['message_id'] == 'synthetic-message'
    meta['sources'][0].pop('excerpt')
    with pytest.raises(ValueError):
        render(meta, body)


def test_partial_dates_must_be_quoted():
    text = sample(valid_from='2026-04')
    with pytest.raises(ValueError, match='引号'):
        parse(text.replace('"2026-04"', '2026-04'))
