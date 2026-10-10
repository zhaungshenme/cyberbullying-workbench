# 材料与研判任务服务

运行入口为 `app:app`，从本目录启动：`python -m uvicorn app:app --host 127.0.0.1 --port 18788 --workers 1 --no-access-log`。

私有配置、服务器安装与 Dify 接入见 [部署指南](../docs/DEPLOYMENT.md)。API、角色和输入输出见 [使用说明](../docs/USAGE.md)。必须配置不同的 `WORKBENCH_BACKEND_TOKEN` 和 `CB_SERVICE_TOKEN`，首次启动须设至少12字符的管理员临时口令。

材料、任务和权限数据存于 `WORKBENCH_DATA_DIR`，本仓库不含运行数据库。当前依赖SQLite与单进程锁，不能并行启动多个业务 worker 访问同一目录。

安装 `requirements-test.txt` 后，从本目录执行 `python -m pytest -q tests`。测试使用临时数据与假的 Dify/模型网关，不调用模型，不代表语言研判准确率。
