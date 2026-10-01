# 官方模型发布来源核查

核查日期：2026-10-01，Asia/Shanghai。核查对象为仓库登记的全部 18 个渠道，读取公开的
价格目录、发布索引、能力目录及相关公告正文，不使用账号或 API Key。下面的发现是此次
实际读取到的证据；没有发现某类公告，不等于厂商从未有过这种发布方式。

## Google：原监控确实漏掉 Argon

原来的发现入口是价格目录：先列出价格页中的模型，再为变更模型找介绍。介绍读取器即使能
读取一个模型，也不会主动把价格目录之外的模型加入扫描。因此，增加一个介绍 URL 并不能
解决发布监控漏报，必须有独立的模型发现来源。

这次 Google 价格读取返回 37 个模型，没有 Argon；
[官方文章](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/)
以及 [Gemini 博客 RSS](https://blog.google/innovation-and-ai/models-and-research/gemini-models/rss/)
已公布 Gemini 4 Argon。文章的正式发布时间是 `2026-09-30T20:00:00+00:00`，即北京时间
10 月 1 日 04:00，不能因扫描日期不同而把它说成另一日发布。

官方定位是面向真实软件开发、企业知识工作与网络防御的前沿模型。当前通过 Fairwind Program
向受信任的网络防御者逐步开放。文章说会在收集早期测试反馈、改进防护后，再尽快向开发者、
企业与普通用户开放；这部分仍是未来计划，不是所有人已经可以调用的 API。

公告已经写了价格，所以“还没进入价格目录”也不能说成“官方没有公布价格”：

| 公告中的适用范围 | 输入，美元/百万 tokens | 输出，美元/百万 tokens |
| --- | ---: | ---: |
| 首发期 | 2 | 10 |
| 首发期结束后 | 4 | 20 |

首发价原文还说缓存输入比输入 token 价优惠 95%，但没有独立给出缓存金额；工具保留这句话，
不反算金额。此次读到的文章没有首发期的确切结束日，因此不补造日期。两组报价都是公告
证据，需要与未来实际 API 目录的计费方案分开记录。

同一 Google 发布来源还说明了另一种边界：
[Gemini 3.8 Flash 与 Flash Cyber](https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/)
在同一文章内介绍，普通版可以面向订阅用户，Cyber 版限制在可信防御者范围。不能因名字有
相同前缀，把限制、能力或价格复制给另一版。
[Gemini Robotics ER 2](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-robotics-er-2/)
则同时有公开的开发者 API 与另一产品面的私有预览；私有预览不等于该模型整体未开放。

## 其他 17 个渠道

“同名条目未匹配”只说明此次目录与公告的字面匹配结果。研究名称、系列名称、区域版本、
不同 API 参数可能各有自己的名字，不能把这种结果直接翻译成没有服务或没有任何收费方式。

| 渠道 | 此次核查发现 | 本次接入与保留的边界 |
| --- | --- | --- |
| 阿里云百炼 | [Qwen-Drive-1.0](https://qwen.ai/blog?id=qwen-drive-1.0) 是自动驾驶视觉语言研究模型，百炼此次目录无同名项；Qwen3.8-Flash-Next 等也有独立的研究/权重发布。 | 读取当前 Qwen 公开文章 API 的正文、路径和日期；研究成果、权重与百炼托管调用分开。旧 `qwenlm.github.io/blog/` 已提示迁移，不能当成最新源。 |
| 火山引擎方舟 | [Seed 官方模型成果索引](https://seed.bytedance.com/en/blog) 单独列出 Seed2.1、SeedRealtime、Seed Audio、Seed GR-RL 等成果；方舟目录使用 `doubao-...` 等具体版本，不能按系列强行合并。 | 独立读取官方结构化成果索引。该索引只给名称与链接的条目会显示介绍未找到，不能合成能力；不推断方舟 API ID 或上架状态。 |
| 腾讯云 TokenHub | [混元公开研究](https://hunyuan.tencent.com/) 有独立的 Hy3 发布文章，公开接口提供正文和发布时间；这条通路不需要进入 TokenHub 控制台。 | 接入匿名只读、分页的研究 API；保持 TokenHub 卡片介绍镜像的原有授权边界。研究公告不证明所有 TokenHub 托管条目已经开放。 |
| 百度智能云千帆 | [ERNIE-5.1-Preview 的 LMArena 公告](https://ernie.baidu.com/blog/posts/ernie-5.1-preview-0430-release-on-lmarena/) 证明研究/榜单预览有独立发布来源，不能只看千帆价格。 | 接入 ERNIE RSS；本次 feed 最新条目为 2026-05-09，90 天窗口内无新增。该旧 feed 不足以证明千帆近期完全没有发布，价格目录仍独立读取。 |
| DeepSeek 原厂 | [官方更新日志](https://api-docs.deepseek.com/zh-cn/updates/) 保留实验版、升级、路由名称与退役等事实，并非每条发布都等于当前新 API ID。 | 重用现有日志读取器；发布、介绍、更新时间及退役消费者共享同一次文档读取，不按旧名称推断当前价格。 |
| Kimi | [Kimi K3](https://www.kimi.com/en/blog/kimi-k3) 与 K2 系列有官方博客介绍；K3 API 已可用，而权重与完整技术报告可以随后发布。 | 接入官方发布文章。未来的权重/报告不被误判为 API 尚未开放；保留公告名称，不把旧研究系列强行映射到当前价格 ID。 |
| 智谱 BigModel | [模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview.md) 列出 AutoGLM-Phone、GLM-Realtime 等；能力目录、旧版目录与价格目录不完全相同。 | 重用完整能力表读取器。无同名价格不代表关闭，旧版条目的退役判断仍由独立通知负责；概览不被宣称为完整研究新闻索引。 |
| MiniMax 原厂 | [官方新闻](https://www.minimax.io/news) 有 M3、H3、Music 等独立发布，模型权重开放与 API 开放时间可以不同。 | 接入官方新闻卡片与正文。Music 等研究名称不能无证据映射到 `music-...` 计费版本。 |
| 小米 MiMo | [模型概览](https://mimo.mi.com/docs/zh-CN/quick-start/summary/model) 有 `mimo-v2.5-tts`、`mimo-v2.5-tts-voiceclone`、`mimo-v2.5-tts-voicedesign`；此次价格页的 6 个条目不包含这三项。 | 确认存在“文档已写、价格页未收录”的缺口。独立发现这三项、展示官方能力；访问对象在文档未明确时保持未知。 |
| 快手可灵 | [视频能力图](https://klingai.com/document-api/guides/capability-map/video.md) 与 [图片能力图](https://klingai.com/document-api/guides/capability-map/image.md) 还列出扩图、音效、主体控制等能力，价格页可能按套餐/服务名字计费。 | 接入两份能力图并复用介绍解析；模型与正式命名能力原样保留，不把未匹配到独立价格等同于免费。 |
| OpenAI | [官方新闻 RSS](https://openai.com/news/rss.xml) 可以独立于开发者价格发现模型；本次索引可读，部分文章正文返回 HTTP 403 挑战页。 | 保留已核实的索引发现，明确报告部分来源失败并不写公告基线；不把客户故事、金融服务产品或漏洞奖励计划中的模型提及当成新模型。 |
| Anthropic | [Fable 与 Mythos 5.1](https://www.anthropic.com/claude-fable-and-mythos-5-1) 都已在价格目录，但 Fable 普遍可用，Mythos 仍只面向核验过的网络防御者及生命科学研究者。 | 证明“有价格 ≠ 普通用户可用”。接入官网新闻与同一文章内分模型的开放证据；Opus 的强化研究防护计划或早期客户测试不能把其基础模型误标为受限。 |
| xAI | [官方新闻](https://x.ai/news) 有 Grok 语言与语音模型发布；正式品牌名称与开发者价格 ID 未必逐字相同。 | 读取近期官方文章，先用新闻卡片日期排除旧条目；不猜 API ID，也不按某种语音能力名称推断全部语音模型。 |
| OpenRouter | [模型 API](https://openrouter.ai/api/v1/models?output_modalities=all) 已包含免费、无独立报价及动态路由条目，原扫描已能保留它们。 | 明确维持 `catalogue_only`。原厂只在研究博客公布一个模型，不能说成 OpenRouter 已托管该模型。 |
| Google Cloud Vertex AI | [官方发布 Atom](https://cloud.google.com/static/feeds/generative-ai-on-vertex-ai-release-notes.xml) 是独立的托管发布证据；此次两个官方 feed 地址均最新到 2026-05-26。 | 接入该 feed，保留其覆盖限制。当前价格页已使用 Gemini Enterprise Agent Platform 产品路径，旧 feed 的沉默不能证明新产品没有发布；Google 原厂博客也不能证明 Vertex 上架。 |
| AWS Bedrock | [AWS What's New](https://aws.amazon.com/about-aws/whats-new/recent/feed/) 有 Bedrock 自己的模型引入公告，托管时间可不同于原厂。 | 按 Bedrock 上下文筛选模型发布；原厂公告不继承为 AWS 上架，区域价格与公告证据保持独立。 |
| Microsoft Azure Foundry | [GPT-6 Astra 的 Azure 公告](https://azure.microsoft.com/en-us/blog/gpt-6-astra-frontier-intelligence-for-work-now-generally-available-in-microsoft-foundry/) 的正文写 Limited Access Program，即使标题用了 generally available。 | 接入 Azure 自己的新闻 RSS，按正文的实际开放范围报告；WordPress 的重复标题尾注不覆盖正文限制，原厂开放也不代表 Azure 同时开放。 |

## 代码行为与验证

实现放在独立的 `announcements` 子系统：公开来源负责发现和读取，解析层负责模型主体、
证据与价格条款，归档层负责保留、对比和精确字面关联。`delta` 汇合这部分与价格、退役
通知的变化；共同措辞仍在 `reporting`，消息布局仍在 `messages`，字符预算仍只计数。

价格读取成功时照常归档；公告读取成功时另存 `snapshots/announcements-<provider>/`。
某部分失败不改写另外部分的结果。公告初次读取使用【官方公布·首次收录】，后续新增记录
使用【新增公布】，开放状态、公告报价和同名价格目录关联的变化各有自己的原因。博客
滚动删除旧链接不会撤回已经收录的模型；更旧文章不会覆盖更新的开放事实。
按昨天等历史日期运行时，首次接入的来源可能尚无所选历史基线。此时保持历史差异为空，
仍把第一次发现的记录作为【官方公布·首次收录】展示，并说明历史基线缺失；不会因升级时
缺少历史而把 Argon 收进基线却从消息中漏掉，也不会在下一次读取时重复首次收录。

离线测试验证 Argon 的受限开放与两组报价、同文不同模型/不同开放面、名称前缀隔离、
API/权重/训练数据区别、公开 JSON 与 RSS/Atom 结构、分页、历史基线、失败保留、目录
下架优先级及超长完整输出。真实 Google 读取使用隔离的临时历史验证，日常价格及退役
历史没有因核查被改写。

本次仍有明确的范围边界：新闻发现只读登记索引中最近 90 天的发布，未注明日期的当前索引
条目也会读取；当前能力目录没有日期限制，已收录历史长期保留。它不是完整的互联网爬虫，
不会自动发现未在这些索引出现的论文、社交帖子、未登记品牌或任意研究代号。旧新闻源与
不可访问正文已在上表注明。`compare`、`provider`、`list` 继续以价格目录为发现入口，
独立发布发现用于 `delta`。默认扫描仍是国内；全部 18 渠道必须包含 `--include-overseas`。
