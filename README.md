# model-price

一个 [Agent Skill](https://agentskills.io/)：查询和对比主流大模型的官方价格与模型清单，
并扫描各渠道的整份目录、报告自上次扫描以来的变化。

面向人的说明在这个文件；面向 Agent 的执行规则在 [SKILL.md](SKILL.md)。

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
├── scripts/
│   ├── query_model_prices.py   # CLI 入口
│   └── model_price/            # 实现，按职责分模块
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
python3 scripts/query_model_prices.py compare MODEL --format markdown   # 跨渠道对比
python3 scripts/query_model_prices.py compare MODEL --provider aliyun   # 限定渠道，可重复
python3 scripts/query_model_prices.py compare MODEL --exact             # 只认官方精确 id
python3 scripts/query_model_prices.py compare MODEL --include-overseas  # 含海外渠道
python3 scripts/query_model_prices.py provider PROVIDER MODEL           # 单渠道查询
python3 scripts/query_model_prices.py list PROVIDER --prefix PREFIX     # 列模型 id
python3 scripts/query_model_prices.py delta --format markdown           # 全量扫描并与上次对比
```

默认输出 JSON，加 `--format markdown` 得到可直接转达的报告。渠道 id 见
[SKILL.md](SKILL.md#model-price)。

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
