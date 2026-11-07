# 长期记忆第二层：读取与检索

2026-11-07。承接第一层 `9c240af`，只接入独立读取、检索和人工网页入口；不接入主 AI、写入或闲置整理。无付费 API 调用。`AGENTS.md` 的已有修改不属于本轮。

## 接口与派生索引

`NoteRetrieval.read(UUID)` / `GET /api/notes/{UUID}` 返回 `id/title/body/markdown/hash/status/relations/sources`。`markdown` 是本次扫描到的完整文件，body 是正文；关联包含类型、目标 UUID、理由，用户可以继续读取。唯一但异常的记录可人工查看，并明确提示 issues；不存在或重复 UUID 拒绝读取。来源仍为历史保存、本次未重新核验。

`search(query, mode, limit=5)` / `GET /api/notes/search?q=…&mode=keyword|semantic|hybrid&limit=5` 必须明确模式，候选数 1–20，查询 1–1000 字符，返回 `id/title/status/score`。HTTP 接口沿用登录保护与 no-store。status 仅包含 nature、valid_from、valid_until、所有 successors（含后继有效起点）、review_hints、available、issues；不新增持久化状态，不根据 recorded_at 挑胜者。不把“没有后继”当作“目前正确”，自由文本有效时间不自动推断区间。

每次检索重新扫描 Markdown 和操作账本，仅搜索 available 的记录，不自动恢复冲突。关键词用既有 jieba 搜索分词和 BM25（k1=1.2、b=0.75），无隐式同义词扩展。语义按正文 300 字符、步长 240 分段，每段加标题前 80 字符，编码归一化后算余弦相似度，一条记忆取最高片段分数。模型自身上限为 512 tokens，极端字符仍可能被截断；长文件阅读返回完整正文。混合对两份完整排名做等权 RRF，常数 60，以 UUID 稳定打破平分；不自动沿关系扩展候选。三种 score 尺度不同，不能横向比较为置信度。

向量为可删除的 SQLite 派生缓存，位于 `local/memory-retrieval/<数据目录哈希>/vectors.sqlite3`；以 UUID、文件字节哈希、模型清单哈希和分段版本判失效。删除、异常、隔离记录不会从旧缓存返回。关键词每次现算。模型清单/权重变更在新进程加载时验证 SHA-256，重启后采用新版本，进程内模型固定；向量和权重均可重新生成，不替代第一层操作账本备份。缓存含私人向量，保持 local/ 忽略，不发布或上传。

## 本地 Embedding 与成本

采用 fastembed **0.7.4**、ONNX Runtime **1.31.1**，`BAAI/bge-small-zh-v1.5`，CPUExecutionProvider、2 线程、512 维；未引入第二个 Agent。准备脚本只下载公共模型资产，不上传记忆；正常搜索用 specific_model_path + local_files_only，不联网下载，缺失时显式失败，关键词仍可用，不静默降级模式。

