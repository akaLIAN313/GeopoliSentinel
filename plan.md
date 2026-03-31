# Project Plan: Geopolitical Sentinel
**核心目标**: 构建一个自动化的 OSINT（开源情报）数据管道，不再仅仅聚合已发生的军事摩擦新闻，而是通过抓取"内部政治生存危机"的领先指标，利用大语言模型预测美伊/以伊爆发直接军事冲突的概率以及对于世界经济的影响。

## 0. 可行性总评 (Feasibility Overview)

| Phase | 技术可行性 | 主要风险 | 评级 |
|-------|-----------|---------|------|
| Phase 1 - 数据抓取 | 高 | RSS 源不稳定；NewsAPI 免费层限制严苛 | ⚠️ 中等 |
| Phase 2 - AI 引擎 | 高 | Token 上下文溢出；LLM 输出不稳定；API 费用 | ⚠️ 中等 |
| Phase 3 - Telegram 推送 | 高 | 消息长度限制；Bot 封禁风险 | ✅ 低风险 |
| Phase 4 - 自动化部署 | 高 | GitHub Actions 免费额度；无持久化存储 | ⚠️ 中等 |

> **架构决策记录 (ADR-001)**: Phase 2 采用"滚动状态压缩"模式（Rolling State Compression），而非每日重新输入全量历史文章。详见第 2.5 节。

## 1. 核心架构与技术栈

**正常运行（每日循环）**:
```
[数据源: RSS + NewsAPI]
        ↓
[Phase 1: 抓取 & 过滤 & 去重 → articles.json (delta)]
        ↓
[Phase 2: Claude API]
  System Prompt: 转移视线战争理论（硬编码）
  User Context:  state.md（滚动状态）+ articles.json（当日增量）
        ↓
  输出: 新 state.md + report.json
        ↓
[Phase 3: Telegram 推送]
        ↑
[Phase 4: GitHub Actions Cron 触发]
```

**冷启动（仅首次，`state.md` 不存在时）**:
```
[bootstrap.py — Claude Agent with web_search tool]
  System Prompt: 转移视线战争理论（硬编码，与日常相同）
  工具:          web_search（Claude 自主决定搜索议题和查询词）
  搜索议程:      系统提示中指定5个必须覆盖的调研方向
        ↓
  输出: 初始 state.md（含历史深度，供日常循环使用）
```

**技术栈**:
- **语言**: Python 3.12
- **数据获取**: `feedparser` (RSS), `requests` (NewsAPI)
- **数据结构化**: `pydantic v2`（严格定义 AI 输出 schema）
- **AI 引擎（日常）**: `anthropic` Claude API，tool use 强制结构化输出；`google-genai` Gemini 作 fallback
- **AI 引擎（冷启动）**: `anthropic` Claude API + `web_search` 内置工具，agentic loop
- **推送端**: `python-telegram-bot >= 20.0`（异步版本）
- **自动化**: GitHub Actions Cron
- **持久化**: `data/` 目录通过 `git commit` 写回仓库

## 2. Phase 1: 数据抓取管道 (Data Pipeline)

### 2.1 可行性分析

**RSS 抓取 (`feedparser`)**
- ✅ 技术完全可行，无需 API Key
- ⚠️ 风险：RSS 源可能变更 URL、停止维护、或被 Cloudflare 拦截（需设置 User-Agent）
- ⚠️ 风险：部分源（如 Al-Monitor）内容在摘要中被截断，全文需要爬取（不在当前范围内）
- 缓解：为每个源配置备用 URL，并在抓取失败时记录警告而不是崩溃

**NewsAPI**
- ✅ 技术可行
- ❌ **重大限制**：免费开发者套餐（Developer Plan）限制如下：
  - 仅可访问过去 **1 个月**内的文章
  - 每天最多 **100 次请求**
  - **不允许用于商业用途或生产环境**（仅限开发测试）
  - 无法访问完整文章内容，仅有标题和描述
- 缓解：将 NewsAPI 作为补充源而非主力；若要生产化，考虑 MediaStack（免费层 500次/月）或 GDELT Project（完全免费，无限制）

deduplication: to be addressed, delta data is considered addressable in terms of amount now.

### 2.2 数据源清单（修订版）

