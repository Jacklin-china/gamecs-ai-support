# GameCS Python 后端

本目录提供 FastAPI 接口、LangGraph 玩家客服和 Celery 后台任务。管理端和玩家端分别位于 `../frontend/admin` 和 `../frontend/player`。旧项目已移至项目目录外备份；上线切换前仍须核对旧系统是否有新增写入。

## 启动开发环境

使用 Python 3.11 或以上版本，新建虚拟环境并运行 `pip install -e '.[test]'`。复制 `.env.example` 为 `.env`，配置**独立的新数据库**、长度至少 32 字符的随机 JWT 密钥和模型密钥。不要把新系统的 `DATABASE_URL` 指向旧的 `game_cs` 库。运行：

```powershell
alembic upgrade head
uvicorn app.main:app --reload --port 18765
```

接口文档位于 `http://localhost:18765/docs`。`LOCAL_MODE=true` 为单进程本地开发模式，无需 Redis：知识向量和对话保存在 MySQL，进程内后台线程处理任务。该模式仅适合小规模开发和单实例，不保存 LangGraph 的中间工具调用状态；`LOCAL_AUTO_TASKS=false` 可关闭后台线程。附件仍需要 MinIO。

升级现有安装前先备份数据库并在隔离副本验证迁移，然后执行 `alembic upgrade head`。新版工单沟通与评价需要 `d4a12ef75b20` 迁移；未迁移前不要将新版前后端用于真实工单。该迁移的降级会删除新增的沟通和评价表及其中数据，应先备份并确认数据处理方案。历史已评价工单不会自动生成评分记录。

`GET /api/v1/tickets` 默认仍返回原有数组；指定 `include_total=true` 时返回 `items`、`total`、`limit`、`offset`，并支持 `status` 与 `query`。工单消息使用 `GET/POST /tickets/{id}/messages`，可带 `request_id` 防止重试造成重复；工单提交人使用 `POST /tickets/{id}/rating` 对已完成工单评价。原状态变更接口仍为兼容旧客户端保留，旧客户端直接标记已评价时不会创建评分。

登录默认按连接来源限制为每分钟 10 次，AI 对话按登录用户限制为每分钟 20 次。本地模式使用单进程内存，分布式模式使用 Redis，Redis 不可用时相关请求返回 503。反向代理部署必须正确配置可信代理和客户端地址，否则多个用户会共用同一来源限制；不要直接信任客户端提交的转发头。仅在隔离测试中可设置 `RATE_LIMIT_ENABLED=false`。

本机 Windows 已配置好依赖后，可双击 `start-local.bat`，或在 `backend` 目录执行 `.\.venv\Scripts\python.exe scripts\start_local.py`，启动本地附件服务、后端与两个前端；需要先启动 MySQL。脚本不会终止已有程序，端口被占用时会提示检查。若没有本地 MinIO 程序，系统仍可启动，但附件功能不可用。

需要部署 Redis Stack 时，设置 `LOCAL_MODE=false`，并在另外两个终端启动 `celery -A app.tasks:celery worker --pool=solo --loglevel=info` 和 `celery -A app.tasks:celery beat --loglevel=info`。只运行一个调度器。生产环境应使用适合操作系统的 Celery worker。切换存储模式前先停止旧模式的后台进程；索引任务会检测存储位置变化并重新排队。

分别在两个前端目录执行 `npm ci` 与 `npm run dev`，默认端口分别为 5173 和 5174。它们通过 Vite 代理访问本机 18765 端口；`VITE_API_BASE` 可在独立部署时指定 API 地址。首次安装空数据库可运行 `python scripts/create_admin.py` 创建管理员；迁移已有管理员后无需重新创建。

## 迁移旧数据

