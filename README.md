# 教学反馈数据采集 Demo

一个本地运行的 FastAPI + SQLite 应用，用于维护教学名单、批量录入晚辅/专项反馈、查看历史记录，并把筛选结果导出为 Excel。

## 技术栈

Python 3.11、FastAPI、Jinja2、SQLAlchemy、SQLite、openpyxl、pytest。

## 安装与运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m app
```

启动后访问 http://127.0.0.1:8000。

`python -m app` 会自动创建 `data/` 目录、初始化 `data/teaching_demo.db`、写入 85 条指标字典，然后启动服务。

## 数据库位置

- 数据库文件：`data/teaching_demo.db`（SQLite，WAL 模式，外键已启用）。
- 指标字典：`db/seed_indicators.sql`（首次启动自动写入，之后采用「缺则插入」语义，不覆盖已有行）。

## Excel 导出

在「历史记录」页用筛选条件过滤后，点击「导出 Excel」即可下载包含 4 个工作表（学生、班级、晚辅反馈、专项反馈）的 `.xlsx` 文件；导出会沿用当前筛选条件（含日期、班级、学生、类型、状态）。

## 安全备份

在应用停止运行后，直接复制 `data/teaching_demo.db` 即可完成备份；不要在应用运行期间拷贝（WAL 模式下可能有未合并的日志）。

## 说明

本项目是一个数据采集演示，没有登录与权限控制，请勿在生产环境直接使用。
