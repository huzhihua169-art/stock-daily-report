# A股晨报/收盘复盘自动推送

每天 **08:30 晨报**、**15:35 收盘复盘**（北京时间，工作日），自动生成并推送到**企业微信群**。电脑无需开机。

- 行情数据：新浪财经 + 东方财富公开API（免费、无需key）
- 新闻：东方财富搜索API（免费、无需key）
- AI生成：DeepSeek V4 Flash（约 ¥5-15/月）
- 推送：企业微信群机器人 webhook（免费）
- 节假日自动跳过（脚本校验当日有无行情数据）

## 配置步骤（约15分钟）

### 1. 企业微信群机器人（拿 webhook）
1. 下载企业微信APP → 个人免费注册（无需真实企业）
2. 建一个群（可以只有自己）→ 群设置 → 群机器人 → 添加
3. 复制 webhook 地址（形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx`）

### 2. DeepSeek API key
1. 打开 platform.deepseek.com → 注册 → 充值 ¥10
2. API keys → 创建 → 复制（形如 `sk-xxx`）

### 3. 配置 GitHub Secrets
仓库 → Settings → Secrets and variables → Actions → New repository secret：

| Name | 值 |
|------|---|
| `WECOM_WEBHOOK_URL` | 企微机器人webhook地址 |
| `DEEPSEEK_API_KEY` | DeepSeek的sk-xxx |

可选：Variables 标签页加 `WATCHLIST`（自选股，新浪代码格式逗号分隔，如 `sh600519,sz300750`）。

### 4. 手动测试
Actions → 「A股晨报与收盘复盘」→ Run workflow → 选报告类型 → 勾选强制运行。

## 本地测试

```bash
export WECOM_WEBHOOK_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
export DEEPSEEK_API_KEY="sk-xxx"
export FORCE_RUN=1
python report_morning.py   # 或 report_close.py
```

## 文件说明

| 文件 | 作用 |
|------|------|
| `data_fetcher.py` | 新浪/东财免费数据抓取（指数/板块/涨停池/涨跌家数/新闻） |
| `report_morning.py` | 晨报主程序 |
| `report_close.py` | 收盘复盘主程序 |
| `notifier.py` | 企微推送（超长自动分段，企微markdown三色系：warning红/info绿/comment灰） |
| `llm.py` | DeepSeek调用（含投研纪律system prompt） |
| `archive/` | 每次报告自动commit存档 |

## 成本估算

DeepSeek V4 Flash：输入¥1/百万token、输出¥2/百万token。每日2次×约15K token ≈ **月¥5-15**。
⚠️ DeepSeek已公告近期涨价+峰谷定价（9-12/14-18点高峰2倍），如介意可将复盘时间改到18:35（改workflow里cron为 `35 10 * * 1-5`）。

## 免责声明

本仓库输出为AI生成的研究参考，不构成投资建议。投资有风险，决策需独立判断。