1. 对旧 MySQL、Redis 和 MinIO 进行备份，准备一个不同名的空 MySQL 数据库，在新库运行 `alembic upgrade head`。不要运行旧项目的初始化 SQL，它包含删表语句。
2. 设置 `LEGACY_DATABASE_URL` 和 `DATABASE_URL`。运行 `python scripts/migrate_legacy.py` 在内存中演练；确认无误后加 `--apply` 写入空目标库。脚本对旧库使用只读一致性快照，拒绝覆盖已有数据，并归档旧表原始记录。原始记录含个人数据和密码哈希，应限制数据库访问并妥善备份。
3. 运行 `python scripts/verify_legacy.py` 对业务映射字段及每张旧表的全部归档内容进行只读比对；角色、AI 分析和历史会话归属另行检查。归档保留不再使用的若依配置和其他原始字段，但不代表新系统提供对应的若依功能。无角色且未停用的旧用户映射为玩家；原有管理员和客服分别映射为管理员和客服。部分旧审计记录的操作人无法匹配用户，保留原始值，不猜测身份。历史对话未记录游戏，保留供查看，继续对话时需新建会话。
4. 配置原来的 BGE-M3 模型后，运行 `python scripts/import_legacy_vectors.py ../data/vector-store.json` 查看精确匹配情况，加 `--apply` 导入匹配向量到当前存储模式。其余知识由后台任务重建。索引不一致时检索会以 MySQL 的启用状态和内容为准，拒绝使用过期记录。
5. 如果需要旧附件内容，启动旧 MinIO 并设置 `LEGACY_MINIO_ENDPOINT`、`LEGACY_MINIO_ACCESS_KEY`、`LEGACY_MINIO_SECRET_KEY`；目标服务使用 `.env` 中的 `MINIO_*`。运行 `python scripts/migrate_objects.py` 检查源附件，加 `--copy` 复制到新对象并逐个比对 SHA-256。脚本不会覆盖或删除旧对象。若接受旧图片不可用，保留其数据库引用即可；新增附件仍需要新 MinIO。
6. 切换前暂停旧系统写入并处理其待落库消息；检查旧库在迁移后是否发生变化。如果发生变化，不要覆盖已经使用的新库，须先制定保留新旧两边写入的合并方案；否则重新核对当前快照，验证权限、Agent、任务和前端后再调整入口。旧库和备份应保留供回退。

`../infra/docker-compose.yml` 可建立一套**独立的新环境**；其中 MySQL 使用自己的数据卷，不会自动包含本机迁移数据。若使用该环境，仍须按上述步骤将旧数据导入容器内的新库。Redis Stack 必须具备 Search 和 JSON 功能，且应启用持久化并防止内存淘汰，以保护队列和 Agent 状态。

## 测试

运行 `pytest -q`，在前端分别运行 `npm run build`。本机无法连接旧附件服务或 Redis Stack 时，这些测试不能替代这些服务的真实联调与最终切换验证。

若本机已有 MinIO 可执行文件，可设置 `MINIO_TEST_BINARY` 为其绝对路径，再运行 `pytest -q tests/test_minio_integration.py`。该测试仅启动隔离的临时进程、临时目录和随机端口，不使用现有 MinIO 数据；未设置该变量时测试会跳过。

MySQL 集成测试只允许连接本机上名称以 `gamecs_isolated_` 开头、已执行全部迁移且用户和分类表为空的临时数据库；设置 `MYSQL_TEST_URL` 后运行 `pytest -q tests/test_mysql_integration.py`。测试会向该数据库写入合成记录，不能指向业务库。CI 使用独立 MySQL 服务执行该测试。

Redis Stack、Celery 和 Compose 的集成测试由 GitHub Actions 使用一次性服务执行；Redis 测试使用 `GAMECS_REDIS_TEST_URL=redis://127.0.0.1:16391/0`，并要求实例及索引为空，不能用于现有 Redis。模型与向量服务使用合成替身；真实模型测试结果另见 `evaluation/`。

本地可选对象存储兼容性测试使用 `MINIO_TEST_BINARY` 和 `SILO_TEST_BINARY` 指向两个可执行文件，然后运行 `pytest -q tests/test_silo_migration.py`。测试只创建临时目录：使用 MinIO 写入合成对象、用 Silo 读写、再切回 MinIO 校验。已验证 MinIO RELEASE.2025-09-07T16-13-09Z 与 Silo RELEASE.2026-10-26T00-00-00Z 的单节点基础读写回退；不代表所有历史对象、配置或集群部署均可无风险升级，升级前仍须备份并验证数据副本。
