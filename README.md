# Hikari

个人资料库与学习助手。Windows 主机运行单个 Agent，Android 和电脑通过网页聊天、查阅资料和维护 Markdown 记忆。

## 开始使用

在项目目录打开 PowerShell：

```powershell
.\scripts\start.ps1
```

首次启动会在主机终端要求创建账户和密码。然后打开 [本地学习空间](http://127.0.0.1:18765)，登录后在设置中检查模型连接，再上传课程资料。已有 `DEEPSEEK_API_KEY` 环境变量时会自动使用；网页保存的密钥优先。密钥不需要发到聊天中。

如果终端限制执行脚本，可直接运行：

```powershell
.\local\venv\Scripts\python.exe -m hikari setup
.\local\venv\Scripts\python.exe -m hikari
```

项目已包含本地网页资源，运行时不需要 Node。新环境依赖安装见 [运行说明](docs/RUNNING.md)。

手机与电脑已使用 Tailscale 时，先启动 Hikari，再运行一次 `scripts/tailscale.ps1` 配置持久的私网 HTTPS 入口。以后使用脚本输出的固定 `.ts.net` 地址，无需购买域名。

## 第一版能力

- 多聊天、全局串行任务队列、状态显示，断线后取回结果。
- PDF、PPTX、Markdown、DOCX 文字提取，自动更新中文全文索引。
- DeepSeek 自主生成检索词、多轮搜索阅读，最终回答提供版本核对后的来源。
- 三类 Markdown 记忆、用户编辑保护、修订与撤销。
- 手机和电脑布局、公式显示、来源预览、资料上传与学习空间备份。
- 独立邮件入口、IMAP 只读同步、单 Agent 分类拟稿、版本绑定确认发送与不确定结果核实；真实收信接入已验证，拟稿质量和发送仍待用户验收。

扫描页、图片与图中公式暂不识别。支持 Tailscale Serve 固定私网地址；真实课程质量与 Android 真机体验仍需相应验收。

- [第一版需求](docs/REQUIREMENTS.md)
- [邮件回复功能需求](docs/EMAIL_REQUIREMENTS.md)
- [邮件配置、使用与验收边界](docs/EMAIL_RUNNING.md)
- [手机与电脑邮件验收步骤](docs/EMAIL_ACCEPTANCE.md)
- [架构设计](docs/ARCHITECTURE.md)
- [长期记忆目标架构与分层实施顺序](docs/MEMORY_ARCHITECTURE.md)
- [技术核查与验收计划](docs/VALIDATION.md)
- [运行、外网连接与恢复](docs/RUNNING.md)

旧版 LEARNING.md 仅为设计参考，不继承 AstrBot、QQ、旧脚本、旧目录或旧部署假设。
