"""Fixed fictional Chinese snapshot shared by experiment and browser preview."""
from uuid import NAMESPACE_URL, uuid5

from hikari.note_format import create, parse, render

SAMPLES = [
    ('march', '三月学习安排', '三月在晚上九点学习线性代数，因为白天要上班。', {'valid_from': '2026-03'}),
    ('april', '四月学习安排', '四月改为清晨七点学习线性代数，夜间需要休息。', {'valid_from': '2026-04'}),
    ('wrong', '旧版选书理由', '当时选择教材甲是因为价格便宜。', {}),
    ('fixed', '选书理由纠正', '选择教材甲实际是因为例题详细，旧记录把理由写错了。', {}),
    ('event1', '一次图解请求', '用户请求用示意图解释矩阵变换，尚未反馈理解程度。', {'type': 'episodic'}),
    ('event2', '第二次图解请求', '用户再次请求画图说明特征向量的方向。', {'type': 'episodic'}),
    ('inference', '可视化偏好推测', '根据两次图解请求，推测用户偏好可视化解释；尚未确认是长期偏好。', {'nature': 'inference'}),
    ('learning', '特征向量学习进度', '用户说听懂了特征向量的讲解，还没有独立做题验证，不能认定已经掌握。下次从练习续接。', {'nature': 'user_feedback'}),
    ('missing', '缺失讲义的历史记录', '历史记录：极限讲义第二页曾讨论趋近过程。原始讲义现已缺失，摘录尚未重新核验。', {}),
    ('procedure', '解题复核流程', '完成计算后先检查单位，再代回原式验证边界条件。', {'type': 'procedural'}),
    ('food', '午餐口味', '用户明确表示午餐喜欢清淡，少放辣椒。', {'type': 'core'}),
    ('travel', '周末行程', '周末计划乘火车去海边看日出。', {'type': 'episodic'}),
]
QUERIES = [
    ('学习安排', ['march', 'april']),
    ('天亮以后读书还是深夜读书', ['march', 'april']),
    ('为什么选择教材甲', ['wrong', 'fixed']),
    ('喜欢看图的推断依据', ['inference', 'event1', 'event2']),
    ('特征向量已经掌握了吗下次怎么继续', ['learning']),
    ('极限讲义原文还在吗', ['missing']),
    ('量子纠缠实验设备采购', []),
]


def identity(key):
    return str(uuid5(NAMESPACE_URL, 'hikari-fictional-memory-v1/' + key))


def seed(store):
    if store.scan():
        return  # Preserve preview edits; never reset a user's files automatically.
    for key, title, body, options in SAMPLES:
        text = create(title, body, sources=[dict(type='manual', description='固定合成验收样本，无外部原文')], **options)
        meta, body = parse(text)
        meta['id'] = identity(key)
        meta['recorded_at'] = '2026-11-07T00:00:00+08:00'
        if key == 'missing':
            meta['sources'] = [dict(type='document', doc='fictional-missing-doc', version='fictional-v1',
                                    position=2, excerpt='趋近并不等于取到。')]
        store.add(render(meta, body))
    for a, b, kind, reason in [
        ('april', 'march', 'supersedes', '四月起安排改变，三月当时有效'),
        ('fixed', 'wrong', 'corrects', '旧理由原本记错'),
        ('inference', 'event1', 'derived_from', '第一次图解请求'),
        ('inference', 'event2', 'derived_from', '第二次图解请求'),
    ]:
        store.relate(identity(a), identity(b), kind, reason)