| 数据流 | 关键词 | 推荐数据源 | 备用源 |
|-------|-------|----------|-------|
| A - 以色列内部危机 | Netanyahu trial, Ben-Gvir, Smotrich, coalition collapse, strike, protest | Times of Israel RSS, Jerusalem Post RSS | Haaretz RSS (英文版) |
| B - 伊朗内部危机 | Rial exchange rate, inflation, IRGC, succession, protest | Al-Monitor RSS, NewsAPI (q="Iran economy OR Rial OR IRGC") | Radio Free Europe/RFE RSS, GDELT |
| C - 军事异动（参照组） | air strike, missile, CENTCOM, Hezbollah | Reuters Middle East RSS, Al Jazeera RSS | AP News RSS |

### 2.3 数据结构定义（补充）

每条抓取到的新闻应规范化为以下结构，以便 Phase 2 稳定消费：

```
Article:
  - id: str           # 标题的 SHA256 哈希前8位，用于去重
  - title: str
  - summary: str      # 截断至 500 字符
  - source: str       # 数据源名称
  - stream: str       # "A" | "B" | "C"
  - published_at: datetime (UTC)
  - url: str
```

### 2.4 输出格式

Phase 1 输出一个 `articles.json` 文件，包含过去 24 小时内、通过关键词过滤后的、去重后的**增量文章列表（delta）**（预计 5-30 条）。若无任何文章通过过滤，输出空列表并记录日志，Phase 2 应能处理空输入（输出低压力分数，但仍保留历史状态）。

Phase 1 **不负责**读取或处理历史数据——历史上下文由 Phase 2 通过滚动状态文档管理（见第 3.4 节）。Phase 1 的职责边界严格限定在：**抓取 → 过滤 → 去重 → 输出当日增量**。

---

## 3. Phase 2: AI 预测引擎 (The Resonance Engine)

### 3.1 可行性分析

**Context Window 风险（已通过滚动状态模式缓解）**
- ✅ 采用滚动状态压缩（见第 3.4 节）后，每日输入结构固定为：状态文档 + 当日增量，上下文成本**不随系统运行时间增长**
- Token 预算估算（稳定态）：
  - 状态文档：~1,500 tokens（固定上限）
  - 当日增量文章（最多 20 条 × 500 字符）：~3,000 tokens
  - System Prompt + 输出：~2,000 tokens
  - **合计：~6,500 tokens/天（上限稳定）**
- 对比朴素方案（累计原始文章）：第 30 天时朴素方案需要 ~50,000+ tokens，滚动方案始终维持在 ~6,500 tokens

**LLM 输出不稳定性（关键）**
- ⚠️ 直接要求 LLM 输出 JSON 有时会失败（格式错误、额外文本包裹等）
- 缓解：使用 `anthropic` SDK 的 **tool use / function calling** 功能强制结构化输出，而非依赖 prompt 中的"输出 JSON"指令
- 备选：`pydantic` + `instructor` 库自动重试解析

**模型选型建议**
- 推荐使用 `claude-3-5-haiku` 或 `claude-3-5-sonnet`（成本与能力平衡）
- Gemini 作为 fallback：当 Claude API 不可用时自动切换
- 每次分析预计消耗：~8,000 tokens，约 $0.002-0.008 USD（Haiku 定价）

**`crisis_resonance_index` 计算逻辑（原计划模糊，需明确）**
- 原计划说"双方压力都很高时，该数值应呈指数级上升"，但这只是文字描述，LLM 不会自动遵守数学公式
- 建议：在 System Prompt 中提供明确的参考公式作为"思维锚点"：
  ```
  参考逻辑（非硬性约束）：
  - 如果 israel_score < 4 或 iran_score < 4，index 上限为 40%
  - 如果 israel_score >= 7 且 iran_score >= 7，index 下限为 70%
  - 双高压力情景（两者均 >= 8）时，index 应在 85%-100% 范围内
  ```

### 3.2 Pydantic 输出 Schema（补充）

