# 运行与维护

## 环境与首次启动

当前已准备项目独立 Python 环境。直接运行 `scripts/start.ps1`，首次设置单账户，密码至少 10 位。服务默认只监听 127.0.0.1:18765，由连接工具转发外网请求。

端口可在 `data/config.json` 中通过 `port` 配置，修改后需重启服务与连接程序。启动时会检查端口占用，外网脚本会核对正式 Hikari 健康检查，避免将隧道连接到其他程序。

若重复启动时提示“Hikari 已在运行”，直接使用已有服务即可；要加载代码更新，请先在原服务窗口按 Ctrl+C 停止，再运行 `scripts/start.ps1`。仅在确认其他程序占用端口时考虑改端口。Tailscale 状态通过 Python 按 UTF-8 读取并转成 ASCII JSON，再交给 Windows PowerShell，避免账户中文名被错误解码导致 JSON 解析失败；读取失败不输出整份设备和账户列表。

新机器可在项目目录执行：

```powershell
python -m venv --without-pip local/venv
python -m pip --python local/venv install -r requirements.lock.txt
.\local\venv\Scripts\python.exe -m hikari setup
.\local\venv\Scripts\python.exe -m hikari
```

启动脚本首次安装依赖要求主机 Python 已有 pip。锁定依赖文件为当前验证的 Windows / Python 3.13 环境。

配置文件：`data/config.json`。运行数据与密钥被 Git 忽略。`HIKARI_DATA` 可在启动前指定另一个独立数据目录。不要启动两个实例使用同一目录；不要删锁文件绕过保护。

失败或中断的任务提示可选择“重试”，或点击右侧“×”关闭。关闭状态保存在主机，刷新、切换设备或重启后不会重新显示；聊天、任务记录和已完成的记忆修改仍保留。排队中的任务使用“取消”，执行中的任务不能用关闭提示的方式取消。

## 模型

默认 `https://api.deepseek.com`、`deepseek-flash`。当前官方对应 Flash 1017，服务商可能更新别名对应版本。

密钥优先使用本机配置，没有时读取 `DEEPSEEK_API_KEY`。网页不返回已有密钥；留空保存时保留原配置。连接测试会发送简短合成内容并产生少量用量。

模型请求直接使用 HTTPS；依赖下载工具可使用主机已有代理。若模型必须通过代理访问，需要另行配置并验证，不静默将密钥发送到替代 API 服务。

## 资料与记忆

默认原件位于 `data/knowledge/`，可以放课程子目录。上传后或主机修改文件后，后台每约 15 秒检查更新；首次大批导入期间可在资料库查看状态。

不支持的扩展名不索引；PDF/PPTX 可能缺少图片和公式。单文件默认上限 100 MB；Office 解压内容上限 512 MB。解析错误可通过“检查更新”安排重试。

Agent 不修改原件。网页同名上传会拒绝，请改名，或在主机主动替换已有文件。变更中的原件会使旧来源核验失败；旧回答保留原来读取的文字，不承诺归档完整旧文件。

移动或删除原件后，旧路径可能保留为内部 `deleted` 记录，正常资料目录不会列出它们。若要清除这些索引记录，可先预览，再执行清理：

```powershell
.\local\venv\Scripts\python.exe scripts/cleanup_library.py
.\local\venv\Scripts\python.exe scripts/cleanup_library.py --apply
```

实际清理前会在 `data/backups/` 保存数据库快照。清理仅移除原件已不存在的旧索引记录，不删除资料文件、聊天、记忆或历史引用文字；重新放回原位置的文件会跳过，等待索引刷新。旧理解的来源失效状态仍然保留。

`data/memory/` 下的 `profile`、`learning`、`understanding` 分别保存偏好、学习记录和资料理解。支持网页或外部编辑器编辑，检测到手工修改会保护整个文件。第一版尚未实现条目级合并；有新证据时由用户处理冲突。

个人记忆无需说“记住”。依后续确认，采用聊天闲置后批量整理：每轮回答先保存为待整理，默认闲置 120 秒且没有排队任务时，由原有单 Agent 处理同一聊天最多 20 轮。可在 `data/config.json` 调整 `memory_idle_seconds`，重启生效。已有资料索引清理和失败提示关闭行为保持。

