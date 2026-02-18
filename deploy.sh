#!/bin/bash
# ============================================================================
# 群晖 DSM 7.2.2 Container Manager 配置管理工具 - 一键部署脚本
# 功能：环境检查、依赖安装、文件部署、服务管理
# 作者：Container Manager 配置管理工具
# 版本：1.0.0
# ============================================================================

# ==================== 全局变量定义 ====================
# 工具部署目录（自动检测脚本所在目录，支持任意位置部署）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${SCRIPT_DIR}"

# 配置文件路径
DOCKERD_JSON="/var/packages/ContainerManager/etc/dockerd.json"
DOCKER_STATUS_CMD="/var/packages/ContainerManager/scripts/start-stop-status"

# 服务端口
SERVICE_PORT=8888

# 日志文件
DEPLOY_LOG="${INSTALL_DIR}/deploy.log"

# Python 依赖
PYTHON_DEPS="flask python-dotenv json5"

# 镜像源（清华源加速）
PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # 无颜色

# ==================== 日志函数 ====================
log() {
    local level=$1
    local message=$2
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "[${timestamp}] [${level}] ${message}" >> "${DEPLOY_LOG}" 2>/dev/null
}

log_info() {
    log "INFO" "$1"
    echo -e "${BLUE}[信息]${NC} $1"
}

log_success() {
    log "SUCCESS" "$1"
    echo -e "${GREEN}[成功]${NC} $1"
}

log_warning() {
    log "WARNING" "$1"
    echo -e "${YELLOW}[警告]${NC} $1"
}

log_error() {
    log "ERROR" "$1"
    echo -e "${RED}[错误]${NC} $1"
}

# ==================== 环境检查函数 ====================

# 检查 root 权限
check_root() {
    log_info "检查 root 权限..."
    if [ "$(id -u)" -ne 0 ]; then
        log_error "请使用 root 用户运行此脚本！"
        log_error "建议使用: sudo $0 $1"
        exit 1
    fi
    log_success "Root 权限检查通过"
}

# 检查 DSM 版本
check_dsm_version() {
    log_info "检查 DSM 版本..."
    
    # 获取 DSM 版本信息
    local dsm_version_file="/etc.defaults/VERSION"
    
    if [ -f "$dsm_version_file" ]; then
        # 直接读取 productversion
        local dsm_version=$(grep "^productversion=" "$dsm_version_file" | cut -d'"' -f2)
        
        log_info "当前 DSM 版本: ${dsm_version}"
        
        # 检查是否为 7.2.2 版本
        if [ "$dsm_version" = "7.2.2" ]; then
            log_success "DSM 版本匹配: 7.2.2"
        else
            log_warning "DSM 版本为 ${dsm_version}，推荐使用 DSM 7.2.2"
            log_warning "工具可能仍可正常工作，但未经完整测试"
        fi
    else
        log_warning "无法获取 DSM 版本信息，跳过版本检查"
    fi
}

# 检查 Python 3
check_python() {
    log_info "检查 Python 3 环境..."
    
    if command -v python3 &> /dev/null; then
        local python_version=$(python3 --version 2>&1)
        log_success "Python 已安装: ${python_version}"
        return 0
    else
        log_error "Python 3 未安装！"
        log_info "群晖通常自带 Python，请检查系统环境"
        log_info "或通过套件中心安装 Python 3"
        return 1
    fi
}

# 检查 pip
check_pip() {
    log_info "检查 pip..."
    
    if python3 -m pip --version &> /dev/null; then
        local pip_version=$(python3 -m pip --version 2>&1)
        log_success "pip 已安装: ${pip_version}"
        return 0
    else
        log_warning "pip 未安装，尝试安装..."
        # 尝试通过 ensurepip 安装
        if python3 -m ensurepip --default-pip &> /dev/null; then
            log_success "pip 安装成功"
            return 0
        else
            log_error "pip 安装失败，请手动安装 pip"
            return 1
        fi
    fi
}

# 检查端口占用
check_port() {
    log_info "检查端口 ${SERVICE_PORT} 占用情况..."
    
    local port_status=$(netstat -tlnp 2>/dev/null | grep ":${SERVICE_PORT}" || ss -tlnp 2>/dev/null | grep ":${SERVICE_PORT}")
    
    if [ -n "$port_status" ]; then
        log_warning "端口 ${SERVICE_PORT} 已被占用:"
        echo "$port_status"
        log_warning "请先停止占用该端口的服务，或修改 .env 文件中的端口配置"
        return 1
    else
        log_success "端口 ${SERVICE_PORT} 可用"
        return 0
    fi
}