实际读取并核验的上游依据：[FastEmbed 模型注册实现](https://github.com/qdrant/fastembed/blob/v0.7.4/fastembed/text/onnx_embedding.py)、[本地加载实现](https://github.com/qdrant/fastembed/blob/v0.7.4/fastembed/text/text_embedding.py)。本机下载清单固定仓库 `Qdrant/bge-small-zh-v1.5` 的 revision **46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59**；ONNX SHA-256 **1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38**。完整清单位于 local/embedding/bge-small-zh-v1.5/manifest.json，准备脚本记录实际下载版本，不把 main 当作不可变版本。

在本机 Windows / Python 3.13.11 上，12 条合成记录、top-3、7 个固定查询的单次实测（非 N100/N150）：模型文件约 **90.8 MiB**；下载缓存另保留一份，Python 依赖还需磁盘空间。模型载入 **0.91 秒**，建向量加首次语义查询 **0.092 秒**。进程 RSS 从 **29.7 MiB** 到载入后 **187.0 MiB**、建索引后 **190.1 MiB**，进程历史峰值工作集 **254.6 MiB**；整次测量 CPU 时间 **3.05 秒**。12 条的 SQLite 向量缓存 **156 KiB**（JSON 浮点存储，最小版本并非紧凑二进制）。每次语义查询编码一次问题，新增/变更记录重新编码片段，未变记录复用；关键词不调用模型。数据扩大时全文扫描、关键词计算和全向量遍历成本随库增长，暂未做大规模优化。

## 同一快照的三模式对比

固定样本与查询见 scripts/memory_samples.py，均虚构，含旧/新安排、理由纠正、两次图解请求及独立推测、学习反馈、原件缺失和无关记录。原件缺失使用虚构 document 来源；不代表核验过真实讲义。scripts/benchmark_memory.py 实际调用本地编码器，输出 local/memory-benchmark/results.json；每次新建独立缓存，保留结果。不调用聊天模型。下表为必要记录召回率 @3，人工预先写定 expected，同一快照同一 k；不是回答质量：

| 查询 | 关键词 | 语义 | 混合 |
| --- | --- | --- | --- |
| 学习安排（新旧两条） | 2/2 | 2/2 | 2/2 |
| 天亮以后读书还是深夜读书（同上） | 0/2 | 2/2 | 2/2 |
| 为什么选择教材甲（原理由和纠正） | 2/2 | 2/2 | 2/2 |
| 喜欢看图的推断依据（推测和两条依据） | 1/3 | 3/3 | 1/3 |
| 特征向量已经掌握了吗下次怎么继续 | 1/1 | 1/1 | 1/1 |
| 极限讲义原文还在吗 | 1/1 | 1/1 | 1/1 |
| 量子纠缠实验设备采购（无相关记录） | 无结果 | 返回3条无关 | 返回3条无关 |

6 个有答案查询平均 recall：关键词 72.2%、语义 100%、混合 88.9%。关键词冷分词首次 393 ms，后续约33–45 ms；语义热查询约37–42 ms，混合约39–44 ms（均含磁盘扫描与来源检查）。等权融合可能将两模式共有的弱候选抬高；不因合成小样本调权重。不设未标定的语义相关阈值，因此无关查询仍返回最近候选，界面提示读正文判断。需要的关联不一定在 top-k，应打开结果再沿关系读取。

## 恢复修复的实际范围

第一层 `9c240af` 在 pending 恢复每次检查时扫描相关 UUID 的实际位置。方案外同 ID 的改名或副本会标 conflict，即使原创建路径缺失也不重新创建、不覆盖手工内容；方案内明确声明的整组改名可完成。已有故障测试覆盖改名后再编辑、复制、计划内改名和幂等恢复。仍有外部编辑器不遵守应用锁的比较/替换竞争窗口，不承诺跨文件瞬时事务。该修复没有网页冲突修复入口、手机用户验收或主 AI 接入。本层只提供异常记录的只读查看。

## 验证与人工入口

开发检查包括 tests/test_note_retrieval.py 的接口/登录/显式模式、正文一致、关系时间、改名复用、手改与模型版本失效、删除和异常隔离、模型不可用不降级、长正文分段，以及第一层格式与故障回归。FakeEmbedding 仅用于接口测试；上面的对比另用了真实本地编码器。网页与真机验收单独记录，不用合成测试替代。

主机准备与隔离验收：

```powershell
.\local\venv\Scripts\python.exe scripts/prepare_memory_embedding.py
.\local\venv\Scripts\python.exe scripts/benchmark_memory.py
.\local\venv\Scripts\python.exe scripts/preview_memory.py
```

打开 http://127.0.0.1:8767，账户 `preview`，密码 `local-preview-only`，点「长期记忆」。隔离数据仅在 local/memory-preview，脚本不覆盖已有预览修改，不读正式个人记忆，后台 Agent 停用。预览只监听本机，不能直接作为手机外网入口。

1. 用「学习安排」分别选择三种模式搜索；打开三月安排，核对正文及“被取代”提示，点关联 UUID 跳到四月，再沿反向关系回看。
2. 用「天亮以后读书还是深夜读书」比较模式差别；用「为什么选择教材甲」追溯纠正。用看图问题打开推测并逐条读依据。
3. 打开学习进度，确认反馈不等同掌握；打开缺失讲义记录，确认来源历史保存且当前不可核验。按 UUID 可独立直读，完整 Markdown 含元信息可展开核对。
4. 用无关采购问题确认语义候选不等于相关证据。正式服务重启加载新代码后同样可用此界面检索现有 notes/；没有 notes 的正式数据应显示空结果，合成样本不自动灌入正式数据。

待用户验收：第一层网页/手机体验仍未验收；本层需用户在实际网页操作上述流程，并在既有手机 HTTPS 入口检查点击、关联追溯、长正文滚动、重连与登录。真实个人记忆质量、真实课程原件核验、主 AI 自选检索/补读/回答质量以及 N100/N150 性能均未验收，留后续层。
