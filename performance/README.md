# 多渠道模型性能测试

这是独立、按需付费的推理测试目录。价格查询、公开目录扫描仍不读任何密钥。
先对每个目标发一条 `Hello`（最多 16 输出 tokens）预检，再比较 TTFT 和输出 TPS。
每次 `run` 都重新预检，失败渠道不发送长输入；不自动重试付费请求。

## 先小测，再扩大矩阵

从仓库根目录执行，仅用 Python 标准库，无需安装依赖：

```bash
# 只看计划，不发请求；配置示例自带四个目标。
python3 performance/benchmark.py plan

# 仅发 Hello。密钥缺失、网络、配额、模型或参数错误分别保留，不会被写成性能结果。
python3 performance/benchmark.py check \
  --target deepseek --target aliyun --target tencent --target volcengine

# 最小真实测试：每个有权访问的渠道，约 1K 输入、64 输出上限、1 次冷请求。
python3 performance/benchmark.py run \
  --target deepseek --target aliyun --target volcengine \
  --input-tokens 1K --max-output-tokens 64 --repeats 1 --modes cold

# 日常低成本对比：10K 输入，冷/热各 3 次；热模式额外预热 1 次。
python3 performance/benchmark.py run --input-tokens 10K --repeats 3

# 查看完整矩阵的请求数和 token 预算，仍不收费。
python3 performance/benchmark.py plan \
  --input-tokens 10K,50K,500K,800K --repeats 3 --max-output-tokens 256

# 确认额度充足后才执行；显式扩大估算预算，不把完整矩阵作为默认。
python3 performance/benchmark.py run \
  --input-tokens 10K,50K,500K,800K --repeats 3 \
  --max-total-tokens 40M --timeout 600

# 复用已保存结果，不联网、不重跑。
python3 performance/benchmark.py report --input cache/performance/RUN.json
```

请保证各账户额度充足。`--max-output-tokens` 是输出上限，不保证模型生成到该长度；
默认 256。长输出专项可设 `98304`（96×1024）或 `131072`（128×1024），
同时提高 `--timeout` 和估算预算。`96K` 表示十进制 96000，`128K` 表示 128000。
3～5 次足以做初步比较；单次只适合连通和烟测，不能得出稳定排名。

四渠道、四档输入、冷/热各 3 次会产生 112 次长输入请求（含 16 次预热），
约 3808 万输入 tokens，另有 4 次 Hello 和实际输出。不要把“3 次”误认为全矩阵只请求 3 次。
默认估算预算 50 万 tokens，会阻止直接执行这一大矩阵。
预算按总输入和输出计数，不因缓存打折；usage 不可得时按该次计划额度保守计入。
估算不能替代账单上限，单个在途请求、tokenizer 偏差及缓存存储费可能造成超出。

## 环境变量和厂家差异

| 目标 | 必需环境变量 | 默认 Chat 协议思考参数 | 当前验证范围 |
| --- | --- | --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY` | `thinking.type=enabled/disabled` | 官方文档、离线流式测试；待实网验证 |
| 阿里百炼 | `DASHSCOPE_WORKSPACE_ID`、`DASHSCOPE_API_KEY` | `enable_thinking=true/false` | 官方文档、工作空间与离线测试；待实网验证 |
| 腾讯 TokenHub | `TENCENT_API_KEY` | `thinking.type=enabled/disabled` | 官方文档、离线测试；待实网验证 |
| 火山方舟 | `ARK_API_KEY` | `thinking.type=enabled/disabled` | 官方文档、离线测试；待实网验证 |

百炼请求地址是
`https://${DASHSCOPE_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions`。
不会在工作空间缺失时退回旧地址。不同地域请在本地环境设置完整的 `ALIYUN_BENCHMARK_URL`。
`ALIYUN_BENCHMARK_MODEL`、`ARK_BENCHMARK_MODEL` 可覆写对应 API ID；方舟的接入点 ID 也可使用。
所有配置中的 `api_key_env` 都可改成用户已有的环境变量名称，不复制密钥。
脚本继承当前进程环境，不自动读取 `.env`、登录 shell 文件、浏览器或 Keychain。