# 检查 Container Manager
check_container_manager() {
    log_info "检查 Container Manager 安装状态..."
    
    if [ -d "/var/packages/ContainerManager" ]; then
        log_success "Container Manager 已安装"
        
        if [ -f "$DOCKER_STATUS_CMD" ]; then
            log_success "Container Manager 状态脚本存在"
            return 0
        else
            log_error "Container Manager 状态脚本不存在"
            log_error "路径: ${DOCKER_STATUS_CMD}"
            return 1
        fi
    else
        log_error "Container Manager 未安装！"
        log_error "请先在套件中心安装 Container Manager"
        return 1
    fi
}

# ==================== 依赖安装函数 ====================

# 升级 pip
upgrade_pip() {
    log_info "升级 pip..."
    
    if python3 -m pip install --upgrade pip -i "$PIP_INDEX" &>> "${DEPLOY_LOG}"; then
        log_success "pip 升级成功"
        return 0
    else
        log_warning "pip 升级失败，继续使用当前版本"
        return 0
    fi
}

# 安装 Python 依赖
install_dependencies() {
    log_info "安装 Python 依赖: ${PYTHON_DEPS}"
    
    if python3 -m pip install $PYTHON_DEPS -i "$PIP_INDEX" &>> "${DEPLOY_LOG}"; then
        log_success "Python 依赖安装成功"
        return 0
    else
        log_error "Python 依赖安装失败！"
        log_error "请手动执行: python3 -m pip install ${PYTHON_DEPS} -i ${PIP_INDEX}"
        return 1
    fi
}

# ==================== 文件部署函数 ====================

# 创建目录结构
create_directories() {
    log_info "创建目录结构..."
    
    # 创建主目录
    mkdir -p "${INSTALL_DIR}"
    
    # 创建子目录
    mkdir -p "${INSTALL_DIR}/templates"
    mkdir -p "${INSTALL_DIR}/backups"
    
    # 设置权限
    chmod -R 755 "${INSTALL_DIR}"
    
    log_success "目录创建成功: ${INSTALL_DIR}"
}

# 生成 .env 配置文件
generate_env_file() {
    log_info "生成 .env 配置文件..."
    
    local env_file="${INSTALL_DIR}/.env"
    
    cat > "$env_file" << EOF
# Container Manager 配置管理工具 - 环境配置
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')

# Flask 应用配置
FLASK_APP=app.py
FLASK_ENV=production

# 服务端口
PORT=${SERVICE_PORT}

# Docker 核心配置目录
DOCKER_CORE_CONFIG=/var/packages/ContainerManager/etc

# 备份目录（绝对路径）
BACKUP_PATH=/volume1/web_packages/ContainerManagerAddonTool/backups

# Container Manager 状态查询命令
DOCKER_STATUS_CMD=synopkg status ContainerManager
# Container Manager 重启命令（快速重启）
DOCKER_RESTART_CMD=systemctl restart pkg-ContainerManager-dockerd.service
# Container Manager 重启命令（套件异常时使用）
DOCKER_RESTART_CMD_FALLBACK=synopkg restart ContainerManager
EOF
    
    log_success ".env 配置文件生成成功"
}

# 部署应用文件
deploy_app_files() {
    log_info "部署应用文件..."
    
    # 获取脚本所在目录（用于复制文件）
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    
    # 检查 app.py 是否存在
    if [ -f "${script_dir}/app.py" ]; then
        cp "${script_dir}/app.py" "${INSTALL_DIR}/"
        log_success "app.py 部署成功"
    elif [ -f "./app.py" ]; then
        cp "./app.py" "${INSTALL_DIR}/"
        log_success "app.py 部署成功"
    else
        log_error "找不到 app.py 文件！"
        return 1
    fi
    
    # 检查 templates 目录
    if [ -d "${script_dir}/templates" ]; then
        cp -r "${script_dir}/templates/"* "${INSTALL_DIR}/templates/"
        log_success "templates 目录部署成功"
    elif [ -d "./templates" ]; then
        cp -r "./templates/"* "${INSTALL_DIR}/templates/"
        log_success "templates 目录部署成功"
    else
        log_error "找不到 templates 目录！"
        return 1
    fi
    
    # 检查 .env 文件
    if [ ! -f "${INSTALL_DIR}/.env" ]; then
        generate_env_file
    fi
    
    # 设置权限
    chmod 755 "${INSTALL_DIR}/app.py"
    
    return 0
}

# ==================== 服务管理函数 ====================

