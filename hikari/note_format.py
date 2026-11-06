"""Version 1 portable Markdown records. No model calls or source inference."""
from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID, uuid4

import yaml


INVERSE = {'supersedes': 'superseded_by', 'superseded_by': 'supersedes',
           'corrects': 'corrected_by', 'corrected_by': 'corrects',
           'derived_from': 'supports', 'supports': 'derived_from', 'related_to': 'related_to'}


class StrictLoader(yaml.BaseLoader):
    """Keep date precision as text; reject duplicate keys instead of silently losing data."""


def mapping(loader, node):
    result = {}
    for key, value in node.value:
        name = loader.construct_object(key)
        if not isinstance(name, str) or name in result:
            raise ValueError('YAML 字段重复或不是文本')
        result[name] = loader.construct_object(value)
    return result


StrictLoader.add_constructor('tag:yaml.org,2002:map', mapping)


def identity(value):
    if not isinstance(value, str):
        raise ValueError('id/target 必须为 UUID 文本')
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise ValueError('id/target 必须为标准小写 UUID') from None
    return value


def nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + ' 必须为非空文本')


def validate(meta):
    required = {'schema_version', 'id', 'title', 'type', 'nature', 'recorded_at', 'sources', 'relations'}
    if not isinstance(meta, dict) or not required <= meta.keys():
        raise ValueError('缺少必要元信息：' + ', '.join(sorted(required - meta.keys()) if isinstance(meta, dict) else required))
    if meta.keys() - required - {'valid_from', 'valid_until'}:
        raise ValueError('包含未知元信息字段（不支持独立 status）')
    if str(meta['schema_version']) != '1':
        raise ValueError('schema_version 必须为 1')
    meta['schema_version'] = 1
    identity(meta['id'])
    nonempty(meta['title'], 'title')
    if not isinstance(meta['type'], str) or meta['type'] not in {'episodic', 'semantic', 'procedural', 'core'}:
        raise ValueError('type 无效')
    if not isinstance(meta['nature'], str) or meta['nature'] not in {'user_statement', 'user_feedback', 'source_statement', 'inference'}:
        raise ValueError('nature 无效')
    try:
        nonempty(meta['recorded_at'], 'recorded_at')
        stamp = datetime.fromisoformat(meta['recorded_at'])
        if stamp.utcoffset() is None or 'T' not in meta['recorded_at']:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('recorded_at 必须为含时区的 ISO 时间') from None
    for field in ['valid_from', 'valid_until']:
        if field in meta:
            nonempty(meta[field], field)
    if not isinstance(meta['sources'], list) or not meta['sources']:
        raise ValueError('sources 必须包含来源；无外部原文时使用 manual 说明')
    for source in meta['sources']:
        if not isinstance(source, dict) or not isinstance(source.get('type'), str) or source['type'] not in {'chat', 'document', 'manual'}:
            raise ValueError('来源 type 必须为 chat/document/manual')
        fields = {'chat': ['conversation_id', 'message_id', 'excerpt'],
                  'document': ['doc', 'version', 'position', 'excerpt'], 'manual': ['description']}[source['type']]
        for field in fields:
            if field == 'position' and isinstance(source.get(field), int) and source[field] > 0:
                continue
            nonempty(source.get(field), 'sources.' + field)
    if not isinstance(meta['relations'], list):
        raise ValueError('relations 必须为列表')
    seen = set()
    for edge in meta['relations']:
        if not isinstance(edge, dict) or set(edge) != {'type', 'target', 'reason'} or not isinstance(edge['type'], str) or edge['type'] not in INVERSE:
            raise ValueError('关系必须含有效 type、target、reason')
        identity(edge['target'])
        nonempty(edge['reason'], 'relations.reason')
        key = (edge['type'], edge['target'])
        if key in seen:
            raise ValueError('关系重复')
        seen.add(key)
    return meta


def parse(text):
    if not isinstance(text, str) or len(text) > 100000:
        raise ValueError('记忆必须为不超过 100000 字符的 UTF-8 Markdown')
    parts = re.split(r'^---\s*$', text, maxsplit=2, flags=re.M)
    if len(parts) != 3 or parts[0]:
        raise ValueError('缺少 YAML frontmatter 分隔符')
    try:
        meta = yaml.load(parts[1], Loader=StrictLoader)
    except (yaml.YAMLError, RecursionError):
        raise ValueError('YAML 格式错误') from None
    return validate(meta), parts[2].lstrip('\r\n')


def render(meta, body):
    validate(meta)
    # Quoted scalars preserve partial dates and avoid implicit YAML timestamp coercion.
    return '---\n' + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_style='"') + '---\n\n' + body


def create(title, body, *, type='semantic', nature='user_statement', sources, **dates):
    meta = dict(schema_version=1, id=str(uuid4()), title=title, type=type, nature=nature,
                recorded_at=datetime.now().astimezone().isoformat(), sources=sources, relations=[], **dates)
    return render(meta, body)


def filename(meta):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', meta['title']).strip(' .')[:45] or '记忆'
    return title + '--' + meta['id'] + '.md'
