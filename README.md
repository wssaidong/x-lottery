# 🎱 X-Lottery — 彩票数据分析

大乐透 & 双色球历史开奖数据可视化分析工具。纯前端 + Python 数据拉取，零依赖部署。

## ✨ 功能

- **走势分析** — 号码趋势折线图，直观查看近期走势
- **频率统计** — 各号码出现频次柱状图，冷热号一目了然
- **遗漏分析** — 当前遗漏值 + 历史最大遗漏对比
- **和值走势** — 每期号码和值变化趋势
- **实时数据** — 支持从官方数据源拉取最新开奖结果
- **多期数支持** — 5 / 10 / 20 / 30 / 50 / 100 期可选

## 📸 截图

![首页](index.html)

大乐透分析页：

![大乐透分析](html/dlt_analysis.html)

双色球分析页：

![双色球分析](html/ssq_analysis.html)

## 🚀 快速开始

### 1. 拉取最新数据

```bash
# 默认拉取大乐透最近 30 期
python3 refresh_data.py

# 拉取双色球
python3 refresh_data.py -g ssq

# 生成所有常用期数（5/10/20/30/50/100）
python3 refresh_data.py --all -g both
```

数据会保存到 `data/` 目录，生成 JSON 和 CSV 两种格式。

### 2. 启动本地服务器

```bash
# 默认 8080 端口
python3 server.py

# 指定端口
python3 server.py -p 3000
```

启动后访问：

| 页面 | 地址 |
|------|------|
| 首页 | http://127.0.0.1:8080 |
| 大乐透分析 | http://127.0.0.1:8080/html/dlt_analysis.html |
| 双色球分析 | http://127.0.0.1:8080/html/ssq_analysis.html |

## 📡 API

服务器提供实时数据拉取接口：

```bash
# 大乐透最近 30 期
curl http://127.0.0.1:8080/api/dlt?count=30

# 双色球最近 50 期
curl http://127.0.0.1:8080/api/ssq?count=50
```

返回 JSON 数组，每条记录包含期号、开奖日期、号码、销售额、奖池余额等字段。

## 📁 项目结构

```
x-lottery/
├── index.html              # 首页（彩种选择）
├── server.py               # 本地开发服务器
├── refresh_data.py         # 数据拉取脚本
├── html/
│   ├── dlt_analysis.html   # 大乐透分析看板
│   └── ssq_analysis.html   # 双色球分析看板
└── data/                   # 开奖数据（JSON + CSV）
```

## 🛠 技术栈

- **前端** — 原生 HTML/CSS/JS + [ECharts](https://echarts.apache.org/)
- **后端** — Python 3 标准库（无第三方依赖）
- **数据源** — 中国体彩网（大乐透）、500.com（双色球）

## 📄 许可证

[MIT](LICENSE)