```
ResonanceReport:
  - israel_pressure_score: int (1-10)
  - israel_pressure_reason: str (max 100 chars)
  - iran_pressure_score: int (1-10)
  - iran_pressure_reason: str (max 100 chars)
  - crisis_resonance_index: float (0.0-1.0，代码层面转换为百分比展示)
  - executive_summary: str (max 300 Chinese chars)
  - top_signals: list[str] (最多3条，驱动本次评分的关键新闻标题)
  - analysis_date: date (UTC)
  - articles_analyzed: int (本次输入的文章数量)
```

`top_signals` 字段是对原计划的补充——让分析报告可解释，用户能看到 AI 基于哪几条新闻得出结论。

### 3.3 API 失败处理（原计划缺失）

- 若 Claude API 返回错误（rate limit、服务中断），自动重试最多 3 次（指数退避）
- 若全部重试失败，切换至 Gemini API 使用相同 prompt
- 若两者均失败，跳过本次分析，通过 Telegram 发送简短的故障通知（而非静默失败）
- **状态文档在分析失败时不更新**，下一次运行将使用上一次的有效状态文档 + 两天的累计增量文章

### 3.4 滚动状态压缩模式 (ADR-001)

**核心思路**：每日运行不是把所有历史原始文章喂给 AI，而是维护一份"活状态文档"（Living State Document）。每次运行的 AI 输入 = 上一日的状态文档 + 今日增量文章；AI 的输出 = 今日用户报告 + 更新后的状态文档（供明日使用）。

```
Day N:
  输入: state_N-1.md (昨日状态) + articles_N.json (今日增量)
  输出: report_N.json (用户推送) + state_N.md (明日状态)

Day N+1:
  输入: state_N.md + articles_N+1.json
  输出: report_N+1.json + state_N+1.md
  ...（原始文章不再累积）
```

**状态文档结构（`data/state.md`）**：

状态文档由 AI 生成并维护，包含以下固定章节，**总长度上限 1,500 tokens（约 1,000 中文字）**：

```markdown
## 状态文档 — 截至 [DATE]

### 以色列压力态势
- 当前压力分数: [X]/10（趋势: ↑上升 / → 持平 / ↓下降）
- 核心驱动事件（持续追踪中）:
  - [事件1]: [状态描述，例如"内塔尼亚胡审判第3周，极右翼联盟施压加剧"]
  - [事件2]: ...
- 已消退/解决的事件: [若有]

### 伊朗压力态势
- 当前压力分数: [X]/10（趋势: ↑ / → / ↓）
- 核心驱动事件:
  - [事件1]: [状态描述]
- 已消退的事件: [若有]

### 冲突共振指数 (CRI) 近期走势
- [DATE-6]: [X]%
- [DATE-5]: [X]%
- [DATE-4]: [X]%
- [DATE-3]: [X]%
- [DATE-2]: [X]%
- [DATE-1]: [X]%（昨日）

### 待观察信号
- [信号描述]: 首次出现于 [DATE]，尚无后续发展
```

**关键设计原则**：
1. **AI 更新状态文档，而非代码**：代码只传递状态文档给 AI，由 AI 决定哪些事件已消退、哪些需要新增、趋势如何变化。这比用代码规则维护状态更能捕捉语义变化。
2. **状态文档长度由 AI 自我控制**：在 System Prompt 中明确要求"总长度不超过 1,000 字，已完全消退的事件应从状态中移除"，防止状态文档无限膨胀。
3. **CRI 走势只保留最近 7 天**：足够看出趋势，不会无限增长。
4. **原始文章不存入状态**：状态文档只存 AI 的分析结论，不存原始新闻文本。

**冷启动处理（Bootstrap Pass — ADR-002）**：

直接用当日增量启动会产生"空心"状态文档——AI 不知道审判进行到第几周、伊朗经济崩溃的基线在哪里、哪些事件是慢性背景噪声。冷启动通过让 Claude 自主搜索来解决这个问题，无需手动维护文档库。

Bootstrap Pass 由独立脚本 `src/bootstrap.py` 执行，**仅在 `data/state.md` 不存在时运行**（或通过 `--force` 参数强制重跑，例如地缘格局发生重大变化时）。

**两类输入的不同处理方式**：

*转移视线战争理论（硬编码进系统提示）*