每批额外向已配置的 DeepSeek 接口发送该批消息与回答、最多 10 条此前历史和已有记忆，用于提取、去重及纠正；产生一次额外请求的输入、输出及推理用量，费用随上下文大小变化，不能保证比所有方案便宜。连续对话可合成一批；没有新增内容也仍需这次判断请求，但不会改文件。整理限 45 秒，新问题可能等待当前请求结束（仍为单 Agent）；期间新消息到达会丢弃候选并延后整理。

自动个人条目写在 `profile/自动记忆.md` 和 `learning/自动记忆.md`，区分用户事实、用户反馈与助手推测，附用户原话和原对话轮次。相同主题合并，明确纠正替换原主题；语义是否重复、是否值得长期保留由模型判断，程序只校验格式、原话、权限及版本，不能保证模型不会误判。手工修改、删除或撤销后整个文件受保护，AI 不覆盖；可通过原有记忆界面检查修订和撤销。资料理解笔记仍要求本轮已读资料来源。

聊天 API 的任务字段 `memory_status` 表示 pending（待闲置整理）、reviewing、updated、unchanged、failed、skipped 或 interrupted，`memory_error` 提供不含原文的失败说明。失败不影响已保存回答，不自动重试不确定写入；重启后 pending 仍可处理，reviewing 标为 interrupted。升级前已完成的历史任务不会自动批量回填。上下文超过 10 万字符时跳过整理，需先精简记忆或分开处理。

撤销受后续修改保护。撤销新建或清空记忆后保留空的受保护文件，避免 AI 自动重新创建。文件原子替换前会再次比较内容，但外部编辑器不遵守应用锁，仍应避免同一时刻编辑同一文件。

只有要求保存完整笔记、总结或练习时，模型才应使用生成文件工具；相关文件位于 `data/generated/`。自然语言意图由模型判断，程序不保证其判断永远正确。

## 手机外网访问

### 推荐：Tailscale 固定私网入口

适用于手机与主机都已经连接同一个 Tailscale 网络的个人使用场景。先运行 `scripts/start.ps1` 启动助手，再在另一个 PowerShell 窗口运行一次：

```powershell
.\scripts\tailscale.ps1
```

脚本核对正式助手和现有转发，使用 `tailscale serve --bg --https=443` 转发到配置中的本机端口。遇到其他服务占用同一入口时拒绝覆盖，不启用公网 Funnel。

首次使用需由网络管理员在 Tailscale 的 DNS 设置中启用 HTTPS Certificates，MagicDNS 也必须开启。这不是 Services 页面里的“定义服务”，无需创建服务名称或 ACL 标签。证书的设备域名会进入公开证书记录，但网页访问仍受 Tailscale 网络权限限制。

入口格式为 `https://设备名.网络名.ts.net`。设备名、网络名保持不变时，重启后的网址仍相同。`--bg` 转发由 Tailscale 持久保存，重启 Tailscale 后会恢复；Hikari 本身仍需运行 `scripts/start.ps1` 启动，并保持主机开机、两端 Tailscale 在线。

首次访问新域名时重新使用 Hikari 个人账户登录。手机浏览器可将此地址添加到桌面。不需要运行 `tunnel.ps1`。

原来的 `scripts/tunnel.ps1` 现在也默认调用 Tailscale 方案，避免沿用旧命令时重新创建临时公网入口。

如需关闭此入口，在确认 443 仍仅用于本项目后执行 `tailscale serve --https=443 off`；不要用全局 reset 清除其他服务配置。

### 可选：Cloudflare 临时公网入口

先启动主机服务，再在另一个 PowerShell 窗口运行：

```powershell
.\scripts\tunnel.ps1 -Cloudflare
```

脚本使用项目内 `tools/cloudflared.exe`；缺失时从 Cloudflare 官方 GitHub 发布下载，校验发布提供的 SHA256。临时网址出现在终端，在 Android 浏览器打开后使用同一个个人账户登录。

临时隧道地址每次重建会变化，无生产可用性保证；不用于 Tailscale 固定入口。主机关机、休眠或连接程序停止后不能远程提问。手机添加到桌面需要浏览器支持；真实 Android 设备的安装和网络仍需用户端验证。

