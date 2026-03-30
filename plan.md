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

```
[数据源: RSS + NewsAPI]
        ↓
[Phase 1: 抓取 & 过滤 & 去重]
        ↓ JSON
[Phase 2: AI 分析引擎]
        ↓ Pydantic 结构化输出
[Phase 3: Telegram 推送]
        ↑
[Phase 4: GitHub Actions Cron 触发]
```

**技术栈**:
- **语言**: Python 3.12
- **数据获取**: `feedparser` (RSS), `requests` (NewsAPI), `httpx` (异步备选)
- **数据结构化**: `pydantic v2`（严格定义 AI 输出 schema）
- **AI 引擎**: `anthropic` (Claude API) 主力，`google-genai` (Gemini) 备用
- **推送端**: `python-telegram-bot >= 20.0`（异步版本）
- **自动化**: GitHub Actions Cron
- **本地持久化（运行间）**: JSON 文件存入 GitHub Actions Artifacts，或写回 repo 的 `data/` 目录

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

**冷启动处理（Bootstrap Pass）**：

直接用当日增量文章启动会产生一个"空心"状态文档——AI 不知道审判进行到第几周、伊朗经济崩溃的基线在哪里、哪些事件是慢性背景噪声而哪些是真正的新增信号。正确的冷启动方式是在首次每日循环前，进行一次独立的**历史语境引导（Bootstrap Pass）**，生成一份有历史深度的初始 `state.md`。

Bootstrap Pass 由独立脚本 `src/bootstrap.py` 执行，**仅在 `data/state.md` 不存在时运行**（或通过 `--force` 参数强制重跑）。

**Bootstrap 信息源（分两类）**：

*类型 A — 背景知识文档（静态，手动维护于 `bootstrap/docs/`）*

这类文档描述的是结构性背景，不会每天变化，适合手动整理一次：

| 文档 | 建议来源 | 描述 |
|------|---------|------|
| `israel_political_crisis.md` | Wikipedia "2023–24 Israeli judicial crisis", Foreign Affairs | 司法改革危机始末、内塔尼亚胡审判进展、极右翼联盟结构 |
| `iran_economic_state.md` | IMF reports, Atlantic Council | 里亚尔汇率崩溃历史、通胀数据基线、制裁影响 |
| `irgc_power_structure.md` | RAND Corporation, Brookings | IRGC 在伊朗政治中的角色、最高领袖继承危机背景 |
| `us_iran_timeline.md` | Wikipedia "Iran–United States relations" | 关键历史节点（JCPOA 退出、苏莱曼尼刺杀、2024核谈判） |
| `diversionary_war_theory.md` | 学术摘要 | 转移视线战争理论的核心逻辑与历史案例，作为 AI 的分析框架基础 |

这些文档放入 `bootstrap/docs/`，通过 git 管理，可按需手动更新。

*类型 B — 近期历史新闻（动态，由脚本抓取）*

运行 `src/bootstrap.py` 时，脚本自动从同一批 RSS/NewsAPI 源抓取过去 **30 天**的符合过滤条件的历史文章（而非 24 小时），作为近期上下文输入。

**Bootstrap 处理流程（两阶段压缩）**：

直接将所有背景文档 + 30 天文章塞入单次 API 调用会超出 context window，需分阶段处理：

```
阶段 1 — 分块摘要（map）:
  对每份背景文档 → 调用 AI，输出该文档的核心要点摘要（≤200字）
  对 30 天历史文章，按数据流 A/B/C 分组 → 每组调用一次 AI，输出该数据流的近期态势摘要（≤300字）

  输出: 每份文档/分组一个摘要片段

阶段 2 — 状态合成（reduce）:
  将所有摘要片段合并（总计约 2,000 tokens）→ 调用一次 AI
  System Prompt 要求: "基于以下背景材料和近期动态，生成一份符合规定格式的初始状态文档"
  输出: 初始 data/state.md，包含完整的以色列/伊朗压力态势、背景理论框架，以及对近期30天走势的初步 CRI 评分序列
```

总计 API 调用次数：约 6-8 次（一次性操作，成本约 $0.01-0.05）

**Bootstrap 触发逻辑（`analyzer.py` 中）**：
```
if not exists("data/state.md"):
    print("No state document found. Running bootstrap pass first...")
    run bootstrap.py
    assert exists("data/state.md"), "Bootstrap failed"
proceed with normal daily analysis using new state.md + today's delta
```

**Bootstrap 的 `bootstrap/docs/` 目录也纳入 git 管理**，确保在 GitHub Actions 环境中可直接访问（无需额外下载）。文档建议在首次部署前手动更新到当前时间节点。

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
| `ANTHROPIC_API_KEY` | Claude API 密钥 | console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | Bot Token | @BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | 目标 Chat/Channel ID | @userinfobot 或 @getidsbot |
| `NEWS_API_KEY` | NewsAPI 密钥（可选） | newsapi.org |
| `GEMINI_API_KEY` | Gemini API 密钥（备用 AI） | aistudio.google.com |

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
│   ├── fetcher.py          # Phase 1: RSS + NewsAPI 抓取与过滤
│   ├── analyzer.py         # Phase 2: AI 引擎，读取 articles.json，输出 report.json
│   ├── notifier.py         # Phase 3: Telegram 推送
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

按以下顺序逐步实现，每个 Phase 完成后等待确认再进入下一个：

1. **Phase 1**: 创建 `src/models.py`（Article schema），然后实现 `src/fetcher.py`，输出 `data/articles.json`
2. **Phase 2**: 实现 `src/analyzer.py`，使用 tool use 强制结构化输出，输出 `data/latest_report.json` 和 `data/history/YYYY-MM-DD.json`
3. **Phase 3**: 实现 `src/notifier.py`，消息模板见第 4.2 节
4. **Phase 4**: 生成 `requirements.txt`，编写 `.github/workflows/main.yml`，补充 `README.md`

在开始编写代码前，请确认你是否理解了**"通过内部政治压力预测外部冲突"**这一核心逻辑，并在得到确认后从 Phase 1 开始。
