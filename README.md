# Container Manager 配置管理工具

基于群晖 DSM 7.2.2 开发的 Container Manager（原 Docker）核心配置可视化管理 Web 工具。

## 📋 功能特性

### 1. 基础状态展示
- 实时显示 Container Manager 运行状态（运行/停止）
- 展示配置文件最后修改时间、备份文件数量、端口监听状态
- 配置文件内容预览

### 2. dockerd.json 配置编辑
- **可编辑项**：
  - `registry-mirrors`：Docker 镜像加速地址（支持多个镜像源）
  - `http-proxy`：HTTP 代理地址
  - `https-proxy`：HTTPS 代理地址
  - `no-proxy`：跳过代理的地址
- **只读项**：
  - `log-driver`、`log-opts`、`storage-driver`、`pidfile` 等系统关键配置
  - 自动保留原始值，防止误操作

### 3. 备份与回滚
- 自动备份：每次保存配置前自动创建备份
- 备份列表：按时间倒序展示所有备份文件
- 一键回滚：选择任意备份文件进行恢复
- 备份管理：支持手动创建和删除备份

### 4. 配置应用生效
- 保存配置后可一键重启 Container Manager
- 重启方式：使用 `systemctl restart pkg-ContainerManager-dockerd.service`（快速重启）
- 备用重启：如 systemctl 失败，可使用 `synopkg restart ContainerManager`
- 实时展示重启日志（通过 journalctl 监控服务日志）
- 二次确认弹窗防止误操作

### 5. 安全保障
- 仅允许编辑镜像源和代理配置
- 保护所有系统关键配置项
- 生产环境禁用 Flask 调试模式
- 完整的操作日志记录

---

## 🚀 快速开始

### 前置条件

- 群晖 NAS 系统 DSM 7.2.2（其他版本可能兼容）
- Container Manager 已安装
- root 权限（用于操作配置文件）

### 一键安装

1. **上传工具文件到群晖**

   将以下文件上传到群晖任意目录（如 `/volume1/docker/`）：
   ```
   ContainerManagerSettingTool/
   ├── deploy.sh
   ├── app.py
   ├── .env
   └── templates/
       ├── index.html
       └── edit.html
   ```

2. **SSH 登录群晖**

   ```bash
   ssh admin@你的群晖IP
   sudo -i  # 切换到 root 用户
   cd /volume1/docker/ContainerManagerSettingTool  # 进入工具目录
   ```

3. **执行安装脚本**

   ```bash
   # 添加执行权限
   chmod +x deploy.sh
   
   # 执行安装
   ./deploy.sh install
   ```

4. **访问 Web 界面**

   安装成功后，通过浏览器访问：
   ```
   http://你的群晖IP:8888
   ```

---

## 📖 使用说明

### deploy.sh 脚本命令

| 命令 | 说明 |
|------|------|
| `./deploy.sh install` | 一键安装（环境检查 → 依赖安装 → 文件部署 → 启动服务） |
| `./deploy.sh start` | 启动服务 |
| `./deploy.sh stop` | 停止服务 |
| `./deploy.sh restart` | 重启服务 |
| `./deploy.sh status` | 查看服务状态 |
| `./deploy.sh uninstall` | 卸载工具（备份文件可选择保留） |

### 使用示例

```bash
# 首次安装
./deploy.sh install

# 查看服务运行状态
./deploy.sh status

# 重启服务
./deploy.sh restart

# 停止服务
./deploy.sh stop

# 启动服务
./deploy.sh start

# 卸载工具
./deploy.sh uninstall
```

---

## 📁 目录结构

```
/volume1/web_packages/ContainerManagerAddonTool/
├── deploy.sh           # 一键部署/管理脚本
├── deploy.log          # 部署日志（自动生成）
├── .env                # 配置文件
├── app.py              # Flask 主程序
├── app.log             # 运行日志（自动生成）
├── backups/            # 备份目录（自动创建）
│   ├── dockerd.json_20240101_120000
│   └── dockerd.json_20240101_130000
└── templates/          # 前端模板
    ├── index.html      # 首页
    └── edit.html       # 配置编辑页
```

---

## ⚙️ 配置文件说明