战争理论是分析框架，不是数据——它描述的是"内部生存压力如何转化为对外冲突意愿"的逻辑结构，本身不会随新闻变化。将其硬编码进 system prompt 有以下好处：
- 无需 API 调用获取
- 每次运行（含日常循环）都自动携带，不依赖 `state.md` 是否包含它
- 版本由 git 管理（修改理论 = 修改代码，有完整历史）

系统提示中应包含的理论要素：
```
分析框架：转移视线战争理论（Diversionary War Theory）
- 核心命题：当领导人面临国内政治生存威胁时，发动或升级对外冲突的概率上升
- 适用条件：领导人个人（非国家）面临审判/政变/革命威胁；国内经济或社会危机达到临界点
- 历史参照：[2-3个简短案例，如阿根廷马岛战争、伊朗1980年两伊战争]
- 本系统监测逻辑：以色列方向追踪内塔尼亚胡个人政治生存；伊朗方向追踪最高领袖政权稳定性
- 双高压力假设：两国同时存在高强度内部压力时，双方均有转移视线动机，冲突风险非线性上升
```

*当前地缘现实（Claude agent + web_search 动态获取）*

Bootstrap 脚本启动一个 agentic loop：Claude 收到系统提示（含战争理论）后，使用 `web_search` 工具自主搜索当前地缘状态，直到覆盖所有指定调研方向为止。

**系统提示中明确要求 Claude 必须覆盖的5个调研方向**：

```
在生成初始状态文档前，你必须通过 web_search 工具完成以下5个方向的调研，
每个方向至少执行1次有效搜索：

1. 以色列内政危机现状
   搜索议题：内塔尼亚胡审判最新进展、本-格维尔/斯莫特里奇联盟稳定性、
             以色列国内抗议动态

2. 伊朗政权稳定性现状
   搜索议题：里亚尔/土曼汇率、通货膨胀数据、IRGC内部动向、
             哈梅内伊健康状况与继承人问题

3. 美伊/以伊近期外交与军事信号
   搜索议题：过去90天内的重大军事事件、外交接触或破裂、制裁动态

4. 伊朗核谈判状态
   搜索议题：JCPOA谈判现状、铀浓缩进展、IAEA报告摘要

5. 地区代理人网络动态
   搜索议题：真主党、胡塞武装、伊拉克什叶派民兵最新动态

完成调研后，将所有发现整合为一份初始状态文档（格式见规范）。
```

**Bootstrap 执行流程**：

```
bootstrap.py 启动:
  1. 从 src/prompts.py 加载硬编码的 system prompt（含战争理论 + 调研议程）
  2. 启动 Claude API agentic loop（messages=[], tools=[web_search]）
  3. Claude 自主执行搜索，直到5个方向均已覆盖
  4. Claude 输出初始 state.md（使用与日常循环相同的状态文档 schema）
  5. 将结果写入 data/state.md
  6. 退出，控制权交还 analyzer.py

估计 API 调用次数：5-15 次 tool call（一次性操作，成本约 $0.05-0.20）
```

**Bootstrap 触发逻辑（`analyzer.py` 中）**：
```
if not exists("data/state.md"):
    run bootstrap.py  # 自主搜索 → 生成初始 state.md
proceed with normal daily analysis using state.md + today's delta
```

`bootstrap/docs/` 目录不再需要，`bootstrap/` 仅保留 `src/prompts.py` 中的硬编码理论文本。

**状态文档的持久化**：
- `data/state.md` 随每日分析结果一起通过 `git commit` 写回仓库（见第 5.1 节）
- 历史状态文档无需保留（git history 本身就是归档）

---

## 4. Phase 3: Telegram 推送模块 (Notification)

### 4.1 可行性分析

- ✅ 技术完全可行，`python-telegram-bot` 稳定成熟
- ⚠️ **消息长度限制**：Telegram 单条消息上限为 **4,096 字符**（Markdown 模式）
  - 估算：完整报告约 800-1,200 字符，**在限制内**，但若 executive_summary 写得过长需截断
- ⚠️ **Markdown 格式兼容性**：Telegram 使用 MarkdownV2，特殊字符（`.`, `-`, `(`, `)` 等）需转义，否则消息发送失败
- 缓解：使用 `telegram.helpers.escape_markdown()` 工具函数处理动态内容