原始 HTTP 的百炼 `enable_thinking` 直接位于 JSON 顶层；Python OpenAI SDK 的 `extra_body`
是 SDK 包装参数，不能作为原始 JSON 的一层发出去。`providers.py` 根据厂家与协议翻译
`thinking_mode`，没有核验的组合直接拒绝自动翻译，不把一家参数套到其他渠道。
示例默认关闭思考，使用 `--thinking on` 可对这四家 Chat API 开启思考。
`--thinking provider-default` 不传思考控制；默认行为可能不同，不能据此公平比较。
需设置厂商特有 effort/budget 时使用 `thinking_mode: custom`，在 `params` 中按该模型文档填写。

同名不保证同版本。示例保留真实调用 ID、region 和待核验的 revision；正式比较前核对控制台
服务版本、原厂直供或云托管、量化/推理方式及额度档位。返回的 `response_model` 单独记录与分组。
1M 上下文与输出上限也要按渠道/具体模型核实，配置值是发送前的检查声明，不是动态能力探测。

## 协议与扩展

`--config` 接受一个只含 `targets` 数组的 JSON。先复制 `targets.example.json` 到忽略目录
（如 `private/benchmark-targets.json`），每个目标独立填写模型 ID、完整端点、环境变量名、
区域、上下文/输出限制、缓存策略及思考参数。`--target` 可重复，精确选择配置中的 id。