外网入口必须使用 HTTPS。开启设置中的安全 Cookie 后，后续登录只能通过 HTTPS；若要退回本机 HTTP 登录，停服后将 `data/config.json` 的 `secure_cookie` 改为 false。登录有效期 30 天，退出使当前会话失效，重设账户会清除所有会话。

## 备份与恢复

设置中的备份在没有执行/排队任务时创建一致性 SQLite 快照，并打包 Markdown 记忆、生成文件及邮件区域。包含邮件正文、附件、草稿、发送记录和邮件个人资料，请妥善保存；邮件清理不会删除旧备份，可以在“管理旧备份”逐个删除。备份不包含登录会话、原件、含密钥的 config.json 或邮箱凭证 email-secret.dpapi。请另外备份资料原件和本机配置。

恢复时先停止服务，将当前数据完整复制到独立备份位置，再恢复 ZIP 中的 `app.sqlite3`、`memory/`、`generated/`、`email/`，保持原件与配置匹配。不要把旧 SQLite 的 `-wal`、`-shm` 旁文件混入新快照；建议恢复到新建的独立数据目录，再用 `HIKARI_DATA` 指向它启动。首次扫描会检查原件现状。邮件提交中状态恢复为待核实，不自动重发；邮箱凭证在主机重新配置。

## 邮件回复

停服后在主机运行 `.\local\venv\Scripts\python.exe -m hikari email-setup`，隐藏输入学生邮箱客户端密码，以 Windows 当前用户保护保存，再启动 Hikari。不要把密码发送到聊天中。已有手机和电脑登录入口保持相同，新增独立“邮件”页面。

请先阅读 [邮件运行说明](EMAIL_RUNNING.md)，其中包括真实账户待验证事项、独立个人资料范围、逐封版本确认、发送结果不明的处理、清理和备份限制。本地模拟验收通过不代表真实邮箱已经接通，真实邮件始终由用户本人逐封确认发送。

## 验证

手机页面若出现无样式、登录区与主界面同时显示，通常是样式或脚本未成功加载。先确认主机服务和两端 Tailscale 在线，再重新打开网页。界面缓存会按页面实际引用的版本 URL 缓存样式和全部脚本依赖；离线打开时显示独立的连接提示，不展示未初始化的页面。网页缓存不保存聊天、资料接口或下载内容。

```powershell
.\scripts\test.ps1
node --test tests/web_cache.test.mjs
.\local\venv\Scripts\python.exe scripts/smoke_model.py --agent
.\local\venv\Scripts\python.exe scripts/benchmark_index.py
```

第一项使用模拟模型与合成文件，不发出真实模型请求。第二项明确调用真实模型，仅发送合成课程资料，会产生用量。第三项在 `local/test-scale/` 建立 100 份、1 万页合成资料，仅用于规模验证，不代表真实讲义效果。

`scripts/preview_demo.py` 在 8766 端口提供隔离的模拟模型演示，数据位于 `local/preview-data/`，不是正式服务。演示账户是测试数据，不能用于公网部署。

## PDF 原文阅读

PDF 资料和有效引用会在 Hikari 内置阅读器中打开，定位到 PDF 实际页码，支持上一页、下一页、输入页码、适合宽度和放大缩小。手机页面顶部提供返回对话和单独的下载入口。PDF.js、字体与字符映射均随项目本地提供，不使用外部在线文档阅读服务。失效的旧引用仍显示历史文字快照，不会把新版本 PDF 当成旧来源。

当前按单页渲染控制手机内存，尚未加入全文搜索、文字选择和批注；特殊编码、加密或损坏文件可能无法显示，可下载原件处理。首次打开及翻页需要连接主机，不承诺离线保存 PDF。

## 本机辅助目录

环境、缓存、测试和预览输出集中在 `local/`：Python 环境为 `local/venv/`，测试数据与缓存为 `local/test-data/`、`local/pytest-cache/`，npm 缓存为 `local/npm-cache/`。Git 元数据保留在根目录 `.git/`，不要移动或删除。`local/venv/` 是运行环境，不要把整个 local 目录当缓存删除。其余测试、演示和缓存子目录可在对应进程停止后删除。正式资料和运行数据仍位于 `data/`。