**Bot 封禁风险**
- 发送过于频繁或内容被举报可能导致 Bot 被封
- 本项目每天仅发送 1 次，风险极低

### 4.2 消息模板设计（补充）

```
[ALERT_EMOJI] 🔭 Project Resonance 日报 — [DATE]

🇮🇱 以色列内部压力指数: [SCORE]/10
   [REASON]

🇮🇷 伊朗内部压力指数: [SCORE]/10
   [REASON]

━━━━━━━━━━━━━━━━━━━━
[INDEX_EMOJI] 冲突共振指数 (CRI): [INDEX]%
━━━━━━━━━━━━━━━━━━━━

📡 驱动信号:
• [SIGNAL_1]
• [SIGNAL_2]
• [SIGNAL_3]

📋 深度简报:
[EXECUTIVE_SUMMARY]

📰 本次分析文章数: [COUNT] 条
```

- 当 `crisis_resonance_index >= 75%`：`ALERT_EMOJI = 🚨`，`INDEX_EMOJI = 🔴`
- 当 `crisis_resonance_index >= 50%`：`ALERT_EMOJI = ⚠️`，`INDEX_EMOJI = 🟡`
- 当 `crisis_resonance_index < 50%`：`ALERT_EMOJI = 🔭`，`INDEX_EMOJI = 🟢`

### 4.3 错误通知（原计划缺失）

- Phase 1 或 Phase 2 失败时，应发送一条简短的错误通知到同一 Chat ID，内容包含失败阶段和错误摘要，而不是静默失败
- 这使得用户能在不查看 GitHub Actions 日志的情况下知晓系统状态

---

## 5. Phase 4: 自动化与部署 (Deployment)

### 5.1 可行性分析

**GitHub Actions Cron**
- ✅ 完全可行
- ⚠️ **时区注意**：GitHub Actions Cron 使用 UTC 时间。目标时间 8:00 AM UTC+8 = **00:00 UTC**
  - Cron 表达式：`0 0 * * *`
- ⚠️ **延迟抖动**：GitHub Actions 免费层在高峰时段（00:00 UTC 整点是热门时间）可能延迟 5-30 分钟，这对本项目可以接受
- ⚠️ **免费额度**：公开仓库 GitHub Actions 完全免费；私有仓库每月 2,000 分钟免费额度，本项目预计每次运行 < 3 分钟，每月 31 次，约消耗 93 分钟，**远低于上限**

**数据持久化问题（关键缺口）**
- ❌ GitHub Actions 的 runner 是无状态的：每次运行后，所有本地文件都会消失
- 这意味着：无法在运行间保存历史数据、无法做趋势分析、无法去重检查"过去已分析过的文章"
- 解决方案（按推荐程度排序）：
  1. **将输出 JSON 提交回仓库**（最简单）：每次运行后，用 `git commit & push` 将 `data/latest_report.json` 和 `data/history/YYYY-MM-DD.json` 写回仓库，既有持久化也有版本历史
  2. **GitHub Actions Artifacts**：将输出上传为 Artifact（保留 90 天），但无法跨 workflow run 直接读取，需要额外 API 调用
  3. **外部存储**（Gist、S3、Supabase 等）：适合生产化，但增加复杂度

  **推荐采用方案 1**，在 Actions workflow 末尾添加：
  ```yaml
  - name: Commit report to repo
    run: |
      git config user.name "resonance-bot"
      git config user.email "bot@resonance"
      git add data/
      git diff --staged --quiet || git commit -m "chore: daily report $(date -u +%Y-%m-%d)"
      git push
  ```

### 5.2 GitHub Secrets 清单（补充）

| Secret 名称 | 说明 | 获取方式 |
|------------|------|---------|
| `ANTHROPIC_API_KEY` | Claude API 密钥（日常分析 + bootstrap web_search agent） | console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | Bot Token | @BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | 目标 Chat/Channel ID | @userinfobot 或 @getidsbot |
| `NEWS_API_KEY` | NewsAPI 密钥（可选） | newsapi.org |
| `GEMINI_API_KEY` | Gemini API 密钥（日常分析 fallback，bootstrap 不使用） | aistudio.google.com |

### 5.3 workflow 文件结构（补充）

