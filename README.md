# Jamie Tennis 免费云端版

本目录是在已确认UX版本上改造的独立副本。当前现用Desktop项目没有被修改或停服。

## 2026-10-28 部署更新

- 用户已确认云端仅保留 systemadmin、Luo、Jamie 三个账号，原密码不变。
- 已完成 Supabase 初始化和独立核对：学员、课程、课包、余额、预约等业务数据均为空。本地旧系统未改动。
- 网站专用受限数据库连接已验证，凭据只通过 Streamlit Secrets 配置，不在仓库中。
- Supabase 根证书已包含，继续使用 verify-full 验证主机名与证书。
- 入口 cloud_app.py，分支 main。代码上传不代表网站已经发布。
- 配置检查现为 5 项通过。下方早期准备说明仅保留为历史参考，不要重复迁移或重建账号。

## 早期准备记录

- 云端入口：cloud_app.py。一个应用包含 ?portal=staff（Jamie/Luo）和 ?portal=student（学员）。
- 继续复用原有角色、课程、余额、缺课、审核规则，公共资料/教练私有档案边界保持不变。
- SQLite本机运行方式保留。云端入口必须配置PostgreSQL，连接失败不会偷偷创建本地空库。
- PostgreSQL由Supabase免费项目提供；代码由私有GitHub仓库保存；网站由Streamlit Community Cloud免费计划运行。
- 目前完成的是代码、迁移工具和本机真实PostgreSQL验证，还没有上传现有学员数据，也没有生成固定网址。

## 需要你完成的一步

在已登录的Supabase页面点击New project：名称jamie-tennis，保持Free计划，地区London（没有则Europe）。数据库密码自己设置并保存，不发到聊天里。若要求付费或绑卡，先停下核对。

创建好后会一起完成以下步骤，不需要你自行研究：

1. 在Connect选择Session pooler，端口5432（可用IPv4），将连接信息通过本机配置工具填写，不上传GitHub。
2. 检查连接；确认是自己的空项目。
3. 核对待迁移备份，确认现有学员、课时、课程、教练私有笔记与账号哈希将保存到你的Supabase项目。活跃登录会话不迁移，所有人重新登录。
4. 切换前暂停旧网站写入，使用现有工具生成最后备份，再执行显式迁移。目标已存在业务表/不同迁移记录时拒绝覆盖。
5. 创建网站专用数据库账号：只能读写业务数据和使用ID序列，不能创建表、改表或创建账号；此数据库账号与Jamie/Luo的网页账号无关。
6. 将审核过的代码上传私有GitHub仓库（无数据库、无密码），Streamlit选择cloud_app.py，Python 3.12或3.13。
7. 在Streamlit Secrets粘贴本机生成的服务器专用配置，选择可用的固定streamlit.app子域名。
8. 使用云端隔离的虚拟数据核查登录、预约、审核扣课；确认后切换入口。

## 本机工具（迁移前先核对，不要乱执行）

- 1-配置云端连接.bat：隐藏输入连接信息，保存到.cloud-private/admin.toml。文件存在则拒绝覆盖。
- 2-检查云端连接.bat：只检查连接，不上传数据。
- python cloud_setup.py plan --source "确认过的备份.db"：只检查本机结构和计数。
- python cloud_setup.py import --source "确认过的备份.db" --apply：显式上传，先保证旧网站停止写入并确认迁移范围。
- python cloud_setup.py runtime --apply：生成服务器专用配置到.cloud-private/streamlit-secrets.toml；只粘贴到Streamlit Secrets，不上传GitHub。
- cloud_backup.py export 私密路径.json：云端一致性快照备份（含私人资料/账号哈希），不包含旧登录会话。
- cloud_backup.py restore 私密路径.json --apply：只允许恢复到空的替代数据库区域，绝不清空/覆盖当前目标。

若已发生云端新预约或新扣课，不可直接切回旧SQLite，否则会遗漏新数据；先备份并核对云端新增记录。

## 保护措施

使用独立jamie_tennis数据库区域，启用RLS，不向匿名/学员API角色授权。网页只在服务器上连接数据库。加密连接使用verify-full验证证书，配置不进入浏览器。少量教练场景保留原SQLite单写者顺序：跨进程事务锁保护排课、预约、审核和扣课，暂不为扩容拆分锁。所有核心修改仍经过既有服务器权限校验。

## 验证结果

- 原有SQLite功能回归：443项中402项通过，41项PostgreSQL专用检查当时按设计跳过；无失败。
- 真实PostgreSQL 16.15：最终44项全部通过，无跳过，包括并发预约、审核扣课、重复提交、撤销退还、负余额、权限隔离、迁移、备份恢复、受限账号和云端入口。
- 云端配置/入口保护：4项全部通过。
- 测试使用独立临时SQLite和本机只监听127.0.0.1的PostgreSQL虚拟库，没有用真实学员信息。
- 尚未验证：你的Supabase实例、Streamlit实际托管、真实手机的公网访问、正式数据切换。这些需要项目创建和部署后实际执行。

## 免费限制

免费不等于全天候服务保证。Streamlit应用可能休眠，Supabase免费项目长期不活跃可能暂停，仍需人工备份。完成部署后你的电脑关机不影响云端运行。

官方资料：
- https://docs.streamlit.io/deploy/streamlit-community-cloud
- https://supabase.com/pricing
- https://supabase.com/docs/guides/database/connecting-to-postgres
