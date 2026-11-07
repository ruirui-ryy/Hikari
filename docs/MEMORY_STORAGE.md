# 长期记忆第一层：Markdown 存储与关系

2026-11-07。本文记录本轮访谈后的最终决定，优先于旧架构中的状态字段、主动撤销与整文件永久保护要求。实现为 `hikari/note_format.py`、`hikari/notes.py`；入口是 `Memory.notes`（`NoteStore`），本层无 HTTP/UI 或主 AI 工具接入，无模型调用。

## 文件与字段

`memory/notes/` 首版只有单层目录，一文件是一条可独立变化、取代或纠正的结论。情景保留完整经历，语义归纳单独保存并关联，不强求每次对话产生记忆。程序新建文件名为“简短标题--完整UUID.md”；身份只取 frontmatter 内 UUID，手动重命名不改变身份。读取每次扫描实际文件，不依赖过期路径索引。

UTF-8 Markdown 的 YAML frontmatter 必须有 `schema_version: 1`、`id`（标准小写 UUID）、`title`、`type`、`nature`、`recorded_at`、非空 `sources` 和 `relations` 列表。正文自由书写，不提取正文中的关系为正式边。不接受独立 `status` 字段。

`type` 为 episodic / semantic / procedural / core；`nature` 为 user_statement / user_feedback / source_statement / inference。用户表示理解属于反馈，不能据此认定掌握。`recorded_at` 是实际创建记录的含时区 ISO 时间，`create()` 在调用时生成。历史有效时间另外放在可选 `valid_from`、`valid_until` 引号文本中，允许年、年月、日期或已知的时间描述，不补造未知精度，未知则省略。外部手写记录的真实性由作者负责，存储层只校验格式。

两份独立结论示例（全部虚构，时间也是示意）：

```yaml
---
schema_version: 1
id: 1ba4ed2e-2dd1-4c31-8df3-741777d2ac21
title: 三月学习安排
type: semantic
nature: user_statement
recorded_at: "2026-11-07T00:00:00+08:00"
valid_from: "2026-03"
sources:
  - type: manual
    description: "虚构例子，无外部原文"
relations:
  - type: superseded_by
    target: 73834364-be81-48fb-bc67-2b32c337edaa
    reason: "四月起安排发生变化，三月安排当时有效"
---

三月选择晚上学习。
```

```yaml
---
schema_version: 1
id: 73834364-be81-48fb-bc67-2b32c337edaa
title: 四月学习安排
type: semantic
nature: user_statement
recorded_at: "2026-11-07T00:01:00+08:00"
valid_from: "2026-04"
sources:
  - type: manual
    description: "虚构例子，无外部原文"
relations:
  - type: supersedes
    target: 1ba4ed2e-2dd1-4c31-8df3-741777d2ac21
    reason: "四月起安排发生变化，三月安排当时有效"
---

四月选择早上学习。
```

## 关系与来源

每条关系必须有 `type`、稳定 ID `target`、非空 `reason`，双方显式保存：supersedes ↔ superseded_by，corrects ↔ corrected_by，derived_from ↔ supports，related_to ↔ related_to。取代表示后来改变；纠正表示旧记录原本错误。取代、纠正及两者混合形成的循环和自指均禁止，普通相关允许对称；相关必须说明联系，不持久化词语或语义相似性。

允许多个继任结论，读取的 `summary` 返回所有取代/纠正去向及目标所写有效时间，不按记录时间挑胜者。即使旧记录没有 valid_until，也仍保留继任提示。依赖的依据被纠正后，归纳仍保留，仅增加 `review_hints`，交后续 Consolidation 复核。

`sources` 来源字段：

| type | 必须字段 |
| --- | --- |
| chat | conversation_id、message_id、excerpt |
| document | doc、version、position、excerpt |
| manual | description（无外部原文时明确说明） |

只能保存实际读到的原文摘录，概括不能冒充原话。调用者负责提供实际阅读的版本和定位；本层不会凭 ID 读取资料正文或伪造核验。`scan/read` 的来源返回 `excerpt_state=historical_saved`、`excerpt_reverified=false`，资料另给 `current_document_version`（文件版本是否仍一致），版本一致也不表示本次重新核验过摘录。聊天删除、原件缺失时必要历史摘录仍保留；记忆可读，来源当前不可核验也不自动判结论为假。

## 接口、账本与异常

