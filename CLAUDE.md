# IPO 仪表盘 · Claude 协作规范

本文件由团队共同维护，所有 AI 协作规则以此为准。
线上地址：https://sysusq-qq.github.io/ipo-dashboard/

---

## 零、新伙伴入门（从零开始）

### 第一步：安装必要工具

**1. 安装 Claude Code**

打开终端（Mac：Command+空格 → 输入"终端"），运行：
```bash
npm install -g @anthropic/claude-code
```

> 如果提示 `npm: command not found`，先安装 Node.js：https://nodejs.org → 下载 LTS 版本安装后重试。

**2. 登录 Claude**

```bash
claude
```

首次运行会弹出浏览器要求登录 Anthropic 账号，登录后回到终端即可。

**3. 确认 Git 已安装**

```bash
git --version
```

如果提示 `command not found`，Mac 运行 `xcode-select --install` 安装；Windows 去 https://git-scm.com 下载安装。

---

### 第二步：Clone 仓库

```bash
# 1. 选择一个你想存放项目的目录，例如进入桌面
cd ~/Desktop

# 2. Clone 仓库（会在当前目录创建 ipo-dashboard 文件夹）
git clone https://github.com/sysusq-qq/ipo-dashboard.git

# 3. 进入项目目录
cd ipo-dashboard
```

---

### 第三步：用 Claude Code 打开项目

```bash
# 在项目目录内启动 Claude Code
claude .
```

Claude Code 会自动读取本目录的 `CLAUDE.md`，加载所有团队规则。你会看到命令行提示符，直接输入指令即可，例如：

```
> 新增一只股票 06810 商米科技，帮我分析招股书
> 把 01879 曦智科技的暗盘收盘价更新为 212.5，涨幅 16.0%
```

---

### 第四步：修改内容后同步给团队

每次修改完 `index.html` 或 `CLAUDE.md` 后，运行以下命令提交并推送：

```bash
# 查看改了哪些文件
git status

# 将改动加入暂存区（. 表示全部改动文件）
git add .

# 提交，引号内写本次改动说明
git commit -m "更新01879暗盘数据"

# 推送到 GitHub，团队其他人即可看到
git push
```

---

### 第五步：获取团队最新内容

每次开始工作前，先同步一下别人的改动：

```bash
git pull
```

---

### 常见问题

**Q：push 时提示没有权限？**
需要被仓库管理员加为 Collaborator。把你的 GitHub 用户名发给管理员，等收到邮件邀请后接受即可。

**Q：pull 时提示 conflict（冲突）？**
说明你和别人同时改了同一个地方。把问题截图发给管理员，或在 Claude Code 里说"帮我解决 git conflict"。

**Q：Claude Code 没有加载本项目的规则？**
确认你是在 `ipo-dashboard` 目录内启动的 `claude .`，不是在其他目录。

---

## 一、项目结构

```
ipo-dashboard/
├── index.html                   # 网页主文件（股票数据硬编码在此，手动维护）
├── data.json                    # 自动化脚本写入（与 index.html 数据独立）
├── scripts/
│   ├── fetch_and_analyze.py     # 扫港交所新股 → 写 data.json → 飞书通知
│   ├── send_reminder.py         # 每日 09:40 申购截止提醒
│   └── grey_market_monitor.py   # 暗盘实时监控（16:14 启动）
└── .github/workflows/
    ├── daily-update.yml         # cron: 每日 01:00 UTC（= 09:00 HKT）
    └── sub-reminder.yml         # cron: 每日 01:40 UTC（= 09:40 HKT）
```

**编辑入口**：新增/修改股票只改 `index.html`，commit + push 后 GitHub Pages 自动部署。

---

## 二、新增股票：完整字段规范

### 2.1 数据结构模板