# 获取服务 PID（返回所有匹配的 PID）
get_service_pid() {
    # 查找运行 app.py 的 Python 进程（兼容群晖 DSM）
    # 方式1: 使用 pgrep 查找所有匹配进程
    local pids=$(pgrep -f "python3.*app.py" 2>/dev/null)
    
    # 方式2: 如果 pgrep 没找到，使用 ps aux
    if [ -z "$pids" ]; then
        pids=$(ps aux 2>/dev/null | grep "python3" | grep "app.py" | grep -v grep | awk '{print $2}')
    fi
    
    # 方式3: 检查端口监听的进程
    if [ -z "$pids" ]; then
        pids=$(netstat -tlnp 2>/dev/null | grep ":${SERVICE_PORT}" | awk '{print $7}' | cut -d'/' -f1 | grep -v '-')
    fi
    
    echo "$pids"
}

# 获取单个 PID（用于显示）
get_single_pid() {
    local pids=$(get_service_pid)
    echo "$pids" | awk '{print $1}'
}

# 启动服务
start_service() {
    log_info "启动服务..."
    
    # 检查服务是否已运行
    local pid=$(get_service_pid)
    if [ -n "$pid" ]; then
        log_warning "服务已在运行中 (PID: ${pid})"
        return 0
    fi
    
    # 检查端口
    if ! check_port; then
        return 1
    fi
    
    # 进入工作目录
    cd "${INSTALL_DIR}"
    
    # 后台启动服务
    nohup python3 app.py > /dev/null 2>&1 &
    
    sleep 2
    
    # 检查服务是否启动成功
    pid=$(get_service_pid)
    if [ -n "$pid" ]; then
        log_success "服务启动成功 (PID: ${pid})"
        # 获取群晖 IP（兼容群晖 DSM）
        local ip=$(ip addr 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | cut -d'/' -f1)
        if [ -z "$ip" ]; then
            ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
        fi
        if [ -z "$ip" ]; then
            ip=$(hostname -i 2>/dev/null | awk '{print $1}')
        fi
        log_success "访问地址: http://${ip}:${SERVICE_PORT}"
    else
        log_error "服务启动失败，请检查日志: ${INSTALL_DIR}/app.log"
        return 1
    fi
}

# 停止服务
stop_service() {
    log_info "停止服务..."
    
    local pids=$(get_service_pid)
    
    if [ -z "$pids" ]; then
        log_warning "服务未运行"
        return 0
    fi
    
    # 显示将要停止的进程
    log_info "发现进程: ${pids}"
    
    # 发送 SIGTERM 信号（优雅停止所有匹配进程）
    for pid in $pids; do
        if kill -0 "$pid" 2>/dev/null; then
            log_info "发送 SIGTERM 到进程 ${pid}..."
            kill "$pid" 2>/dev/null
        fi
    done
    
    sleep 2
    
    # 检查是否停止成功
    pids=$(get_service_pid)
    if [ -z "$pids" ]; then
        log_success "服务已停止"
        return 0
    fi
    
    # 仍有残留进程，强制终止
    log_warning "发现残留进程，强制终止..."
    for pid in $pids; do
        if kill -0 "$pid" 2>/dev/null; then
            log_info "强制终止进程 ${pid}..."
            kill -9 "$pid" 2>/dev/null
        fi
    done
    
    sleep 1
    
    # 最终检查
    pids=$(get_service_pid)
    if [ -z "$pids" ]; then
        log_success "服务已完全停止"
    else
        log_error "无法停止以下进程: ${pids}"
        log_error "请手动检查进程状态"
        return 1
    fi
}

# 重启服务
restart_service() {
    log_info "重启服务..."
    stop_service
    sleep 1
    start_service
}

# 查看服务状态
status_service() {
    log_info "查询服务状态..."
    
    local pids=$(get_service_pid)
    local pid=$(get_single_pid)
    
    if [ -n "$pids" ]; then
        log_success "服务运行中 (PID: ${pids})"
        
        # 显示服务详情
        echo ""
        echo "=================================="
        echo "服务状态详情"
        echo "=================================="
        echo "工作目录: ${INSTALL_DIR}"
        echo "服务端口: ${SERVICE_PORT}"
        echo "进程 PID: ${pids}"
        echo "配置文件: ${INSTALL_DIR}/.env"
        echo "应用日志: ${INSTALL_DIR}/app.log"
        echo "部署日志: ${INSTALL_DIR}/deploy.log"
        echo "备份目录: ${INSTALL_DIR}/backups"
        
        # 获取群晖 IP（兼容群晖 DSM，hostname 不支持 -I 选项）
        local ip=$(ip addr 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | cut -d'/' -f1)
        if [ -z "$ip" ]; then
            ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
        fi
        if [ -z "$ip" ]; then
            ip=$(hostname -i 2>/dev/null | awk '{print $1}')
        fi
        echo ""
        echo "访问地址: http://${ip}:${SERVICE_PORT}"
        echo "=================================="
    else
        log_warning "服务未运行"
        echo ""
        echo "启动服务: $0 start"
    fi
}