- `create()` 生成身份、实际记录时间和 Markdown；`add(text)` 写入一个无待补反向边的新记录。
- `scan()` 扫描并重建派生索引；`read(id, for_ai=False)` 按身份读取。`for_ai=True` 拒绝异常记录；当前主 AI 不调用此接口。
- `relate(source, target, type, reason)` 为已有记录整组写入双向关系。
- `apply(changes, expected, actor='user', repairs=None)` 提交完整文件方案。键为 notes/ 内单层文件名；值为完整文本，或 None 表示删除。expected 必须为整组逐文件当前字节 SHA-256（新文件为 None）。新增并关联可一次提交全部新旧文件。删除或替换导致悬空边会被拒绝，须同组处理关系。
- `operation(id)` 显式查看状态、路径和前后字节快照；`recover()` 恢复 pending 操作。没有主动撤销方法。

方案先全量解析和校验受影响关系，然后在 SQLite `memory_operations` 持久化整组文件的前后字节快照，再复核现场，逐文件原子替换，整组核验后标记 applied。文件和数据库不具备瞬时跨文件事务；外部编辑器不遵循应用锁，比较与替换之间仍有极短竞争窗口，应避免同时编辑同一文件。应用备份共用记忆锁。

任一文件变成前后快照以外的第三种内容，整组停止并持久化 conflict，保留现场，不永久锁定文件。pending 中断恢复只在各文件仍等于前或后快照时继续原方案，包括全为旧快照的情形；无模型请求，重复恢复幂等。conflict 重启后不重试；pending/conflict 涉及的文件、同身份改名文件及直接入边记录会提示“关联操作未完成”，可查看，暂不正常提供给 AI。

恢复同时检查方案涉及 UUID 的实际文件位置。若中断后在方案外发现同 ID 的改名文件或副本，即使原路径缺失仍匹配新建前快照，也标为 conflict；不重建旧路径、不自动采纳或覆盖手工文件。方案内明确安排的整组改名可正常恢复，方案外改名须基于当前文件显式修复。

显式修复须用 `repairs=旧操作ID`，读取当前整组内容，覆盖旧组全部路径并制定新的合法方案（允许保留当前内容、删除或改名，但不做自动合并）。修复完成后新旧操作双向关联，旧记录才标 repaired 并解除隔离；修复中再次中断仍走同一恢复规则。

修复若再次发生外部编辑冲突，允许重新显式发起修复；新方案须覆盖同一修复链所有未完成操作的文件。仅在新方案完成后统一关联并解除这些冲突；存在 pending 修复时先恢复，不提交重叠修复。

坏 YAML、缺字段、坏编码保留原文件并报具体字段或编码错误，不重写、不按旧格式导入。重复 ID 的所有文件均异常，入边目标歧义；缺失目标的关系保留。外部手改形成自指、循环或缺反向边同样局部隔离，不按异常关系推论，无关记录继续可用。程序提出同类无效方案则在账本及文件落盘前整组拒绝。只有显式扫描结果可作为索引，不能直接拿数据库缓存充当当前可用记忆。

`memory_note_index` 只有派生身份/关系元信息，可删除后扫描恢复；`memory_operations` 是不可由 Markdown 重建的操作历史和快照。没有 memory/.history。完整修订及恢复能力必须同时备份 Markdown 与 app.sqlite3，不能把删除派生索引当作删除数据库。

## 兼容范围与验收

旧 `profile/learning/understanding` API、自动提取、修订和撤销仅为既有功能兼容保留，仍是旧格式；它们不识别、不导入、不修改 notes/，数据库中误入 notes/ 的旧修订也不能恢复或撤销新数据。邮件独立撤销不变。本层新格式不进入旧 context、memories 工具或闲置整理的整库输入；新的按需读取和写入工具留后续层接入。当前记忆网页仍只显示旧格式，notes/ 通过本地编辑器和存储接口验收，尚无专门冲突界面。

`tests/test_note_format.py` 和 `tests/test_notes.py` 用虚构内容验证字段、稳定身份改名、取代与纠正双向边、有效日期精度、分叉、循环、来源丢失、性质、索引重建、各阶段故障、幂等恢复、手改冲突、冲突不重试、显式修复、坏文件局部隔离及旧入口排除。测试不是实际模型记忆质量、真实资料来源核验或手机验收，不覆盖检索、Embedding、图谱、整理判断与主 AI 两工具。