```javascript
{
  code: '00000',
  applyEndTs: 0000000000,   // 北京时间 YYYY-MM-DD 10:00（见下方计算规则）
  listTs:     0000000000,   // 北京时间 YYYY-MM-DD 00:00（见下方计算规则）
  name: '公司简称',
  nameEn: 'Company Name',
  sector: '行业 / 子赛道 / 特殊标签（如 18C / 18A / WVR）',
  listDate: 'YYYY-MM-DD',
  subDate: 'YYYY-MM-DD ~ YYYY-MM-DD',
  price: 0.00,              // 招股价 HKD
  lotSize: 000,             // 每手股数
  entryFee: 0000.00,        // 1手入场费（含经纪佣金等，招股书第4页表格）
  totalIssue: 'X万股H股（全球发售）',
  publicIssue: 'X万股（10%香港公开发售）',
  greenshoe: '有（最多X万股，约15%）' | '无',   // 见第三节查验方法
  mktCapH: '约HK$XX亿（总股本X亿股×HK$XX）',
  pe: '约XXx（2025A）/ PS Xx',
  verdict: 'da' | 'wait' | 'no',
  verdictLabel: '打' | '观望' | '不打',
  score: 0,                 // 0-100，四维评分合计
  position: 'X手现金仓' | '孖展X手 + 现金X手',
  sponsors: '保荐人A、保荐人B',
  isTransfer: false,        // 转板股票设为 true
  cornerstone: {
    total: 'USD XX万（≈HK$XX亿），占发售股份XX%',
    tier1: [
      { name: '投资方名称', amt: 'USD XXX万', lockup: 'X个月' },
    ],
    others: []
  },
  conclusion: '...',        // 200字以内，含核心逻辑和明确结论
  scores: [
    { label: '业务质量',   pts: 0, max: 25, desc: '...' },
    { label: '财务健康',   pts: 0, max: 25, desc: '...' },
    { label: '估值吸引力', pts: 0, max: 25, desc: '...' },
    { label: '资本结构',   pts: 0, max: 25, desc: '...' },
  ],
  financial: [
    { label: '收入（人民币亿元）', y2023: '', y2024: '', y2025: '' },
    { label: '毛利率',             y2023: '', y2024: '', y2025: '' },
    { label: '净利润（人民币M）',  y2023: '', y2024: '', y2025: '' },
    { label: '收入增速',           y2023: '—', y2024: '', y2025: '' },
  ],
  cfChecks: [
    { icon: '✅' | '⚠️', text: '...', tag: 'ok' | 'warn', tagText: '标签' },
  ],
  risks: [ '<strong>风险标题</strong>：说明' ],
  actions: [
    { date: 'X月X日 节点', title: '标题', desc: '操作建议' },
  ],
  subscription: {
    scenarios: [
      { label: '保守', mult: 0,  premPct: 0 },
      { label: '基准', mult: 0,  premPct: 0 },
      { label: '乐观', mult: 0,  premPct: 0 },
    ],
    recClass: 'da' | 'wait' | 'no',
    recTitle: '✅ 积极申购' | '⚠️ 谨慎参与' | '❌ 不建议参与',
    lots: 0,
    method: '现金仓' | '孖展X成',
    marginOk: true | false,
    marginTip: '...',
    rationale: '申购逻辑说明，3-5句',
    urgentTip: '⏰ 截止X/X(周X) 10:00；...'
  },
  greyMarket: {
    date: 'YYYY-MM-DD',     // 上市日前一天（可为周末，如周一上市→周日暗盘）
    price: null,            // 暗盘收盘价，脚本 18:30 后自动写入
    changePct: null,        // 涨跌幅，脚本自动写入
    peakPrice: null,        // 当日最高价，暗盘期间手动追踪后填写
    peakChangePct: null     // 最高涨幅，手动填写
  }
}
```

---

## 三、时间戳填写规则 ⚠️

### 3.1 applyEndTs（申购截止时间）

富途港股打新申购截止**统一为北京时间 10:00**。

```
applyEndTs = 截止日 UTC 00:00 的 Unix 秒 + 7200
```

Python 计算示例：
```python
from datetime import datetime, timezone, timedelta
dt = datetime(2026, 4, 24, 2, 0, 0, tzinfo=timezone.utc)  # UTC 02:00 = BJ 10:00
applyEndTs = int(dt.timestamp())  # 1776996000
```

注释格式：`// 北京时间 2026-04-24 10:00`

### 3.2 listTs（上市日期）⚠️ 唯一来源：招股书

**禁止使用 Futu API 返回的 list_timestamp**（API 返回 09:30 开市时间，曾导致 03296 华勤技术日期偏移一天，时间线全部算错）。

```
listTs = 上市日 BJ 00:00 = 上市日 UTC 前一天 16:00 的 Unix 秒
       = 上市日 UTC 00:00 - 28800
```

Python 计算示例：
```python
dt = datetime(2026, 4, 28, 16, 0, 0, tzinfo=timezone.utc)  # = BJ 04/29 00:00
listTs = int(dt.timestamp())  # 1777392000
```

注释格式：`// 北京时间 2026-04-29 00:00（招股书）`

---

## 四、绿鞋（超额配售权）查验规则 ⚠️

招股书中绿鞋有两种写法，**必须同时搜索**：

| 关键词 | 说明 |
|--------|------|
| `超額配股權` | **主流写法**，多数招股书使用 |
| `超額配售` | 少数情况，不能作为唯一搜索词 |

**只搜"超額配售"会漏判**（01879 曦智科技的教训：全文无"超額配售"，但有"超額配股權"，误判为无绿鞋）。

验证流程：
1. 用 pdfplumber 搜 `超額配股權` → 有结果 = 有绿鞋，在释义页找具体股数
2. 无结果再搜 `超額配售`
3. 招股书第 2 页发售结构如写"視乎超額配股權行使與否而定"= 有绿鞋

已验证案例：

| 代码 | 绿鞋 |
|------|------|
| 01879 曦智科技 | 有（最多 206.93 万股，约 15%） |
| 02493 迈威生物 | 无 |
| 06810 商米科技 | 无 |

