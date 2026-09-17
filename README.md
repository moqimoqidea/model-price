# model-price

一个 [Agent Skill](https://agentskills.io/)：查询和对比主流大模型的官方介绍、价格与模型清单，
并扫描各渠道的整份目录、报告自上次扫描以来的变化。单模型查询会说明模型主要用途、
主打能力与生命周期；增量报告会为真正发生变化的模型附上同样的介绍。

面向人的说明在这个文件；面向 Agent 的执行规则在 [SKILL.md](SKILL.md)，
改代码用的项目结构与约束说明在 [AGENTS.md](AGENTS.md)。

## 支持的渠道

| 默认查询（国内） | 仅在明确提到时查询（海外） |
| --- | --- |
| 阿里云百炼、火山引擎方舟、腾讯云 TokenHub、百度智能云千帆、DeepSeek 原厂、月之暗面 Kimi、智谱 BigModel、MiniMax 原厂、小米 MiMo | OpenAI、Anthropic、Google Gemini、xAI |

价格一律按「元 / 百万 tokens」（海外为「美元 / 百万 tokens」）对齐，峰谷时段按各平台官方
原文分别记录，不跨平台套用。

## 目录结构

```
.
├── SKILL.md            # Agent 加载的指令
├── AGENTS.md           # 给维护代码的 Agent：结构、不变量、改动落点（CLAUDE.md 指向它）
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

```bash
# 用户级：所有项目可用
git clone https://github.com/moqimoqidea/model-price.git ~/.workbuddy/skills/model-price

# 项目级：只在该项目可用
git clone https://github.com/moqimoqidea/model-price.git .workbuddy/skills/model-price

# 已有工作副本时，用软链代替再克隆一份
ln -s "/path/to/this/repo" .workbuddy/skills/model-price
```

> **不要用复制的方式安装。** 仓库自带的更新检查需要一个配好 upstream 的 Git 工作副本；
> 复制出来的目录没有 `.git`，每次刷新都会报 `check_failed`（见下）。软链可以：更新逻辑会
> 先把路径解析到真实目录，再定位仓库。

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
```

`--format message` 得到一条可直接转发到钉钉的普通消息（`delta` 默认就是它），
`--format json` 得到同样的数据，给需要解析而不是阅读的一方。渠道 id 见
[SKILL.md](SKILL.md#model-price)。

**要发 IM 就用 `--format message`。** 用户说「发给我」「发到钉钉/微信/飞书」或任何
走即时通讯的要求，都该出 message 格式，不必再等他补一句开关。默认是 `json`，
因为 `compare` / `provider` 的常见消费方是程序；`delta` 默认 `message`，因为它的
常见消费方就是聊天窗口。

## 输出

消息按「总—分—总」排布：抬头是标题、时间与主题，随后是结论、渠道明细、
差异总结，再往下才是峰谷时段原文、模型介绍与各渠道来源。

```
模型价格对比
时间：2026-09-17 23:53（UTC+8）
主题：deepseek-flash 在各渠道的价格与服务方式

【结论】
……
【渠道对比】
1. 阿里云百炼｜DeepSeek-V4.1-Flash
   模型：deepseek-v4.1-flash
   服务方式：平台托管
   地域：中国区
   计费方案 1：闲时
      - 输入：1 元/百万 tokens
【差异总结】
……
```

**为什么不发 Markdown 文档。** 钉钉会用自家的解析器读文档，本工具的报告层级密、
表格多，被它读回来容易串行——列错位、标题被吞、价格落到别的模型下面。所以
消息里一个 Markdown 记号都没有：层级靠编号与缩进，段落之间空行分隔，每条来源
URL 都写在行尾，避免被钉钉自动链接连带标点一起吃掉。

**投递必须走纯文本通道。** 钉钉在渲染一条 Markdown 消息前会先解析它，那个解析
把每个单换行都变成空格——三行抬头会挤成一行，以 URL 结尾的段落还会吞掉后面那个
换行（`…&_v=undefined` 直接粘上下一个渠道名）。所以发送时要显式指定文本类型：

```bash
dws chat +messages-send --identity user \
  --open-dingtalk-id <接收人 openDingTalkId> \
  --msg-type text --text "$(cat message.txt)" -y
```

`dws chat +dm` 看着等价，但它默认走 Markdown，不能用。纯文本消息单条上限
**5120 字符**（服务端按字符数拒绝，不是字节数），所以消息得控制篇幅：已经出现在
渠道明细里的来源 URL 不再在「来源检查」里重复，每条计费方案也只写自己的档位，
不再把同一个渠道的峰谷时段窗口在每一行上重抄一遍。

**定时任务。** `delta` 读遍各渠道后如果全都没有变化，只输出标题和一行结论
（如「9 个渠道共 312 个模型，全部无变化。」），让你知道任务确实跑过，而不用每天
重复一整份目录；有渠道变化、有渠道读不到、或首次运行还没有基线时，才输出完整消息。

模型介绍与价格是相互独立的数据源：介绍优先读取官方 Markdown，其次读取公开结构化
接口，最后才解析官方 HTML。某个介绍页失效不会影响价格结果；报告会如实标记“未找到
官方独立介绍”或“介绍来源读取失败”。腾讯云 TokenHub 的详情需要登录，因此使用仓库内
的 `scripts/model_price/descriptions/data/tencent-models.json` 镜像，镜像未覆盖的模型同样
会明确标注，不会根据模型名臆测能力。

更新腾讯镜像时，先从已登录的模型广场导出卡片 JSON，再执行
`python3 scripts/update_tencent_model_mirror.py CAPTURE.json`。脚本会校验必填字段与
生命周期、合并同一模型的“自部署/原厂直供”重复卡片，并从公开模型目录补齐 API id
别名；无效或相互冲突的镜像不会被运行时读取。

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
| `snapshots/` | 每个渠道上次扫描的基线，`delta` 与它对比 | 否 |

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