| `protocol` | 完整端点形状 | 鉴权 | 输出上限字段 |
| --- | --- | --- | --- |
| `chat` | `https://HOST/.../chat/completions` | Bearer | `max_tokens`；可设 `output_limit_field: max_completion_tokens` |
| `responses` | `https://HOST/.../responses` | Bearer | `max_output_tokens`，`store=false` |
| `anthropic` | `https://HOST/.../messages` | `x-api-key`，版本 `2023-06-01` | `max_tokens` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent` | `x-goog-api-key` | `generationConfig.maxOutputTokens` |

四种协议有离线 SSE fixtures；这不等同于所有提供相似 API 的厂家都已实测。
DeepSeek Responses 已有 `thinking_mode` 映射；其他非 Chat 组合使用 `custom` 原始参数，
按官方文档和一次 Hello 验证后再测试。Gemini 原始参数放在 `params.generationConfig`；
Anthropic 原始思考参数放在 `params.thinking`，effort 放在模型支持的 `params.output_config`。
密钥不能出现在配置、请求 URL、参数或额外 headers 中；HTTPS 重定向被拒绝。

现有价格系统的 20 个渠道不是已验证的推理渠道清单。本轮仅提供上述四家的文档适配配置；
其余渠道等核对模型、端点、鉴权、思考、流式 usage 和缓存后再加目标。
OpenAI、Anthropic、Gemini 等直接 key 协议有解析基础，但尚无真实请求报告；
AWS Bedrock SigV4、Vertex OAuth/ADC、Azure 的特定鉴权与 API version 尚未实现，
不可套用一个 Bearer 模板声称支持。音视频生成的完成时延也不等同于文本 token TPS。
不把这些未验证能力登记为可用目标。

## 测量和缓存口径

| 字段 | 口径 |
| --- | --- |
| `ttft_seconds` | 发送前到首个非空正文文本事件的客户端耗时；忽略 role、ping、usage 等元数据 |
| `first_content_seconds` | 发送前到首个正文或推理文本的耗时 |
| `first_reasoning_seconds` | 发送前到首个可观察推理文本的耗时；不可见则未知 |
| `headers_seconds` | 收到响应头的耗时，含连接/TLS；不是正文 TTFT |
| `output_tps` | `(服务端 output_tokens−1)/(最后内容事件−首个内容事件)`，仅在输出时间范围可观察时计算 |
| `visible_output_tps` | 正文 tokens 与正文事件时间跨度；推理计数未知且未显式关思考时保持未知 |
| `end_to_end_tps` | 服务端输出总 tokens / 整个请求耗时，包含 TTFT 与末尾 usage 等待 |
| `cache_hit_ratio` | 服务端缓存读取 tokens / 实际总输入 tokens；未报告缓存字段时为未知 |

TTFT 包括本机序列化、上传、网络、排队和服务端预填充；并非服务器纯推理指标。
请求串行执行，按轮打散厂家顺序；每次建立请求连接，不混入 SDK 连接池复用收益。
`--timeout` 同时用作 socket 阻塞超时和事件间总截止检查，不是能强行取消服务器计费的硬期限；
单次阻塞读取可能延迟截止检查。`--pause` 默认 1 秒，可为异步缓存落盘适当增大。

SSE 事件可能含多个 tokens，所以扣除首 token 的 TPS 是基于事件到达时间的客户端近似，
不是逐 token 服务器遥测。单个文本事件没有可测的生成跨度，TPS 为未知。
Responses 的 reasoning summary 不是全部思维链，Gemini thoughts 也可能不流出；这些情况
不会把隐藏推理 token 除以正文跨度制造高 TPS。可用时仍报告正文 TPS 与端到端 TPS。
`tps_basis=requested_non_thinking` 表示依赖了显式关思考的请求；不会补造缺失的推理 usage。
没有 usage 时绝不以字符数或 SSE 事件数冒充 tokens。

输入由固定种子的常见英文词组成，按约 4 字符/token 规划，不依赖第三方 tokenizer，
**10K/50K/500K/800K 是目标估算**。每个样本保留实际 `input_tokens`、字符数及 SHA-256，
比较前必须检查实际长度是否相近。文本没有真实用户数据，也不保存请求正文和模型输出。
准确 token 对齐需要相应模型的 tokenizer 或计数服务，当前不声称做到精确 10K。
输出任务固定为连续编号的软件测试建议，只测吞吐，不测质量/正确率。

`cold` 在输入最前方放置每请求唯一 nonce，尽量使长前缀不命中；它**不是**关闭厂商缓存的开关。
`warm` 每个目标/输入档位先预热一次（最多 16 输出 tokens），随后逐字重发相同输入。
`cache_policy=implicit` 使用供应商自动前缀缓存；原生 Anthropic 可选
`anthropic_ephemeral` 为热请求添加 `cache_control`。Gemini 显式 cachedContents、Responses
previous_response_id/session cache 的创建、保留和存储计费本轮未实现，不会偷偷发额外存储请求。
DeepSeek 默认自动缓存，其他渠道/模型的自动缓存可用性需看真实计数。
报告按 `mode`、实际 `cache_observed=hit/miss/unknown` 和返回模型分别统计，
不把预热请求计入均值，也不把热请求未命中计入“已命中”结果。部分命中仍显示比例。

JSON 保存每次成功/失败、预检、预热、均值、中位数、范围、样本标准差以及每项有效样本数。
不因为失败而补发请求。未通过鉴权的目标、流中断、配额和限流都可见。
输出达到 limit 属正常截断；安全拦截和异常中断不进入均值；无正文的样本没有 TTFT。
失败样本仍保留可观察的部分计数，但统计只用成功数据。
默认结果在被忽略的 `cache/performance/`，每次生成新文件、每请求原子检查点、文件权限 0600。
Ctrl+C 保留在途失败记录和此前结果；已有文件拒绝覆盖。

## 维护来源与验证

本轮按 2026-11-15 北京日期核查文档（网页可能滚动更新）；“文档核验”不代表实网通过：

- DeepSeek [模型及价格](https://api-docs.deepseek.com/quick_start/pricing/)、[思考](https://api-docs.deepseek.com/guides/thinking_mode/)、[缓存](https://api-docs.deepseek.com/guides/kv_cache/)、[Responses](https://api-docs.deepseek.com/guides/responses_api/)。
- 百炼 [DeepSeek API](https://help.aliyun.com/zh/model-studio/deepseek-api)，包括工作空间端点和 enable_thinking。
- 腾讯 [DeepSeek 调用指南](https://cloud.tencent.com/document/product/1823/132248)。
- 方舟 [Chat API](https://docs.volcengine.com/docs/ark/chat-api?lang=zh)、[深度思考](https://docs.volcengine.com/docs/ark/deep-thinking?lang=zh)、[DeepSeek V4.1 Flash](https://console.volcengine.com/ark/region%3Acn-beijing/docs/deepseek-v4-1-flash)。
- Anthropic [流式协议](https://platform.claude.com/docs/en/build-with-claude/streaming)、[缓存 usage](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。
- Gemini [生成与 usageMetadata](https://ai.google.dev/api/generate-content)。

新增一家时先查它自己的接口文档，在 `providers.py` 加有证据的厂家/协议参数规则及离线 fixture，
再 `check`，然后小输入、短输出烟测，最后才扩大重复次数和上下文矩阵。不要修改公开价格抓取器
来复用认证传输。回归使用 `python3 -m unittest discover -s tests`，测试不联网。
