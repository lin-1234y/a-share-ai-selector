# A股智能选股

本项目是一个本地 A 股数据采集、智能问诊、相似 K 线检索与个股画像系统。它在你的电脑上运行，手机和电脑连接同一个 Wi-Fi 后也能像网页 App 一样打开使用。

## 小白启动

直接双击：

```text
启动A股智能选股.bat
```

启动窗口会显示两个地址：

- 电脑访问：`http://127.0.0.1:8766/`
- 手机访问：类似 `http://192.168.x.x:8766/`

手机和电脑必须连接同一个 Wi-Fi。如果 Windows 防火墙弹窗，请允许 Python 访问“专用网络”。

## 能做什么

通过网页完成：

- 更新股票基础信息
- 更新日行情与最新行情快照
- 更新财务指标与估值指标
- 按质量、估值、动量综合打分并导出候选股
- 用中文条件问诊并导出候选股
- 查找和某只股票走势形态相似的个股
- 查看个股近期 K 线、财务、估值、公告、新闻、主营业务和基本面画像

默认股票池为沪深主板和创业板。系统会排除 ST/退市风险、上市时间过短、流动性过低、核心数据缺失严重的股票。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

如使用 Tushare Pro，请设置环境变量：

```powershell
$env:TUSHARE_TOKEN = "your-token"
```

未设置 `TUSHARE_TOKEN` 时，系统会自动使用 AKShare。

## 命令

```powershell
python -m stock_selector update-basic
python -m stock_selector update-market --start 20250101 --end 20250531
python -m stock_selector update-universe-market --start 20250101 --end 20260531
python -m stock_selector update-finance
python -m stock_selector screen --date 20250531 --top 50
python -m stock_selector update-all --start 20250101 --end 20250531 --top 50
python -m stock_selector serve
```

默认数据库路径是 `data/stock.db`，导出文件位于 `exports/`。

## 本地仪表盘

命令行启动：

```powershell
python -m stock_selector serve --host 0.0.0.0 --port 8766
```

然后在电脑浏览器打开 `http://127.0.0.1:8766`。启动命令会同时打印一个局域网地址，例如 `http://192.168.1.23:8766`，手机和电脑连同一个 Wi-Fi 时，用手机浏览器打开这个地址即可。

如果手机打不开，请检查：

- 电脑和手机是否在同一个 Wi-Fi
- Windows 防火墙是否允许 Python 访问专用网络
- 命令窗口是否还开着，服务关闭后网页也会打不开

仪表盘包含：

- 总览：股票池数量、行情记录、财务记录、最新交易日、板块分布
- 总览：可一键更新沪深主板 + 创业板行情库
- 选股：按质量、估值、动量三因子评分，支持导出 CSV
- 问诊：输入中文条件，例如“历史高点回撤30%以上的非ST非亏损股”
- 问诊：支持“全A股里选一个月下跌大于30%的股”，按最近 20 个交易日计算
- 问诊：支持“找和300750最近半年走势最像的股票”，可跳转到相似 K 线检索
- 相似 K 线：按 1 个月、半年、1 年窗口，在本地行情库中找走势形态相似的股票
- 个股：输入股票代码，查看最新行情、估值、财务、收益和价格曲线
- 个股：可实时拉取公告、新闻、主营业务、行业概念，并生成基本面画像

仪表盘直接读取 `data/stock.db`。如果页面没有结果，请先运行基础数据、行情和财务更新命令。

## AI 功能

AI 功能是可选的。未配置 API Key 时，系统会使用本地规则解析和本地摘要，不影响普通选股、相似 K 线和个股查询。

如需启用 OpenAI：

```powershell
$env:OPENAI_API_KEY = "your-api-key"
$env:OPENAI_MODEL = "gpt-4.1-mini"
python -m stock_selector serve --host 0.0.0.0 --port 8766
```

AI 只用于自然语言解析和摘要生成；K 线相似度仍由本地行情算法计算。

## 数据源

- Baostock：优先使用的收盘后数据源，无需 token。
- AKShare：备用免费数据源，无需 token。
- Tushare Pro：读取 `TUSHARE_TOKEN`，接口失败时会记录错误并回退到可用数据源。

## 直接提问

先更新你关心股票的日线和财务数据，再用 `ask` 提问：

```powershell
python -m stock_selector update-market --start 20200101 --end 20260531 --symbols "000001,300750,600519"
python -m stock_selector update-finance --symbols "000001,300750,600519"
python -m stock_selector ask "历史高点回撤30%以上的非ST非亏损股" --date 20260531 --top 50
python -m stock_selector ask "价格在60周线附近的股" --date 20260531 --top 50
```

结果会导出到 `exports/`。第一版支持：

- 历史高点回撤 30% 以上
- 最近 20 个交易日下跌 30% 以上
- 价格在 60 周线附近，默认上下 5%
- 自动排除 ST/退市风险股
- 自动排除亏损或缺少盈利证明的股票

本系统仅用于研究和选股辅助，不实现自动交易，也不构成投资建议。
