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

## Mistral 接入核查（2026-10-07）

[Mistral 官方新闻](https://mistral.ai/news/mistral-large-4/)于 2026-10-06 发布 Large 4。
正文分别说明公开预览 API 可在 Studio 尝试、权重将在月底发布，以及限定对象的红队测试；
公开 API 的状态不因后两项而变成未开放或受限。发布日期读取文章自身的结构化时间。
网站通用 SEO 文案不充当模型介绍；官方发布段落和模型文档的介绍各保留自己的来源。

[模型目录](https://docs.mistral.ai/models)公布 API ID 与别名，独立于
[价格页](https://docs.mistral.ai/inference/pricing)的模型展示名称。本次真实扫描收录
37 个 API 标识；目录中未报价的条目仍然保留，权重研究条目不生成 API ID。
Large 4 的标准输入、缓存输入、输出当前分别为 $0.68、$0.07、$2.09 / 百万 tokens，
原价分别为 $1.36、$0.14、$4.18。标准、Batch、Priority 与区域推理保留各自的报价，
OCR、转录、TTS 保留按千页、分钟、百万字符的单位。新闻里的报价证据不替代当前价目。

[生命周期政策](https://docs.mistral.ai/inference/model-lifecycle)规定退役 ID 的调用返回
404；具体的弃用、退役日期和替代建议由官方模型数据逐项公布，按退役表 API 列中的
字面标识限定通知，不把日期扩散到未在退役表中列出的别名。
不把弃用当成 EOM，不把替代建议当成自动跳转，不把旧代日期转给当前仍使用的别名。
新闻、价格与退役历史分别归档；一处失败保留其他可读证据和既有成功基线。

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

## 2026-10-01 网络复核

OpenAI 同一篇官方文章在旧请求 UA 下返回 403 挑战页，改用指定的 Chrome 154/macOS
浏览器身份后可读。RSS 文章链接补齐官网规范地址末尾的斜杠后，本次新闻读取共 10 次请求，
成功得到 10 条模型记录，无正文读取错误；不换行连字符造成的名称核验失败也已修复。

腾讯原始目录和价格网页本次均可读取，所以旧报错本身不能证明页面结构永久变化或
用户网络被拦截。官网前端的匿名文档接口返回了与内嵌数据一致的目录正文；改读该接口
后，完整读取 130 个目录模型和 17 条退役事件，无需登录。详情介绍仍使用原有授权镜像。

全部 18 个渠道的价格目录使用最终请求头完成现场读取。Google 首次匿名访问设置会话标记
后发起 OAuth 跳转；保留本轮匿名标记、停止登录跳转，以相同 UA 重读公开页面后，价格和
退役来源均可读取。没有访问登录接口。核查使用临时输出，
没有推进用户已有的价格、公告或退役历史。网络策略及来源契约见
[source-notes.md](source-notes.md)。

## 2026-10-01 蚂蚁渠道补充

新增国内渠道 `ant-ling`（蚂蚁大模型），当前登记渠道数为 19。通过最终公共请求客户端
现场读取了官方价格及下架页面，共 2 次请求，请求头均匹配指定的 Chrome 154/macOS
身份。解析得到 5 个自营人民币定价模型、4 条下架记录；优惠价与删除线原价分别保留，
同页的 OpenRouter、ZenMux 报价归属各自平台。时间节点保留 UTC+8 和秒级精度。

本次接入只登记官方价格和下架来源，独立发布覆盖为 `catalogue_only`，模型介绍为
`not_found`，不把价目读取成功当作独立公告覆盖。验证使用临时基线，既有历史未推进。
上述 18 渠道的核查结果仍描述此前那次核查范围。

## 2026-10-13 发布来源修复与博客复核

此次核查覆盖当前全部 20 个渠道。使用项目的匿名 HTTP 客户端读取官方公开来源，
保留 Chrome/macOS UA、浏览器提示、语言、来源页和匿名会话处理；反爬候选页另试
导航请求头。小米和 Mistral 的早期连接失败在重试后恢复，最终结果以下表为准。
仍被验证页拦截的可选博客按用户要求不登记，不把验证页当成正常正文。

Google Cloud 的旧 `cloud.google.com/static/feeds/` 地址已返回 404。只换成
`docs.cloud.google.com` 的旧 Vertex feed 仍只能读到旧内容，不能解决发布遗漏。
已改读 [Gemini Enterprise Agent Platform 发布记录](https://docs.cloud.google.com/gemini-enterprise-agent-platform/release-notes)
及其[当前 Atom](https://docs.cloud.google.com/feeds/gemini-enterprise-agent-platform-release-notes.xml)。
同一天的记录按 Feature、Announcement、Change 等类别分开读取，模型发布不混入修复、
退役或功能教程的证据。部分 feed 链接额外带了 `/docs/`，证据链接使用已核验的现行页面
加原始日期锚点。此次读取获得 16 个不同的发布名称，来源无错误。

[GLM-Legal](https://z.ai/blog/glm-legal) 此前遗漏有两层原因：原来只读 BigModel
能力目录，而该测试模型在独立文章发布；文章的 HTML 又是空壳，正文编译在其链接的
同域模块里。现在同时读取能力目录、[Z.ai 模型更新](https://docs.z.ai/release-notes/new-released.md)
及已核验文章，并从模块读取字面的 MDX 段落和文章自身的日期 `2026-10-12`。
“Apply for API access”保留为受限申请证据，不生成 BigModel API ID、免费报价或普遍开放。
名称规则也支持 GLM-Legal、GLM-Image、GLM-OCR 这类没有版本数字的字面名称。

Z.ai 的 `/blog`、博客 sitemap 和 feed 均未提供可读总索引。因此这里仍是明确的有限覆盖：
模型更新和能力目录之外的其他独立文章，需要登记已核验的官方 URL；此次修复不能声称
已自动覆盖所有未被索引的 Z.ai 文章。没有把 GLM-Legal 的名称写进模型识别器。

| 渠道 | 此次核验的官方发布入口 | 结果与覆盖范围 |
| --- | --- | --- |
| 阿里云百炼 | [Qwen 博客](https://qwen.ai/)及其匿名文章 API | 可读，继续读取字面标题、完整正文与官方日期。研究发布不证明百炼托管。 |
| 火山引擎方舟 | [Seed 博客](https://seed.bytedance.com/en/blog)及匿名分页 API | 可读，新增近期博客与现有成果目录的组合读取；实际 API 会限制每页条数，必须跟随分页令牌。包括 SeedRealtime、Seedance 2.5、Seed Audio 1.0；名称由官方卡片标签限定。 |
| 腾讯 TokenHub | [Hunyuan 研究索引](https://hunyuan.tencent.com/)及匿名 `publicList` | 可读，本次近期窗口内没有可识别发布。索引沉默不证明没有研究进展；研究正文不替代 TokenHub 的授权介绍镜像。 |
| 百度千帆 | [ERNIE 博客 RSS](https://ernie.baidu.com/blog/index.xml) | 可读，但 feed 最新条目仍为 2026-05-09，近期窗口无记录。旧订阅的覆盖限制仍保留。 |
| DeepSeek | [官方更新日志](https://api-docs.deepseek.com/zh-cn/updates/) | 可读，继续与日期、介绍和退役消费者复用同一次文档读取。 |
| Kimi | 用户指定的[官方博客](https://www.kimi.ai/blog/) | 可读，现已作为登记入口。Next.js `articleList.items` 提供标题、地址和日期，先排除旧文章及外部论文；本次确认 Kimi K3。 |
| 智谱 BigModel | [能力目录](https://docs.bigmodel.cn/cn/guide/start/model-overview.md)、[Z.ai 更新](https://docs.z.ai/release-notes/new-released.md)、[GLM-Legal](https://z.ai/blog/glm-legal) | 三处均可读。测试申请模型与当前价格目录分别保留；总博客索引的缺口见上文。 |
| MiniMax | 用户指定的[官方新闻](https://www.minimax.io/news) | 已是登记入口，本次可读。当前服务端响应主要提供模型导航并带新闻页跳转标记；沿已公布的第一方链接确认 M3、M2.7、M2.5、H3、Music 3.0。未标日期的页面只证明首次发现。 |
| 小米 MiMo | [研究博客](https://mimo.xiaomi.com/)、[能力目录](https://mimo.mi.com/docs/zh-CN/quick-start/summary/model)、[模型更新](https://mimo.mi.com/docs/en-US/updates/model) | 用户修复访问后，三处均可读。新增博客读取，目录包含 More 中折叠卡片；博客同域请求 12 次，CDN 数据 3 次，低于默认单域名额度。 |
| 快手可灵 | [视频、图片能力图](https://klingai.com/document-api/guides/capability-map/video.md) | 两张能力图可读。博客索引虽然服务端渲染，但 `/blog/<slug>` 正文页一律是客户端渲染外壳：无标题、可见文本为零，索引到的模型名在 HTML 中根本不存在，正文佐证因此永远无法成立，该入口自登记起每次都报读取失败。已移除该发布入口，可灵改为只读两张能力图。 |
| 蚂蚁大模型 | [官方博客](https://developer.ant-ling.com/en/blogs/) | 可读，新增 Next.js 发布卡片读取，本次识别 Ling-3.0-flash。客座部署指南和比较对象不成为发布主体。 |
| OpenAI | [新闻 RSS](https://openai.com/news/rss.xml)及规范文章地址 | 可读，继续用官网 UA 与末尾斜杠读取正文；新增无数字的字面名称识别同样作用于官方研究发布。 |
| Anthropic | [官方新闻](https://www.anthropic.com/news) | 可读，同文的 Fable、Mythos 等模型保留各自开放证据。 |
| Google Gemini | [模型研究博客](https://blog.google/innovation-and-ai/models-and-research/gemini-models/)及 RSS | 可读，保持原厂发布与 Google Cloud 托管发布的渠道边界。 |
| xAI | [官方新闻](https://x.ai/news) | 可读，沿近期模型发布卡片读取正文。 |
| Mistral AI | [官方新闻](https://mistral.ai/news/)与公开模型数据 | 重试恢复，新闻和文档均可读；本次确认 Mistral Large 4。无数字的发布主体仍由官方模型目录限定。 |
| OpenRouter | [模型 API](https://openrouter.ai/api/v1/models?output_modalities=all)；候选[博客](https://openrouter.ai/blog/) | 博客在浏览器 UA、提示及导航头下仍返回 HTTP 200 验证页，已排除；维持 `catalogue_only`。原厂博客不能证明 OpenRouter 托管。 |
| Google Cloud | [现行发布 Atom](https://docs.cloud.google.com/feeds/gemini-enterprise-agent-platform-release-notes.xml) | 可读，已替换失效且过时的 Vertex 来源，模型发布与同日其他类别分开。 |
| AWS Bedrock | [AWS What's New](https://aws.amazon.com/about-aws/whats-new/recent/feed/) | 可读，只收录 Bedrock 自己的模型引入证据。 |
| Azure Foundry | [Azure 博客 RSS](https://azure.microsoft.com/en-us/blog/feed/) | 可读，修复只读摘要的问题：完整 `content:encoded` 才包含实际访问限制。GPT-6 Astra、Sol、Luna 等明确并列名称可展开，正文范围不被标题覆盖。 |

小米博客的卡片不是普通链接。读取器从首页明确链接的 Rspress 路由和部署清单定位
`en/index.mdx` 的当前内容块，限定到官网指定的 Xiaomi CDN 前缀，读取字面的
`title/link/desc` 及该文章路由的 `frontmatter.date`，不执行 JavaScript。
新文章的 iframe 正文只跟随一个明确的同域地址；没有日期时保持未知，系列标题也不会
展开成猜测型号。官网旧 `/index.html` 地址会经 `/index` 再到目录页面，两次跳转容易耗尽
单域名额度；已使用现场核验的规范路径，保留原文章内容与来源。

一个渠道现在可以组合博客、模型更新和能力目录，所有登记读取器独立运行。
其中一处失败仍会展示其他可读记录，但公告状态为 `source_error`，不推进公告历史。
多来源入口与覆盖范围作为可选来源信息保留；公告历史继续兼容版本 1，价格快照保持
版本 4，解析缓存版本升为 34。去掉了原先只能选择一种来源的分支及蚂蚁旧的
`catalogue_only` 声明，未修改价格、消息长度或退役语义。

681 项离线测试全部通过，另通过 CLI 帮助检查和差异空白检查。新增回归覆盖发布类别隔离、
正文限权、博客分页、模型主体与教程区别、无数字名称、同文兄弟型号、模块字面解析、
同域 iframe、部署变化、文章日期归属及部分失败后的历史保留。
现场完整扫描验证均使用临时缓存和基线，没有推进用户已有历史：
智谱读取 67 条价格记录且价格不变，新增公布中包含 GLM-Legal；Google Cloud 读取
127 条价格记录且价格不变，收录 16 个公告名称；小米读取 6 条价格记录且价格不变，
公告历史保留 15 个规范化名称。发布、访问、目录和价格仍分别报告。