---

## 五、时间节点计算规则

```
申购截止  = applyEndTs（BJ 10:00）
中签结果  = prevTradingDay(暗盘日)  ← 暗盘日前最近港股交易日
暗盘      = listTs - 86400          ← 可为周末/节假日（如周一上市→周日暗盘，正常）
上市      = listTs（BJ 00:00，09:30 开盘）
```

`prevTradingDay` 需跳过**周末 + 港交所假期**，index.html 中已内置实现。

**错误示例**：周一上市(4/28) → 暗盘周日(4/27) → 中签结果不能是 4/26(周六)，应为 4/24(周五)。

---

## 六、JavaScript 北京日期计算 ⚠️

```javascript
// ✅ 正确：固定 +8h，getUTC* 读取即为北京日期，与运行环境时区无关
const d = new Date(ts * 1000 + 8 * 3600000);

// ❌ 错误：UTC+8 环境下 getTimezoneOffset()=-480，与 +8h 正好抵消，拿到的是 UTC 日期
const d = new Date(ts * 1000 + new Date().getTimezoneOffset() * 60000 + 8 * 3600000);
```

---

## 七、股票状态规则

`status` / `statusText` 字段已废弃，所有状态由 `calcStatus()` 动态计算，规则如下：

| 条件 | 状态 |
|------|------|
| `isTransfer: true` | 转板 |
| 当前时间 ≥ `listTs` | 已上市 |
| 当前时间 ≥ `applyEndTs` | 已截招 |
| 当前北京日期 = applyEndTs 所在日 | 今日截招 |
| 其他 | 招股中 |

`nodeState` 判断顺序：**先判断同一北京日期（返回 today）→ 再判断 nowTs ≥ ts（返回 past）**，顺序不能颠倒，否则 listTs=00:00 BJ 当天全天会被误判为 past（已过）。

---

## 八、招股书解读与评分体系

### 四维评分（满分 100 分）

| 维度 | 满分 | 核心看点 |
|------|------|---------|
| 业务质量 | 25 | 护城河/增速/市占率/全球化 |
| 财务健康 | 25 | 净利润趋势/毛利率/净利率/现金流 |
| 估值吸引力 | 25 | PE/PS/同类对标/WVR折价 |
| 资本结构 | 25 | 基石质量与独立性/绿鞋/保荐人背书 |

### 结论标准

| 分数 | 建议 | 仓位 |
|------|------|------|
| 75+ | 打（da） | 孖展+现金，积极参与 |
| 55-74 | 观望（wait） | 1手现金仓彩票性质 |
| <55 | 不打（no） | 不参与 |

### 特殊上市类型识别

| 类型 | 特征 | 策略 |
|------|------|------|
| 18C 未商业化科技 | 无盈利，PS 100x+，高波动 | 彩票1手或不打 |
| 18A 生物科技 | 亏损中，核心管线价值定估值 | 关注临床进展 |
| A+H 双市场 | H 股相对 A 股折价 | 关注折价幅度和 H 股流动性 |
| WVR 架构 | 创始人超级投票权，治理风险 | 估值折价处理 |

---

## 九、飞书通知规则

**Webhook**：见 `scripts/` 中各脚本配置
**关键词验证**：消息必须包含 `IPO仪表盘`，否则报 `code:19024 Key Words Not Found`

三类通知：
1. **发现新股**（10:00/14:00）：fetch_and_analyze.py 扫港交所主板
2. **申购截止提醒**（09:40）：send_reminder.py，富途统一 10:00 截止
3. **暗盘实时更新**（16:14 启动）：grey_market_monitor.py，5分钟轮询

---

## 十、暗盘监控规则

- 暗盘时段：**16:15–18:30 HKT**（不是 16:00–23:00）
- 数据源：Futu OpenAPI `get_market_snapshot`（FutuOpenD 运行在 127.0.0.1:11111）
- 暗盘期间 `market_state` 返回 `GREY`，`last_price` 为实时价
- **18:30 收盘后**脚本自动写入 `greyMarket.price` / `greyMarket.changePct`
- `peakPrice` / `peakChangePct`（当日最高价）需**暗盘期间人工追踪**后手填

涨幅建议区间：

| 涨幅 | 建议 |
|------|------|
| ≥20% | 大幅溢价，持仓 |
| ≥10% | 溢价健康，持仓 |
| ≥5%  | 视持仓成本决定 |
| ≥0%  | 谨慎持仓 |
| ≥-5% | 孖展立刻止损 |
| <-5% | 止损离场 |

---

## 十一、港交所资源

- 新股招股章程下载：https://www2.hkexnews.hk/New-Listings/New-Listing-Information/Main-Board
- 招股书PDF解析工具：pdfplumber（`pip install pdfplumber`）
- 关键页面：封面（第2页看发售结构）、预期时间表（第5-7页）、基石投资者（目录查页码）、释义页（查绿鞋定义）