`.env` 文件配置项：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `FLASK_APP` | Flask 应用入口 | `app.py` |
| `FLASK_ENV` | 运行环境 | `production` |
| `PORT` | 服务监听端口 | `8888` |
| `DOCKER_CORE_CONFIG` | Docker 配置目录 | `/var/packages/ContainerManager/etc` |
| `BACKUP_PATH` | 备份目录 | `/volume1/web_packages/ContainerManagerAddonTool/backups` |
| `DOCKER_STATUS_CMD` | CM 状态命令路径 | `synopkg status ContainerManager` |

---

## 🔧 常见问题排查

### 1. 依赖安装失败

**问题**：pip 安装 Flask 等依赖失败

**解决方案**：
```bash
# 手动安装依赖
python3 -m pip install flask python-dotenv json5 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 如果提示权限问题
python3 -m pip install flask python-dotenv json5 --user -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 端口被占用

**问题**：8888 端口已被其他服务占用

**解决方案**：
```bash
# 查看端口占用
netstat -tlnp | grep 8888

# 停止占用端口的服务，或修改 .env 文件中的端口
vim /volume1/web_packages/ContainerManagerAddonTool/.env
# 将 PORT=8888 改为其他端口

# 重启服务
./deploy.sh restart
```

### 3. 权限不足

**问题**：无法操作配置文件

**解决方案**：
```bash
# 确保以 root 用户运行
sudo -i
./deploy.sh restart
```

### 4. Container Manager 状态检测失败

**问题**：无法获取 Container Manager 运行状态

**解决方案**：
```bash
# 检查 Container Manager 是否安装
ls -la /var/packages/ContainerManager/

# 检查状态脚本是否存在
ls -la /var/packages/ContainerManager/scripts/start-stop-status

# 手动测试状态命令
/var/packages/ContainerManager/scripts/start-stop-status status
```

### 5. 服务启动后无法访问

**问题**：浏览器无法打开 Web 界面

**解决方案**：
```bash
# 检查服务是否运行
./deploy.sh status

# 检查防火墙设置
# 群晖控制面板 → 安全性 → 防火墙，确保 8888 端口开放

# 检查日志
cat /volume1/web_packages/ContainerManagerAddonTool/app.log
```

---

## 🔒 安全注意事项

1. **权限要求**：本工具需要 root 权限运行，仅建议在受信任的内网环境中使用
2. **配置保护**：只读配置项无法通过 Web 界面修改，保护系统关键设置
3. **操作日志**：所有操作都会记录到 `app.log`，便于审计
4. **备份机制**：每次修改配置前都会自动备份，可随时回滚

---

## 📝 手动部署步骤

如果一键部署脚本无法正常工作，可以按以下步骤手动部署：

```bash
# 1. 创建目录
mkdir -p /volume1/web_packages/ContainerManagerAddonTool/{templates,backups}

# 2. 复制文件
cp app.py /volume1/web_packages/ContainerManagerAddonTool/
cp .env /volume1/web_packages/ContainerManagerAddonTool/
cp templates/*.html /volume1/web_packages/ContainerManagerAddonTool/templates/

# 3. 安装依赖
python3 -m pip install flask python-dotenv json5 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 4. 设置权限
chmod -R 755 /volume1/web_packages/ContainerManagerAddonTool

# 5. 启动服务
cd /volume1/web_packages/ContainerManagerAddonTool
nohup python3 app.py > /dev/null 2>&1 &
```

---

## 📞 技术支持

- 适用于群晖 DSM 7.2.2
- 仅支持管理 `/var/packages/ContainerManager/etc/dockerd.json` 配置文件
- 如遇问题，请查看日志文件：
  - 运行日志：`/volume1/web_packages/ContainerManagerAddonTool/app.log`
  - 部署日志：`/volume1/web_packages/ContainerManagerAddonTool/deploy.log`

---

## 📜 许可证

本项目仅供学习交流使用，请勿用于商业用途。使用本工具造成的任何问题，作者不承担责任。

---

## 🔄 更新日志

### v1.0.0
- 初始版本发布
- 实现 dockerd.json 可视化编辑
- 支持镜像源和代理配置
- 自动备份与回滚功能
- 一键部署脚本