# 卸载工具
uninstall_tool() {
    log_info "卸载工具..."
    
    # 确认卸载
    echo -e "${YELLOW}警告: 即将卸载 Container Manager 配置管理工具${NC}"
    echo -e "${YELLOW}备份文件将保留在: ${INSTALL_DIR}/backups${NC}"
    read -p "确认卸载？(y/N): " confirm
    
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        log_info "取消卸载"
        return 0
    fi
    
    # 停止服务
    stop_service
    
    # 询问是否删除备份
    echo ""
    read -p "是否同时删除备份文件？(y/N): " delete_backups
    
    if [ "$delete_backups" = "y" ] || [ "$delete_backups" = "Y" ]; then
        rm -rf "${INSTALL_DIR}"
        log_success "工具已完全卸载（包含备份）"
    else
        # 保留备份目录
        mkdir -p "/tmp/cm_backups_$(date +%Y%m%d)"
        cp -r "${INSTALL_DIR}/backups/"* "/tmp/cm_backups_$(date +%Y%m%d)/" 2>/dev/null
        rm -rf "${INSTALL_DIR}"
        log_success "工具已卸载，备份已保存到: /tmp/cm_backups_$(date +%Y%m%d)/"
    fi
}

# ==================== 安装流程 ====================

# 执行完整安装
do_install() {
    log_info "开始安装 Container Manager 配置管理工具..."
    echo ""
    echo "=================================="
    echo " Container Manager 配置管理工具 "
    echo " 一键安装脚本 v1.0.0            "
    echo "=================================="
    echo ""
    
    # 1. 检查 root 权限
    check_root "install"
    
    # 2. 检查 DSM 版本
    check_dsm_version
    
    # 3. 检查 Container Manager
    if ! check_container_manager; then
        exit 1
    fi
    
    # 4. 检查 Python
    if ! check_python; then
        exit 1
    fi
    
    # 5. 检查 pip
    if ! check_pip; then
        exit 1
    fi
    
    # 6. 升级 pip
    upgrade_pip
    
    # 7. 安装依赖
    if ! install_dependencies; then
        exit 1
    fi
    
    # 8. 创建目录
    create_directories
    
    # 9. 部署文件
    if ! deploy_app_files; then
        exit 1
    fi
    
    # 10. 检查端口
    check_port
    
    # 11. 启动服务
    start_service
    
    echo ""
    log_success "=================================="
    log_success " 安装完成！"
    log_success "=================================="
    echo ""
    # 获取群晖 IP（兼容群晖 DSM）
    local ip=$(ip addr 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}' | cut -d'/' -f1)
    if [ -z "$ip" ]; then
        ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
    fi
    if [ -z "$ip" ]; then
        ip=$(hostname -i 2>/dev/null | awk '{print $1}')
    fi
    echo "访问地址: http://${ip}:${SERVICE_PORT}"
    echo ""
    echo "常用命令:"
    echo "  查看状态: $0 status"
    echo "  重启服务: $0 restart"
    echo "  停止服务: $0 stop"
    echo "  卸载工具: $0 uninstall"
    echo ""
}

# ==================== 主函数 ====================

main() {
    local action=$1
    
    # 初始化日志目录
    mkdir -p "${INSTALL_DIR}"
    touch "${DEPLOY_LOG}" 2>/dev/null
    
    case "$action" in
        install)
            do_install
            ;;
        start)
            check_root "start"
            start_service
            ;;
        stop)
            check_root "stop"
            stop_service
            ;;
        restart)
            check_root "restart"
            restart_service
            ;;
        status)
            status_service
            ;;
        uninstall)
            check_root "uninstall"
            uninstall_tool
            ;;
        *)
            echo "Container Manager 配置管理工具 - 部署脚本"
            echo ""
            echo "用法: $0 {install|start|stop|restart|status|uninstall}"
            echo ""
            echo "命令说明:"
            echo "  install    - 一键安装（环境检查 → 依赖安装 → 文件部署 → 启动服务）"
            echo "  start      - 启动服务"
            echo "  stop       - 停止服务"
            echo "  restart    - 重启服务"
            echo "  status     - 查看服务状态"
            echo "  uninstall  - 卸载工具（停止服务 → 删除目录 → 保留备份）"
            echo ""
            echo "示例:"
            echo "  $0 install    # 首次安装"
            echo "  $0 status     # 查看状态"
            echo "  $0 restart    # 重启服务"
            echo ""
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"