```yaml
# .github/workflows/main.yml
name: Resonance Daily Report

on:
  schedule:
    - cron: '0 0 * * *'   # 每天 00:00 UTC = 08:00 UTC+8
  workflow_dispatch:        # 允许手动触发（用于调试）

jobs:
  run-resonance:
    runs-on: ubuntu-latest
    timeout-minutes: 10    # 防止 runaway job 消耗额度

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          fetch-depth: 0    # 需要完整历史以便 push 回仓库

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run Phase 1 - Fetch data
        run: python src/fetcher.py

      - name: Run Phase 2 - AI Analysis
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python src/analyzer.py

      - name: Run Phase 3 - Send notification
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python src/notifier.py

      - name: Commit report data
        run: |
          git config user.name "resonance-bot"
          git config user.email "bot@noreply"
          git add data/
          git diff --staged --quiet || git commit -m "chore: report $(date -u +%Y-%m-%d)"
          git push
```

---

## 6. 项目文件结构（补充）

```
sentinel_US-Iran/
├── .github/
│   └── workflows/
│       └── main.yml
├── src/
│   ├── fetcher.py          # Phase 1: RSS + NewsAPI 抓取与过滤，输出当日增量
│   ├── analyzer.py         # Phase 2: 日常 AI 分析；检测到无 state.md 时自动调用 bootstrap.py
│   ├── bootstrap.py        # 冷启动：Claude agent + web_search，生成初始 state.md
│   ├── notifier.py         # Phase 3: Telegram 推送
│   ├── prompts.py          # 硬编码系统提示（战争理论框架 + bootstrap 调研议程，两者共用）
│   └── models.py           # Pydantic schemas (Article, ResonanceReport)
├── data/
│   ├── articles.json       # Phase 1 → Phase 2 中间产物（每次覆盖，当日增量）
│   ├── state.md            # 滚动状态文档（每次更新，跨日持久化）
│   ├── latest_report.json  # 最新分析结果（每次覆盖）
│   └── history/
│       └── YYYY-MM-DD.json # 历史报告归档（每日追加）
├── requirements.txt
├── README.md
└── plan.md
```

Phase 间通过文件传递数据（articles.json → analyzer.py → report.json → notifier.py），解耦每个阶段，方便独立调试。

---

## 7. 风险汇总与缓解措施

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| RSS 源 URL 失效 | 中 | 中 | 配置备用源；失败时跳过而非崩溃 |
| NewsAPI 免费层请求耗尽 | 高 | 低 | 降频调用；转用 GDELT 免费替代 |
| LLM 输出非法 JSON | 中 | 高 | 使用 tool use 强制 schema；pydantic 验证 |
| Context window 超限 | 低 | 高 | 输入文章截断至最多 20 条 |
| Claude API 中断 | 低 | 高 | 自动 fallback 至 Gemini API |
| Telegram 消息超 4096 字符 | 低 | 中 | 在 notifier.py 中硬截断 executive_summary |
| GitHub Actions 延迟 | 高 | 低 | 可接受；本项目对时效性要求不严格 |
| 无历史数据持久化 | 确定 | 中 | 采用 git commit 写回方案 |

---

## 8. Claude Code 执行指令（修订）

按以下顺序逐步实现，每个步骤完成后等待确认再进入下一步：

1. **共享基础**: 创建 `src/models.py`（Article、ResonanceReport schema）和 `src/prompts.py`（战争理论系统提示 + bootstrap 调研议程）
2. **Phase 1**: 实现 `src/fetcher.py`，输出 `data/articles.json`（当日增量）
3. **Bootstrap**: 实现 `src/bootstrap.py`，Claude agent + web_search agentic loop，生成初始 `data/state.md`；本地手动测试通过后再继续
4. **Phase 2**: 实现 `src/analyzer.py`，日常分析循环（state.md + articles.json → 新 state.md + report.json）；自动检测并触发 bootstrap
5. **Phase 3**: 实现 `src/notifier.py`，消息模板见第 4.2 节
6. **Phase 4**: 生成 `requirements.txt`，编写 `.github/workflows/main.yml`，注意 bootstrap 在 Actions 环境中的 timeout 设置（建议 bootstrap 单独给 15 分钟）
