# model-price

一个 [Agent Skill](https://agentskills.io/)：查询和对比主流大模型的官方介绍、主要用途、
主打能力、规格、服务方式与官方价格，并扫描各渠道的价格目录及官方发布来源，报告模型公布、
开放状态、上新下架、计费方式和价格变化。单模型查询先介绍模型能力，再对比各渠道；增量报告也会为真正
发生变化的模型先列用途与主打能力，便于判断新旧模型的定位变化。

面向人的说明在这个文件；面向 Agent 的执行规则在 [SKILL.md](SKILL.md)，
改代码用的项目结构与约束说明在 [AGENTS.md](AGENTS.md)。

仓库同时发布到两个地址：

- GitHub（`origin`）：https://github.com/moqimoqidea/model-price
- Gitee（`gitee`，中国大陆镜像）：https://gitee.com/moqimoqidea/model-price

## 支持的渠道

| 默认查询（国内） | 仅在明确提到时查询（海外） |
| --- | --- |
| 阿里云百炼、火山引擎方舟、腾讯云 TokenHub、百度智能云千帆、DeepSeek 原厂、月之暗面 Kimi、智谱 BigModel、MiniMax 原厂、小米 MiMo、快手可灵 | OpenAI、Anthropic、Google Gemini、xAI、OpenRouter、Google Cloud Vertex AI、AWS Bedrock、Microsoft Azure Foundry |

海外渠道又分两类：**模型原厂**（OpenAI、Anthropic、Google Gemini、xAI）按模型名推断，
**聚合与云渠道**（OpenRouter、Google Cloud Vertex AI、AWS Bedrock、Microsoft Azure Foundry）
转售别家的模型，模型名说明不了它由谁承载，所以只在 `--include-overseas` 或显式 `--provider`
时查询——AWS 一次扫描要读两份公开价目（约 25 MB），无人指名时不该发生。

AWS 与 Azure 按区域定价，一个模型能有三十到四十个区的报价，而其中多数的金额完全相同。
这两家默认只读**美国第一个区**（AWS `us-east-1`、Azure `us-east`），并在记录里写明读的是
哪个区——一条没有区域的云价格是给不出场景的价格。模型在默认区没有报价时，读它能读到的
第一个美国区；连美国区都没有，才读厂商唯一给出的那个区。Google Cloud Vertex 的分组方式
不同：它的页面按自己的区域组（Global、US Multi-Region、EU Multi-Region 与具体区域）给出
价格，这些组就按区域原样记录。

token 计费按「元 / 百万 tokens」（海外为「美元 / 百万 tokens」）对齐；图片按张、视频按秒、
语音按万字符或小时、音乐按首、3D 按次、模型按请求次数等**按厂商自己写的计费单位原样记录**，
不换算成 token 价——厂商按张卖就是一张多少钱，换算出来的数没人收。峰谷时段按各平台官方
原文分别记录，不跨平台套用。

**不是只有 token 计费的模型才进目录。** 图片、视频、语音、音乐、3D、向量这些模型一并收录，
它们和语言模型一样有介绍、有上下架、有价格变化；一个模型一格里印了几档价（火山
「单图生成场景：≤ 261 万像素 0.30 / > 261 万像素 0.60」），就按档位分别列成几条报价，
不会被合并成一个数。厂商按积分计费的，记的就是积分并保留厂商自己的换算说明，不替它折成钱。

## 目录结构

```
.
├── LICENSE             # Apache License 2.0
├── SKILL.md            # Agent 加载的指令
├── AGENTS.md           # 给维护代码的 Agent：结构、不变量、改动落点
├── im/                 # 各即时通讯渠道的投递契约
│   └── dingtalk.md     # 钉钉的 dws 版本、纯文本入口与发送前检查
├── scripts/
│   ├── query_model_prices.py   # CLI 入口
│   └── model_price/            # 实现，价格 providers 与 descriptions 分离
├── tests/                      # unittest 测试
├── references/                 # schema 与来源维护笔记
├── agents/openai.yaml          # 平台特定的接口元数据
├── cache/                      # 运行时状态，不入版本控制
└── snapshots/                  # 运行时状态，不入版本控制
```

## 安装

**仓库根目录就是 skill 目录**，所以 skill 的安装名由克隆到的目标目录名决定。
将仓库克隆到任意工作目录，再按所用 Agent 宿主的说明将该目录添加到 skill 搜索路径，
或在该搜索路径中创建指向此仓库的软链接。

```bash
# GitHub
git clone https://github.com/moqimoqidea/model-price.git model-price

# Gitee（中国大陆可优先使用）
git clone https://gitee.com/moqimoqidea/model-price.git model-price
```

> **不要用复制的方式安装。** 仓库自带的更新检查需要一个配好 upstream 的 Git 工作副本；
> 复制出来的目录没有 `.git`，每次刷新都会报 `check_failed`（见下）。软链可以：更新逻辑会
> 先把路径解析到真实目录，再定位仓库。

维护代码的工作副本应保留上述两个远程。每次提交后将同一分支分别推送到
`origin` 和 `gitee`，避免两个入口的内容不一致。

## 用法

在仓库（或已安装的 skill 目录）下执行：

```bash
python3 scripts/query_model_prices.py compare MODEL --format message   # 跨渠道对比
python3 scripts/query_model_prices.py compare MODEL --provider aliyun   # 限定渠道，可重复
python3 scripts/query_model_prices.py compare MODEL --exact             # 只认官方精确 id
python3 scripts/query_model_prices.py compare MODEL --include-overseas  # 含海外渠道
python3 scripts/query_model_prices.py provider PROVIDER MODEL           # 单渠道查询
python3 scripts/query_model_prices.py list PROVIDER --prefix PREFIX     # 列模型 id
python3 scripts/query_model_prices.py delta --format message            # 全量扫描并与上次对比
python3 scripts/query_model_prices.py delta --include-overseas          # 全部 18 渠道，含 Google 官方博客
python3 scripts/query_model_prices.py delta --since yesterday           # 与昨天最后一份基线对比
python3 scripts/query_model_prices.py delta --since yesterday-first --timezone Asia/Shanghai --include-overseas  # 北京时间昨天最早一份，含海外
python3 scripts/query_model_prices.py delta --since last-month          # 与上个月最后一份基线对比
python3 scripts/query_model_prices.py delta --since 2026-09-19T23:59:59+08:00
python3 scripts/query_model_prices.py compare MODEL --format message --max-chars 2000
```

`--format message` 得到一条交给即时通讯发送方的普通消息（`delta` 默认就是它），
`--format json` 得到同样的数据，给需要解析而不是阅读的一方。`--max-chars`
只在目标渠道的上限与钉钉不同时才需要改（见「输出」）。渠道 id 见
[SKILL.md](SKILL.md#model-price)。

**要发 IM 就用 `--format message`。** 用户说「发给我」「发到钉钉/微信/飞书」或任何
走即时通讯的要求，都该出 message 格式，不必再等他补一句开关。默认是 `json`，
因为 `compare` / `provider` 的常见消费方是程序；`delta` 默认 `message`，因为它的
常见消费方就是聊天窗口。

真正投递前还要读目标渠道自己的契约。发钉钉时以
[im/dingtalk.md](im/dingtalk.md) 为准；那里集中维护 dws 最低版本、唯一推荐入口、
dry-run 判断标准、收件人核对和字符上限，其他文档不再复制这些易漂移的细节。

## 典型示例

常见问法对应的用法——人怎么问、该跑什么、报告里读哪一节：

| 你会怎么说 | 跑什么 | 报告里得到什么 |
| --- | --- | --- |
| 详细介绍一下 deepseek-flash 模型。 | `compare deepseek-flash --format message` | 开头的「模型介绍」：用途、主打能力、规格、异常生命周期状态与来源 |
| 比较 deepseek-flash 的能力定位、服务方式和各平台官方价格。 | `compare deepseek-flash` | 模型能力介绍，以及各渠道的模型 id、服务方式、地域与每个计费方案的金额 |
| 分析这一次所有渠道的模型变更。 | `delta --format message` | 「模型能力」＋标准模型价格＋渠道结论 |
| 和昨天相比，有哪些模型发生了变化？ | `delta --since yesterday` | 每个渠道与昨天最后一份成功基线的差异 |
| 和北京时间昨天最早一次相比，含海外渠道有哪些变化？ | `delta --since yesterday-first --timezone Asia/Shanghai --include-overseas` | 每个渠道与北京时间昨天最早一份成功基线的差异 |
| 和上个月相比，有哪些模型发生了变化？ | `delta --since last-month` | 每个渠道与上个自然月最后一份成功基线的差异 |

第一种问的是模型本身，所以报告把介绍放在价格之前；第二种问的是跨渠道口径差异，
所以重点是逐条列出的模型版本、服务方式与计费方案；第三种问的是「这次变了什么」，
所以它读遍所有渠道、与上次基线对比，只介绍真正有变化的模型。后两种问法的
扫描范围由是否包含海外渠道决定，对比基线可以选昨天最早、昨天最后或上个月最后一份。
这些结果要发到钉钉时都加 `--format message`（`delta` 已默认）。

## 输出

消息先说模型能做什么，再说要花多少钱：抬头是标题、时间与主题，紧接着就是「模型介绍」，
然后是结论、渠道明细、差异总结，最后才是峰谷时段原文与各渠道来源。不知道模型能做什么的
人，判断不了一个价格值不值。

```
模型价格对比
时间：2026-09-17 23:53（UTC+8）。
主题：deepseek-flash 在各渠道的价格与服务方式。

【模型介绍】
1. 阿里云百炼｜DeepSeek-V4.1-Flash（deepseek-v4.1-flash）。
   用途：面向代码与智能体的高吞吐模型。
   主打能力：文本生成、函数调用。
   来源：……
【结论】
……
【渠道对比】
1. 阿里云百炼｜DeepSeek-V4.1-Flash。
   模型：deepseek-v4.1-flash。
   服务方式：平台托管。
   地域：中国区。
   计费方案 1：闲时。
      - 输入：1 元/百万 tokens。
【差异总结】
……
```

**一个价格不止一个数时，另一个数也印出来。** 厂商做活动时不会只给一个数：阿里云给刊例价和一个折扣率，百度给活动名、原价和活动时间，Google 在同一个格子里给「限时至 12 月 31 日」和「1 月 1 日起的新价」。这些都会跟在金额后面一起显示：

```
- 输入（Batch Chat)：6 元/百万 tokens（原价 12，5 折）。
- Input price：0.75 美元/百万 tokens 至 2026-12-31（原价 1.50）。
```

`原价` 是这个价所折减的那个价，`5 折` 是厂商自己印的折数（`0.5` 是五折，不是「0.5 折」），`至` 是当前价有效的最后一天。**三者都只在厂商写出来的地方读**：不会从两个数字反推折扣率，不会把厂商的原话当成日期，也不会拿别的渠道的口径套过来。

厂商把促销写在价格表旁边的文字里、没写进任何一列时，那句话会原样带到模型下面，价格仍是当下实际计费的那个：

```
1. OpenAI｜gpt-5.6-sol。
   价格说明：GPT-5.6 Sol’s promotional pricing is available at least through November 21, 2026.
   计费方案 1：standard；service_tier=standard；context_tier=short。
```

这么写是因为那句话说了字段说不出的东西——「至少到 11 月 21 日」不等于「11 月 21 日截止」，翻译成日期就是替厂商改了口径。只有句子里点名了某个模型，那句话才会挂到该模型上。

**两条长度上限，超了是总结，不是截断。** 默认扫描消息只展示标准价格；在这份
展示范围内，工具不会为了凑字数删掉条目或截断金额。一个渠道同时调整至少两个在架模型的
标准价格时，两栏都会按下面的规则合并为带官方链接的条目；这是默认展示方式，与超长处理无关。

- **模型介绍（summary）不超过 300 字。** 官方原文更长的照样完整留在记录里，只是打上
  `summary_needs_condensing` 标记，消息里那一行的标签会写成「用途（原文 N 字，超过 300 字
  上限，需先总结再发送）」。由真正发送的一方（Agent 或人）先把原文总结进 300 字再发。
- **整条消息不超过 `--max-chars`**（默认 3000 字符，给投递和最后编辑留出余量——渠道只
  会越来越多，而一条消息得始终是一条消息）。超了照样渲染完整报告，只在末尾补一行，写明超了
  多少字、发送前需总结压缩到哪里，并点名不许丢的东西：标题、渠道状态与已展示的全部金额。

触发时就是这个样子（照抄真实输出）：

```
   用途（原文 302 字，超过 300 字上限，需先总结再发送）：Qwen 新一代原生全模态模型，……
（中间是完整的报告，一段都没少）
本消息 4396 字，超过 3000 字上限 1396 字；发送前需总结压缩到 3000 字内，保留标题、渠道状态与已展示的全部金额
```

本工具不调用模型、也不带任何凭据，所以它只负责量出超限并把话说明白，「取重点写短」这一步
交给发送方。`--max-chars` 只在目标渠道的上限与钉钉不同时才需要改。

**delta 的正文依次是「模型能力」「模型价格」「渠道结论」**，抬头仍为「模型价格自动检测」。
模型能力按渠道组织，并标明实际变化：【新增上架】、【目录下架】、【价格调整】、
【计费模式新增】、【计费模式移除】、【替代模型更新】以及退役公告、日期、状态和时间节点变化。
同一模型同时发生几种变化就同时标明，不能因为它仍在架就写成新上架。
模式变化会列出模式名称和条件，公告变化会列出事实与该渠道的官方来源。
「模型价格」的目录报价只展示在架模型的标准方案；单模型的上下文档位及金额仍分别保留。
新上架但未公布价格的模型进入「新增模型」，标为「价格未知」。fast、flex、batch 等方案
的金额保留在 JSON 与历史基线中，不进入默认消息；模式增删仍会在模型能力中说明。
渠道结论列出全部渠道的状态与变化数量，只有公告或替代模型变化的渠道也算有变化；
公告读取失败不能归为完全无变化。结论
不重复打印渠道概览、定价来源列表或 Skill 更新检查。
活跃是新上架模型的默认事实，因此消息不重复输出“生命周期：在用”；预览、旧版、已下线
或官方未说明等会影响判断的状态仍会显示。

**多个模型调价时，两栏都合并成一条。** 同一渠道至少两个不同在架模型的标准价格发生变化，
「模型能力」和「模型价格」各写一条【价格调整】，列出完整模型 ID、模型数、标准价格变化项数，
以及该渠道的官方 HTTPS 目录页面；逐项金额和条件请读者到该页面查看。
这适用于 OpenRouter 等所有渠道，且不取决于消息是否超长。只有价格变化的模型不重复介绍；
同时发生模式增删、替代模型更新或公告变化的模型仍单独说明这些事实。
新上架模型、标准模式增删的价格以及单模型多档调价继续详细展示。
JSON 和历史基线仍保留完整金额与条件，不能为了简短修改原始数据。

**官方公布与 API 上架分开监控。** 模型可能先在博客、研究公告或能力文档公布，过一段时间
才进入价格目录。`delta` 现在独立读取这些来源；即使价格目录没有变化，新公布的模型也会
进入「模型能力」，带官方介绍、公布时间、开放原文和来源。首次启用发布监控时标为
【官方公布·首次收录】，不会把之前已经发布的模型说成今天刚发布；以后新增的记录标为
【新增公布】，开放对象、公告报价和同名目录条目的变化也分别报告。历史对比缺少公告基线时，
仍展示一次首次收录事实，同时明确说明缺少所选历史，避免升级时漏掉已发现模型。

报告区分价格目录状态与开发者、普通用户的开放状态。目录出现一个价格不代表所有人都能用；
未匹配到同名价格条目也不代表 API 关闭。只公布名称、没有可读取能力说明的模型仍会列出，
介绍明确标为未找到。公告所写的价格在「模型价格」中另列为「公告报价」，保留公布时的全部
适用条款，不混入当前目录的计费方案。未公布或未能解析的价格显示未知。

例如 [Gemini 4 Argon](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/)
已公布并向受信任的网络防御者逐步开放，开发者和普通用户仍待开放。公告给出的首发期输入/
输出价为每百万 tokens 2/10 美元，首发期后为 4/20 美元，两组分开保留；不会根据“缓存输入
优惠 95%”反算缓存价，也不会把“至少持续到某日”写成确切截止日。

发布监控覆盖各渠道已登记的官方来源，不等于搜索全网：新闻来源读取最近 90 天内的文章，
当前能力目录没有日期限制；已收录记录不会因滚动索引删掉旧链接而消失。第三方托管渠道
只接受该渠道自己的发布证据；OpenRouter 保持模型目录监控，不把原厂研究公告当成上架。
新闻正文、公开接口或目录不可读时会明确报告来源失败并保留历史。单模型查询及 `list`
仍以价格目录为入口，公告发现属于 `delta`。各渠道的核查发现与边界见
[发布来源核查](references/announcement-audit.md)，来源维护规则见
[source-notes.md](references/source-notes.md#model-announcements)。

**退役时间单独监控。** `delta` 每次重新读取各渠道可公开访问的官方下线公告或模型目录，
把公告新增、公告日期修订、停止新购（EOM）、自动切换和服务下线（EOS）到点列为变化。
公告与价格各有独立状态和历史；其中一个来源失败时，不会把另一个结果抹掉。
同名模型在不同托管渠道按各自公告处理。Google Gemini 的关停日期是“最早可能日期”，
到点仍需官方确认实际是否下线；官方表格中的灰色行则明确表示已下线。xAI 等渠道的旧 ID 可能自动转发并改按新模型计费，
因此不能把退役一概说成旧 ID 已不可用。智谱的模型页有零散的下线提示，MiniMax 的
Legacy Models 表只说明旧版状态；没有公布日期的条目不会被补造 EOS。已到期且明确的
官方 EOS 即使价格表仍保留也会停止展示该模型价格，未来计划不会被说成已停服。
公告事实和证据现在归入「模型能力」中的对应渠道、对应模型，不另设退役段落。
【目录下架】只表示从所监测的目录移除，不等同于 API 停服。各官方来源和适用范围见
[references/source-notes.md](references/source-notes.md)。

核对 13 家供应商当前官方下架证据，可运行
`python3 scripts/audit_model_retirements.py`；用 `--provider PROVIDER` 限定一家，
`--timezone Asia/Shanghai` 指定无时区日期的判断日。脚本不写缓存或历史，JSON 中逐条保留
模型原始 ID、官方来源 URL、日期、替换方式和截至检查时是否有确切下架证据。

**回溯对比。** 每次成功扫描都会按渠道留下带时间戳的历史，目录未变也照常归档。
`--since yesterday` 取昨天最后一份，`--since yesterday-first` 取昨天最早一份成功基线，
`--since last-month` 取上一个自然月最后一份。相对日期默认按运行机器的时区计算；
要求北京时间时加 `--timezone Asia/Shanghai`，同时固定报告的扫描时间为北京时间。
`YYYY-MM-DD` 日期（如 `2026-09-19`）取当天最后一份；ISO 日历时间戳取不晚于该时刻的最后一份，
适合在已知发布时间之前取基线。时间戳不带时区时，按本次扫描的时区解释。
某渠道没有匹配历史时会明确报 `baseline_not_found`，不会偷换成其他日期；但本次成功结果
仍会归档，供之后对比。

每个渠道最多保留 1000 份基线。裁剪前先为最近三个月的每一天保留当天最后一份，
再保留当天最早一份，最后用最新扫描填满剩余名额：既保住“昨天最早/最后”的回溯锚点，
也防止高频调度无上限增长。
旧版的 `snapshots/<provider>.json` 仍能直接参与对比，并在下一次成功扫描时自动迁入历史；
无法解析的旧基线会移入 `snapshots/rejected/`，不再每轮重复读取。

**消息正文不是 Markdown 文档。** 报告里一个 Markdown 记号都没有：层级靠编号与
缩进，段落之间空行分隔，每条来源 URL 都写在行尾，避免即时通讯客户端重新解释正文。
展示来源时必须使用以 `https://` 开头的完整地址，方便用户直接点开。
这只定义报告本身；具体发送通道、兼容版本和验证方式属于渠道契约。钉钉的唯一维护点是
[im/dingtalk.md](im/dingtalk.md)，发送方必须先读它，不能凭相似命令猜测等价行为。

**定时任务。** `delta` 读遍各渠道后如果全都没有变化，只输出标题与渠道结论
（如「9 个渠道共 312 个模型，全部无变化。」和渠道名称），不用每天重复目录。

模型介绍严格对应托管渠道与模型 ID：腾讯云模型用腾讯云说明，OpenRouter 模型用
OpenRouter 说明，同名模型在不同渠道分别保留介绍；不再优先或回退到模型原厂，
也不跨渠道合并介绍。某渠道没有可读取的独立介绍就明确说明缺失。
介绍与价格是相互独立的数据源：介绍优先读取该渠道官方 Markdown，其次读取公开结构化
接口，最后才解析官方 HTML。某个介绍页失效不会影响价格结果；报告会如实标记“未找到
官方独立介绍”或“介绍来源读取失败”。腾讯云 TokenHub 的详情需要登录，因此使用仓库内
的 `scripts/model_price/descriptions/data/tencent-models.json` 镜像，镜像未覆盖的模型同样
会明确标注，不会根据模型名臆测能力。

更新腾讯镜像时，先从已登录的模型广场导出卡片 JSON，再执行
`python3 scripts/update_tencent_model_mirror.py CAPTURE.json`。脚本会校验必填字段与
生命周期、合并同一模型的“自部署/原厂直供”重复卡片，并从公开模型目录补齐 API id
别名；无效或相互冲突的镜像不会被运行时读取。

## 网络读取

一次运行内，同一官方文档只下载一次，整份目录也只解析一次；`delta` 的“强制刷新”表示
绕过上一次运行的磁盘缓存，不表示按模型重复请求同一页面。GET/HEAD 遇到连接中断、超时、
HTTP 408/425/429 或临时 5xx 时最多尝试 3 次并指数退避；普通 4xx 立即失败，明确的防机器人
挑战页最多额外尝试一次。短 `Retry-After` 会被尊重，超过 30 秒则提前结束该渠道并报告；
每个主机每轮最多 20 次实际请求。POST 默认不重试，只有百炼、腾讯公开研究索引这种明确只读且幂等的查询
会主动开启重试。报告会区分超时、连接失败、4xx 拒绝和 5xx 服务端错误，不再输出空原因。

## 自更新

每次显式刷新（`--refresh`，以及天然刷新的 `delta`）之前，脚本会：

1. `git fetch` 配置的 upstream；
2. 用 `git diff <本地 HEAD>..<upstream> -- <本 skill 路径>` 判断这次远端变化**是否真的动了本 skill**；
3. 只有「确实动了本 skill」「能快进」「工作区干净」三条同时成立，才 `git merge --ff-only` 并用新代码重启。

任一条件不满足就继续用当前代码，并把结果写进报告里的 `skill_update`：
`updated` / `up_to_date` / `update_skipped` / `check_failed`，从不静默隐瞒。

## 运行时状态

| 目录 | 内容 | 是否入库 |
| --- | --- | --- |
| `cache/` | 各渠道的目录与价格查询响应缓存，有效期 3 小时 | 否 |
| `snapshots/` | 各渠道的带时间戳基线历史，供 `delta` 做上次或回溯对比 | 否 |

两者都是本地状态，但**目录本身必须存在**（脚本要往里写），所以各自带一个 `.gitignore`
只忽略内容、保留目录，而不是在根 `.gitignore` 里排掉整个目录。

## 开发

```bash
python3 -m unittest discover -s tests    # 全量测试
python3 scripts/query_model_prices.py --help
```

改动前请读 [references/source-notes.md](references/source-notes.md)（来源与解析维护要点）；
消费 JSON 输出时读 [references/schema.md](references/schema.md)。解析按内容识别，
不硬编码模型名白名单；新增渠道只需在 `providers/` 加一个模块并在
`providers/__init__.py` 登记。

## License

个人仓库，未特别说明的内容默认保留所有权利；引用的第三方资料以其原始来源许可为准。
