# Talent Signal MVP

本项目的目标是打通候选人、入职、早期测评和绩效数据，用合成数据分析招聘渠道的入职转化和 30 天留存，提供数据质量检查、API 和 HTML 报告。项目使用 Python 3.12、FastAPI、SQLite 和 Pandas，通过 Pytest 和 GitHub Actions 运行自动化测试。180 天绩效仅汇总已录入的样本和成绩。

## 本地运行

```bash
git clone https://github.com/Ivan9ua/talent-signal-mvp.git
cd talent-signal-mvp
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m talent_signal.storage sample_data --as-of 2025-07-31
python -m uvicorn talent_signal.app:app
```

启动后，访问 [HTML 报告](http://127.0.0.1:8000/report) 或 [交互式 API 文档](http://127.0.0.1:8000/docs)。

| 入口 | 内容 |
| --- | --- |
| `GET /api/channels` | 渠道汇总和按入职月、管理单元、渠道拆分的月度批次 |
| `GET /api/data-quality` | 快照截止日、四张表的行数及各项异常影响行数 |
| `GET /report` | 可在浏览器查看或保存的 HTML 报告 |

渠道 API 与 HTML 报告支持 `?as_of=2025-02-08` 回看历史。省略时使用导入的快照截止日，超过快照截止日返回 HTTP 422。可保存离线报告。

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/report --output talent-signal-report.html
```

## 数据与指标口径

样例含 18 名合成候选人、11 名入职员工、9 条测评、3 条实际绩效样例记录，覆盖 11 个管理单元。候选人通过 `candidate_id` 关联入职，入职记录通过 `employee_id` 关联测评和绩效。一名候选人最多对应一条入职记录，每名员工可各有一条测评和绩效记录。

- **渠道入职率**等于截至统计日已入职人数除以同渠道已申请候选人数。
- **渠道 30 天留存率**等于入职已满 30 天且前 30 天未离职的人数除以入职已满 30 天的人数。入职当天至第 30 天离职计为未留存，第 31 天离职计为已留存。退出日期为空表示截至快照截止日无离职记录，计算依赖离职数据截至该日完整。
- **月度批次留存率**按入职月、管理单元和渠道计算，分母为该组当月入职人数。月末满 30 天后展示整批留存率。观察中的批次展示已知离职人数。
- **早期测评和 180 天绩效**各展示实际录入人数和均分。测评窗口为入职后第 0 至 30 天，样例绩效记录采用入职后第 180 天。分数范围为 0 至 100，均分只使用截至统计日已录入的记录。项目仅输出已观察到的绩效统计。
- **无样本**的率和均分在 API 中显示为 `null`，在 HTML 中显示为 `—`。

## 导入与数据质量

导入目录包含 `candidates.csv`、`hires.csv`、`assessments.csv` 和 `performance.csv`。列名和顺序见 [样例数据](sample_data)，日期格式为 `YYYY-MM-DD`。每次导入会原子替换完整快照，默认 SQLite 文件为 `var/talent_signal.sqlite3`。可用环境变量 `TALENT_SIGNAL_DB` 指定独立数据库，导入与应用启动时使用同一个值。

CSV 文件或列结构出错时导入失败，原快照继续可用。数据质量检查覆盖必填值、重复主键、关联缺失、管理单元、日期顺序、观察窗口、分数范围和快照截止日。存在异常时，渠道 API 与报告返回 HTTP 409，质量接口继续提供检查结果。尚未导入时返回 HTTP 503。

## 测试

```bash
python -m pytest -ra
```

GitHub Actions 在推送和 PR 时使用 Ubuntu 与 Python 3.12 安装同一套依赖并运行测试。测试用临时 SQLite 数据库覆盖四表关联、观察窗边界、月度成熟规则、空样本、数据质量阻断、快照替换和 HTML 输出。
