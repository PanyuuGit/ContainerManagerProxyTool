#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
群晖 DSM 7.2.2 Container Manager 核心配置可视化管理工具
主程序 - 提供 dockerd.json 配置的查看、编辑、备份、回滚功能
"""

import os
import sys
import json
import shutil
import subprocess
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, stream_with_context
from dotenv import load_dotenv
import threading
import queue
import time

# 加载环境变量
load_dotenv()

# ==================== 配置区域 ====================
# Flask 应用配置
app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# 路径配置
DOCKER_CORE_CONFIG = os.getenv('DOCKER_CORE_CONFIG', '/var/packages/ContainerManager/etc')
DOCKER_STATUS_CMD = os.getenv('DOCKER_STATUS_CMD', '/var/packages/ContainerManager/scripts/start-stop-status status')
PORT = int(os.getenv('PORT', 8888))

# 备份路径配置（支持相对路径，相对于脚本所在目录）
_script_dir = os.path.dirname(os.path.abspath(__file__))
BACKUP_PATH = os.getenv('BACKUP_PATH', os.path.join(_script_dir, 'backups'))
# 如果是相对路径，则相对于脚本目录
if not os.path.isabs(BACKUP_PATH):
    BACKUP_PATH = os.path.join(_script_dir, BACKUP_PATH)

# Container Manager 重启命令（从环境变量读取）
# 快速重启：systemctl restart（推荐，启动快）
# 备用重启：synopkg restart（套件异常时使用）
DOCKER_RESTART_CMD = os.getenv('DOCKER_RESTART_CMD', 'systemctl restart pkg-ContainerManager-dockerd.service')
DOCKER_RESTART_CMD_FALLBACK = os.getenv('DOCKER_RESTART_CMD_FALLBACK', 'synopkg restart ContainerManager')

# 核心配置文件路径
DOCKERD_JSON_PATH = os.path.join(DOCKER_CORE_CONFIG, 'dockerd.json')

# ==================== 全局变量 ====================
# 用于存储重启进程和日志队列
restart_process = None
log_queue = queue.Queue()
restart_running = False

# ==================== 日志配置 ====================
log_handler = logging.FileHandler('app.log', encoding='utf-8')
log_handler.setLevel(logging.INFO)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
app.logger.addHandler(log_handler)
app.logger.setLevel(logging.INFO)

# ==================== 可编辑配置项定义 ====================
# 定义 dockerd.json 中可编辑的字段及其类型
EDITABLE_FIELDS = {
    'registry-mirrors': {
        'name': '镜像源',
        'description': 'Docker 镜像加速地址，支持多个镜像源',
        'type': 'list',
        'example': '["https://docker.mirrors.ustc.edu.cn", "https://hub-mirror.c.163.com"]'
    },
    'http-proxy': {
        'name': 'HTTP 代理',
        'description': 'HTTP 代理服务器地址',
        'type': 'string',
        'example': 'http://192.168.1.1:7890'
    },
    'https-proxy': {
        'name': 'HTTPS 代理',
        'description': 'HTTPS 代理服务器地址',
        'type': 'string',
        'example': 'http://192.168.1.1:7890'
    },
    'no-proxy': {
        'name': '跳过代理',
        'description': '无需使用代理的地址列表',
        'type': 'string',
        'example': 'localhost,127.0.0.1,*.local'
    }
}

# 只读字段列表（这些字段仅展示，不可编辑）
READONLY_FIELDS = ['log-driver', 'log-opts', 'storage-driver', 'pidfile', 'data-root', 'exec-root']


# ==================== 工具函数 ====================

def check_root_permission():
    """检查是否以 root 用户运行"""
    return os.geteuid() == 0


def run_command(cmd, timeout=30):
    """
    执行系统命令并返回结果
    :param cmd: 命令字符串或列表
    :param timeout: 超时时间（秒）
    :return: (success, stdout, stderr)
    """
    try:
        result = subprocess.run(
            cmd,
            shell=isinstance(cmd, str),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', '命令执行超时'
    except Exception as e:
        return False, '', str(e)


def get_container_manager_status():
    """
    获取 Container Manager 运行状态
    使用 synopkg status ContainerManager 命令
    返回码为0表示运行中，非0表示非运行状态
    :return: dict 包含状态信息和详情
    """
    status_info = {
        'running': False,
        'status_text': '未知',
        'message': ''
    }
    
    # 使用环境变量配置的状态检测命令（synopkg status ContainerManager）
    status_cmd = DOCKER_STATUS_CMD if DOCKER_STATUS_CMD else 'synopkg status ContainerManager'
    
    success, stdout, stderr = run_command(status_cmd)
    
    # synopkg status ContainerManager 返回逻辑：
    # - 返回码为0且输出中包含"running"表示运行中
    # - 返回码非0或输出中不包含"running"表示已停止
    if success and 'running' in stdout.lower():
        status_info['running'] = True
        status_info['status_text'] = '运行中'
        status_info['message'] = stdout.strip() or 'Container Manager 正在运行'
    else:
        # 检查输出中是否有状态信息
        if stdout and 'running' not in stdout.lower():
            status_info['running'] = False
            status_info['status_text'] = '已停止'
            status_info['message'] = stdout.strip() or 'Container Manager 已停止'
        elif stderr:
            # 命令执行出错，尝试备用检测方式
            dockerd_running, dockerd_out, _ = run_command('ps aux | grep dockerd | grep -v grep')
            
            if dockerd_running and dockerd_out.strip():
                status_info['running'] = True
                status_info['status_text'] = '运行中'
                status_info['message'] = '检测到 dockerd 进程运行中'
            else:
                status_info['running'] = False
                status_info['status_text'] = '已停止'
                status_info['message'] = stderr or '无法获取 Container Manager 状态'
        else:
            # 返回码非0，服务已停止
            status_info['running'] = False
            status_info['status_text'] = '已停止'
            status_info['message'] = 'Container Manager 已停止'
    
    return status_info


def get_dockerd_json_info():
    """
    获取 dockerd.json 文件信息
    :return: dict 包含文件信息
    """
    info = {
        'exists': False,
        'path': DOCKERD_JSON_PATH,
        'last_modified': None,
        'size': 0,
        'content': None,
        'error': None
    }
    
    if not os.path.exists(DOCKERD_JSON_PATH):
        info['error'] = '配置文件不存在'
        return info
    
    try:
        stat_info = os.stat(DOCKERD_JSON_PATH)
        info['exists'] = True
        info['last_modified'] = datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        info['size'] = stat_info.st_size
        
        with open(DOCKERD_JSON_PATH, 'r', encoding='utf-8') as f:
            info['content'] = json.load(f)
    except json.JSONDecodeError as e:
        info['error'] = f'JSON 解析错误: {str(e)}'
    except Exception as e:
        info['error'] = f'读取文件失败: {str(e)}'
    
    return info


def get_backup_list():
    """
    获取备份文件列表
    :return: list 备份文件信息列表
    """
    backups = []
    
    if not os.path.exists(BACKUP_PATH):
        os.makedirs(BACKUP_PATH, exist_ok=True)
        return backups
    
    for filename in os.listdir(BACKUP_PATH):
        if filename.startswith('dockerd.json_'):
            filepath = os.path.join(BACKUP_PATH, filename)
            try:
                stat_info = os.stat(filepath)
                # 从文件名提取时间戳
                timestamp_str = filename.replace('dockerd.json_', '')
                
                backups.append({
                    'filename': filename,
                    'filepath': filepath,
                    'size': stat_info.st_size,
                    'created_time': datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'timestamp': stat_info.st_mtime
                })
            except Exception:
                continue
    
    # 按时间倒序排列
    backups.sort(key=lambda x: x['timestamp'], reverse=True)
    return backups


def get_dsm_version():
    """
    获取群晖 DSM 版本信息
    直接读取 productversion 字段
    :return: str DSM 版本号
    """
    dsm_version_file = "/etc.defaults/VERSION"
    
    if os.path.exists(dsm_version_file):
        try:
            with open(dsm_version_file, 'r') as f:
                for line in f:
                    if line.startswith('productversion='):
                        # 格式: productversion="7.2.2"
                        return line.split('"')[1]
        except Exception:
            pass
    
    return "未知"


def get_docker_version():
    """
    获取 Docker 版本信息
    :return: str Docker 版本号
    """
    success, stdout, stderr = run_command('docker --version', timeout=10)
    
    if success and stdout:
        # 输出格式: Docker version 24.0.7, build afdd53b
        # 提取版本号
        try:
            import re
            match = re.search(r'Docker version ([\d.]+)', stdout)
            if match:
                return match.group(1)
        except Exception:
            pass
    
    return "未知"


def get_port_status():
    """
    检查 8888 端口监听状态
    :return: dict 端口状态信息
    """
    port_info = {
        'port': PORT,
        'listening': False,
        'process': None
    }
    
    # 使用 netstat 或 ss 检查端口
    success, stdout, _ = run_command(f'netstat -tlnp 2>/dev/null | grep ":{PORT}" || ss -tlnp 2>/dev/null | grep ":{PORT}"')
    
    if stdout:
        port_info['listening'] = True
        port_info['process'] = stdout.split()[-1] if stdout.split() else None
    
    return port_info


def create_backup():
    """
    创建 dockerd.json 备份
    :return: (success, message, backup_path)
    """
    if not os.path.exists(DOCKERD_JSON_PATH):
        return False, '配置文件不存在，无法备份', None
    
    # 确保备份目录存在
    os.makedirs(BACKUP_PATH, exist_ok=True)
    
    # 生成备份文件名
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_filename = f'dockerd.json_{timestamp}'
    backup_filepath = os.path.join(BACKUP_PATH, backup_filename)
    
    try:
        shutil.copy2(DOCKERD_JSON_PATH, backup_filepath)
        app.logger.info(f'创建备份成功: {backup_filepath}')
        return True, '备份成功', backup_filepath
    except Exception as e:
        app.logger.error(f'创建备份失败: {str(e)}')
        return False, f'备份失败: {str(e)}', None


def restore_backup(backup_filename):
    """
    从备份恢复 dockerd.json
    :param backup_filename: 备份文件名
    :return: (success, message)
    """
    backup_filepath = os.path.join(BACKUP_PATH, backup_filename)
    
    if not os.path.exists(backup_filepath):
        return False, '备份文件不存在'
    
    try:
        # 先备份当前配置
        create_backup()
        
        # 恢复备份
        shutil.copy2(backup_filepath, DOCKERD_JSON_PATH)
        app.logger.info(f'恢复备份成功: {backup_filename}')
        return True, '恢复成功'
    except Exception as e:
        app.logger.error(f'恢复备份失败: {str(e)}')
        return False, f'恢复失败: {str(e)}'


def delete_backup(backup_filename):
    """
    删除备份文件
    :param backup_filename: 备份文件名
    :return: (success, message)
    """
    backup_filepath = os.path.join(BACKUP_PATH, backup_filename)
    
    if not os.path.exists(backup_filepath):
        return False, '备份文件不存在'
    
    try:
        os.remove(backup_filepath)
        app.logger.info(f'删除备份成功: {backup_filename}')
        return True, '删除成功'
    except Exception as e:
        app.logger.error(f'删除备份失败: {str(e)}')
        return False, f'删除失败: {str(e)}'


def validate_json_content(content):
    """
    验证 JSON 内容合法性
    :param content: JSON 字符串或字典
    :return: (valid, error_message)
    """
    try:
        if isinstance(content, str):
            json.loads(content)
        elif isinstance(content, dict):
            json.dumps(content)
        return True, None
    except json.JSONDecodeError as e:
        return False, f'JSON 格式错误: {str(e)}'


def merge_config(original_config, editable_values):
    """
    合并可编辑配置项到原始配置
    :param original_config: 原始配置字典
    :param editable_values: 可编辑项的新值字典
    :return: 合并后的配置字典
    """
    merged = original_config.copy()
    
    # 处理镜像源（list 类型）
    if 'registry-mirrors' in editable_values:
        mirrors_value = editable_values['registry-mirrors'].strip()
        if mirrors_value:
            try:
                # 尝试解析为 JSON 数组
                if mirrors_value.startswith('['):
                    merged['registry-mirrors'] = json.loads(mirrors_value)
                else:
                    # 按行或逗号分隔
                    mirrors = [m.strip() for m in mirrors_value.replace('\n', ',').split(',') if m.strip()]
                    merged['registry-mirrors'] = mirrors
            except json.JSONDecodeError:
                # 作为单个地址处理
                merged['registry-mirrors'] = [mirrors_value]
        elif 'registry-mirrors' in merged:
            # 如果新值为空，删除该配置项
            del merged['registry-mirrors']
    
    # 处理代理配置（放入 proxies 对象中）
    # 格式: "proxies": { "http-proxy": "...", "https-proxy": "...", "no-proxy": "..." }
    has_proxy_config = False
    proxies_config = {}
    
    # 检查是否有任何代理配置
    for proxy_field in ['http-proxy', 'https-proxy', 'no-proxy']:
        if proxy_field in editable_values:
            value = editable_values[proxy_field].strip()
            if value:
                proxies_config[proxy_field] = value
                has_proxy_config = True
    
    # 如果有代理配置，更新或创建 proxies 对象
    if has_proxy_config:
        # 保留原有的其他代理配置（如果存在）
        if 'proxies' in merged and isinstance(merged['proxies'], dict):
            # 合并到现有 proxies 配置
            merged['proxies'] = {**merged['proxies'], **proxies_config}
        else:
            merged['proxies'] = proxies_config
    else:
        # 如果所有代理配置都为空，删除整个 proxies 对象（可选）
        # 这里选择保留原有的 proxies 配置，不删除
        pass
    
    # 处理解锁的只读字段（以 readonly_ 开头的字段）
    for key, value in editable_values.items():
        if key.startswith('readonly_'):
            real_key = key.replace('readonly_', '')
            try:
                # 尝试解析为 JSON
                merged[real_key] = json.loads(value)
            except json.JSONDecodeError:
                # 作为字符串处理
                merged[real_key] = value
    
    return merged


def save_dockerd_json(config):
    """
    保存配置到 dockerd.json
    :param config: 配置字典
    :return: (success, message)
    """
    # 验证 JSON 格式
    valid, error = validate_json_content(config)
    if not valid:
        return False, error
    
    try:
        # 创建备份
        create_backup()
        
        # 写入配置文件
        with open(DOCKERD_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        app.logger.info('配置保存成功')
        return True, '配置保存成功'
    except Exception as e:
        app.logger.error(f'配置保存失败: {str(e)}')
        return False, f'保存失败: {str(e)}'


# ==================== 路由定义 ====================

@app.route('/')
def index():
    """首页 - 展示状态信息和备份列表"""
    # 获取 Container Manager 状态
    cm_status = get_container_manager_status()
    
    # 获取配置文件信息
    config_info = get_dockerd_json_info()
    
    # 获取备份列表
    backups = get_backup_list()
    
    # 获取端口状态
    port_status = get_port_status()
    
    # 获取 DSM 版本
    dsm_version = get_dsm_version()
    
    # 获取 Docker 版本
    docker_version = get_docker_version()
    
    return render_template('index.html',
                         cm_status=cm_status,
                         config_info=config_info,
                         backups=backups,
                         port_status=port_status,
                         dsm_version=dsm_version,
                         docker_version=docker_version,
                         editable_fields=EDITABLE_FIELDS)


@app.route('/edit')
def edit():
    """配置编辑页面"""
    # 获取当前配置
    config_info = get_dockerd_json_info()
    
    if not config_info['exists']:
        flash('配置文件不存在', 'error')
        return redirect(url_for('index'))
    
    if config_info['error']:
        flash(config_info['error'], 'error')
        return redirect(url_for('index'))
    
    # 分离可编辑和只读配置项
    config = config_info['content']
    editable_config = {}
    readonly_config = {}
    
    for key, value in config.items():
        if key in EDITABLE_FIELDS:
            editable_config[key] = value
        elif key != 'proxies':  # proxies 不显示，代理配置在可编辑项中
            readonly_config[key] = value
    
    # 处理代理配置（从 proxies 对象中读取）
    # 格式: "proxies": { "http-proxy": "...", "https-proxy": "...", "no-proxy": "..." }
    if 'proxies' in config and isinstance(config['proxies'], dict):
        for proxy_field in ['http-proxy', 'https-proxy', 'no-proxy']:
            if proxy_field in config['proxies']:
                editable_config[proxy_field] = config['proxies'][proxy_field]
    
    # proxies 不再放入只读配置中，避免与可编辑代理配置重复
    
    return render_template('edit.html',
                         editable_config=editable_config,
                         readonly_config=readonly_config,
                         editable_fields=EDITABLE_FIELDS,
                         readonly_fields=READONLY_FIELDS,
                         full_config=config)


@app.route('/api/save', methods=['POST'])
def api_save():
    """保存配置 API"""
    try:
        # 获取当前配置
        config_info = get_dockerd_json_info()
        
        if not config_info['exists']:
            return jsonify({'success': False, 'message': '配置文件不存在'})
        
        original_config = config_info['content']
        
        # 获取所有表单数据（包括可编辑字段和解锁的只读字段）
        editable_values = {}
        for field in EDITABLE_FIELDS.keys():
            editable_values[field] = request.form.get(field, '')
        
        # 获取解锁的只读字段
        for key in request.form.keys():
            if key.startswith('readonly_'):
                editable_values[key] = request.form.get(key, '')
        
        # 合并配置
        merged_config = merge_config(original_config, editable_values)
        
        # 保存配置
        success, message = save_dockerd_json(merged_config)
        
        return jsonify({'success': success, 'message': message})
    
    except Exception as e:
        app.logger.error(f'保存配置异常: {str(e)}')
        return jsonify({'success': False, 'message': f'保存异常: {str(e)}'})


@app.route('/api/backup', methods=['POST'])
def api_backup():
    """创建备份 API"""
    success, message, backup_path = create_backup()
    return jsonify({'success': success, 'message': message, 'backup_path': backup_path})


@app.route('/api/restore', methods=['POST'])
def api_restore():
    """恢复备份 API"""
    backup_filename = request.form.get('backup_filename', '')
    
    if not backup_filename:
        return jsonify({'success': False, 'message': '未指定备份文件'})
    
    success, message = restore_backup(backup_filename)
    return jsonify({'success': success, 'message': message})


@app.route('/api/delete_backup', methods=['POST'])
def api_delete_backup():
    """删除备份 API"""
    backup_filename = request.form.get('backup_filename', '')
    
    if not backup_filename:
        return jsonify({'success': False, 'message': '未指定备份文件'})
    
    success, message = delete_backup(backup_filename)
    return jsonify({'success': success, 'message': message})


@app.route('/api/preview_backup', methods=['POST'])
def api_preview_backup():
    """预览备份文件内容 API"""
    backup_filename = request.form.get('backup_filename', '')
    
    if not backup_filename:
        return jsonify({'success': False, 'message': '未指定备份文件'})
    
    backup_filepath = os.path.join(BACKUP_PATH, backup_filename)
    
    if not os.path.exists(backup_filepath):
        return jsonify({'success': False, 'message': '备份文件不存在'})
    
    try:
        with open(backup_filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 尝试格式化 JSON
        try:
            json_content = json.loads(content)
            formatted_content = json.dumps(json_content, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            formatted_content = content
        
        app.logger.info(f'预览备份文件: {backup_filename}')
        return jsonify({'success': True, 'content': formatted_content})
    except Exception as e:
        app.logger.error(f'预览备份文件失败: {str(e)}')
        return jsonify({'success': False, 'message': f'读取失败: {str(e)}'})


@app.route('/api/restart', methods=['POST'])
def api_restart():
    """重启 Container Manager API（快速重启，使用systemctl）"""
    global restart_running, restart_process
    
    if restart_running:
        return jsonify({'success': False, 'message': '重启进程正在进行中'})
    
    # 清空日志队列
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break
    
    # 启动后台重启线程（使用快速重启命令）
    restart_running = True
    thread = threading.Thread(target=do_restart_background, args=(DOCKER_RESTART_CMD,))
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'message': '重启进程已启动'})


@app.route('/api/restart_fallback', methods=['POST'])
def api_restart_fallback():
    """备用重启 Container Manager API（套件异常时使用synopkg重启）"""
    global restart_running, restart_process
    
    if restart_running:
        return jsonify({'success': False, 'message': '重启进程正在进行中'})
    
    # 清空日志队列
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break
    
    # 启动后台重启线程（使用备用重启命令）
    restart_running = True
    thread = threading.Thread(target=do_restart_background, args=(DOCKER_RESTART_CMD_FALLBACK,))
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'message': '备用重启进程已启动'})


# 全局 journalctl 监控进程
journalctl_process = None


def start_log_monitor():
    """
    启动日志监控进程（使用 journalctl 监控 Container Manager 服务日志）
    直接使用 subprocess.Popen 实时读取输出
    :return: process 对象
    """
    global journalctl_process
    
    # 先强杀所有可能存在的 journalctl 进程，确保干净启动
    subprocess.run('pkill -9 -f "journalctl.*pkg-ContainerManager" 2>/dev/null', shell=True)
    
    # 如果已有进程对象，清理引用
    if journalctl_process:
        journalctl_process = None
    
    # 启动 journalctl 进程，实时读取输出
    # -f 跟踪新日志
    journalctl_process = subprocess.Popen(
        ['journalctl', '-u', 'pkg-ContainerManager-dockerd.service', '-f'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1  # 行缓冲
    )
    
    app.logger.info(f'启动 journalctl 监控进程: PID {journalctl_process.pid}')
    return journalctl_process


def stop_log_monitor():
    """
    停止日志监控进程（使用 pkill -9 强制终止）
    """
    global journalctl_process
    
    # 先尝试正常终止
    if journalctl_process:
        try:
            journalctl_process.terminate()
            journalctl_process.wait(timeout=2)
        except:
            pass
        journalctl_process = None
    
    # 强制杀掉所有 journalctl 进程（确保清理干净）
    subprocess.run('pkill -9 -f "journalctl.*pkg-ContainerManager" 2>/dev/null', shell=True)
    app.logger.info('已强制停止日志监控进程')


def read_journalctl_output():
    """
    非阻塞读取 journalctl 进程的输出
    :return: 新日志行（如果有）
    """
    global journalctl_process
    
    if not journalctl_process:
        return None
    
    try:
        import select
        # 使用 select 检查是否有数据可读
        ready, _, _ = select.select([journalctl_process.stdout], [], [], 0.1)
        if ready:
            line = journalctl_process.stdout.readline()
            if line:
                return line.strip()
    except Exception as e:
        app.logger.error(f'读取 journalctl 输出失败: {str(e)}')
    
    return None


def do_restart_background(restart_cmd=None):
    """
    后台执行重启Container Manager
    使用 synopkg status ContainerManager 检测状态（返回码0表示运行中）
    使用 journalctl 实时监控服务日志
    :param restart_cmd: 重启命令，默认使用 DOCKER_RESTART_CMD
    """
    global restart_running
    
    if restart_cmd is None:
        restart_cmd = DOCKER_RESTART_CMD
    
    try:
        # 步骤1: 启动日志监控
        start_log_monitor()
        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 已启动 journalctl 日志监控\n")
        
        # 步骤2: 执行 systemctl daemon-reload（重载 systemd 配置）
        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 执行: systemctl daemon-reload\n")
        daemon_reload_result = subprocess.run(
            'systemctl daemon-reload',
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        if daemon_reload_result.returncode == 0:
            log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] daemon-reload 完成\n")
        else:
            log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] daemon-reload 警告: {daemon_reload_result.stderr}\n")
        
        # 步骤3: 执行重启命令
        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 执行: {restart_cmd}\n")
        restart_process = subprocess.Popen(
            restart_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # 步骤3: 等待重启命令执行完成
        log_queue.put(f"\n[{datetime.now().strftime('%H:%M:%S')}] 等待重启命令执行...\n")
        
        # 等待重启命令返回
        restart_wait = 0
        while restart_wait < 30 and restart_process.poll() is None:
            time.sleep(0.5)
            restart_wait += 0.5
            # 读取 journalctl 输出
            journal_line = read_journalctl_output()
            while journal_line:
                log_queue.put(f"[journalctl] {journal_line}\n")
                journal_line = read_journalctl_output()
        
        # 获取重启命令的输出
        if restart_process.poll() is not None:
            stdout, stderr = restart_process.communicate()
            if stdout:
                log_queue.put(f"[命令输出] {stdout}\n")
            if stderr:
                log_queue.put(f"[命令错误] {stderr}\n")
        
        log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 重启命令已发送\n")
        
        # 步骤4: 持续监控直到 synopkg status ContainerManager 返回码为0
        # synopkg status 返回码：0=运行中，非0=非运行状态
        log_queue.put(f"\n[{datetime.now().strftime('%H:%M:%S')}] 开始监控服务状态...\n")
        
        # 等待一段时间让服务开始重启过程
        time.sleep(3)
        
        # 持续监控，不限时，直到返回码为0
        check_count = 0
        while True:
            check_count += 1
            
            # 执行 synopkg status ContainerManager 检测状态
            status_cmd = DOCKER_STATUS_CMD if DOCKER_STATUS_CMD else 'synopkg status ContainerManager'
            success, stdout, stderr = run_command(status_cmd)
            
            # 读取 journalctl 输出
            journal_line = read_journalctl_output()
            while journal_line:
                log_queue.put(f"[journalctl] {journal_line}\n")
                journal_line = read_journalctl_output()
            
            # 日志输出：每5次检测输出一次详细信息
            if check_count % 5 == 0:
                status_text = "运行中 (返回码=0)" if success else f"非运行状态 (返回码非0)"
                log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 状态检测 #{check_count}: {status_text}\n")
                if stdout:
                    log_queue.put(f"  输出: {stdout}\n")
            
            # 检测成功条件：返回码为0且输出包含running
            if success and 'running' in stdout.lower():
                # 停止日志监控进程
                stop_log_monitor()
                log_queue.put(f"\n[{datetime.now().strftime('%H:%M:%S')}] ✓ Container Manager 重启成功！\n")
                log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 状态: 运行中 (返回码=0)\n")
                log_queue.put("[DONE]")
                return
            
            # 等待1秒后继续检测
            time.sleep(1)
        
    except Exception as e:
        # 确保停止监控进程
        stop_log_monitor()
        log_queue.put(f"\n[ERROR] 重启异常: {str(e)}\n")
        log_queue.put("[DONE]")
    finally:
        restart_running = False


@app.route('/api/restart_stream')
def api_restart_stream():
    """SSE流式返回重启日志"""
    def generate():
        while True:
            try:
                # 从队列获取日志
                log_line = log_queue.get(timeout=1)
                
                # 检查是否结束
                if log_line == "[DONE]":
                    yield f"data: [DONE]\n\n"
                    break
                
                # 发送日志行
                yield f"data: {log_line}\n\n"
            except queue.Empty:
                # 队列为空，发送心跳
                yield f": heartbeat\n\n"
            except GeneratorExit:
                # 客户端断开连接
                break
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@app.route('/api/stop_restart', methods=['POST'])
def api_stop_restart():
    """停止重启监控"""
    global restart_running
    
    # 清空日志队列
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break
    
    log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] 用户手动停止监控\n")
    log_queue.put("[DONE]")
    
    return jsonify({'success': True, 'message': '已停止监控'})


@app.route('/api/cm_status')
def api_cm_status():
    """获取 Container Manager 实时状态（轻量API）"""
    return jsonify(get_container_manager_status())


@app.route('/api/test_mirror', methods=['POST'])
def api_test_mirror():
    """测试镜像源连接性"""
    mirror_url = request.form.get('mirror_url', '').strip()
    
    if not mirror_url:
        return jsonify({'success': False, 'message': '镜像地址为空'})
    
    # 移除末尾斜杠
    mirror_url = mirror_url.rstrip('/')
    
    try:
        import urllib.request
        import socket
        
        # 设置超时5秒
        socket.setdefaulttimeout(5)
        
        # 测试访问 v2 API
        test_url = f"{mirror_url}/v2/"
        
        try:
            req = urllib.request.Request(test_url, method='GET')
            req.add_header('User-Agent', 'Docker-Client/1.0')
            response = urllib.request.urlopen(req, timeout=5)
            status_code = response.getcode()
            
            if status_code == 200 or status_code == 401:
                # 200表示可访问，401表示需要认证（也是正常的）
                return jsonify({
                    'success': True, 
                    'status': 'ok',
                    'message': '连接成功',
                    'latency': 0
                })
            else:
                return jsonify({
                    'success': False, 
                    'status': 'error',
                    'message': f'HTTP {status_code}'
                })
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return jsonify({
                    'success': True, 
                    'status': 'ok',
                    'message': '连接成功（需认证）'
                })
            return jsonify({
                'success': False, 
                'status': 'error',
                'message': f'HTTP {e.code}'
            })
        except urllib.error.URLError as e:
            return jsonify({
                'success': False, 
                'status': 'error',
                'message': f'连接失败: {str(e.reason)}'
            })
        except socket.timeout:
            return jsonify({
                'success': False, 
                'status': 'timeout',
                'message': '连接超时'
            })
    except Exception as e:
        return jsonify({
            'success': False, 
            'status': 'error',
            'message': f'测试异常: {str(e)}'
        })


@app.route('/api/test_proxy', methods=['POST'])
def api_test_proxy():
    """测试代理连接性"""
    proxy_url = request.form.get('proxy_url', '').strip()
    proxy_type = request.form.get('proxy_type', 'http')  # http 或 https
    
    if not proxy_url:
        return jsonify({'success': False, 'message': '代理地址为空'})
    
    try:
        import urllib.request
        import socket
        
        socket.setdefaulttimeout(10)
        
        # 解析代理地址
        if not proxy_url.startswith('http://') and not proxy_url.startswith('https://'):
            proxy_url = f'http://{proxy_url}'
        
        # 设置代理
        proxy_handler = urllib.request.ProxyHandler({
            'http': proxy_url,
            'https': proxy_url
        })
        opener = urllib.request.build_opener(proxy_handler)
        
        # 测试访问一个简单的URL
        test_url = 'https://www.google.com' if proxy_type == 'https' else 'http://www.google.com'
        
        start_time = time.time()
        try:
            req = urllib.request.Request(test_url, method='HEAD')
            req.add_header('User-Agent', 'Mozilla/5.0')
            response = opener.open(req, timeout=10)
            latency = int((time.time() - start_time) * 1000)
            
            return jsonify({
                'success': True,
                'status': 'ok',
                'message': f'连接成功 ({latency}ms)',
                'latency': latency
            })
        except urllib.error.URLError as e:
            return jsonify({
                'success': False,
                'status': 'error',
                'message': f'连接失败: {str(e.reason)}'
            })
        except socket.timeout:
            return jsonify({
                'success': False,
                'status': 'timeout',
                'message': '连接超时'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'error',
            'message': f'测试异常: {str(e)}'
        })


@app.route('/api/status')
def api_status():
    """获取状态 API"""
    return jsonify({
        'cm_status': get_container_manager_status(),
        'config_info': {
            'exists': os.path.exists(DOCKERD_JSON_PATH),
            'last_modified': datetime.fromtimestamp(os.stat(DOCKERD_JSON_PATH).st_mtime).strftime('%Y-%m-%d %H:%M:%S') if os.path.exists(DOCKERD_JSON_PATH) else None
        },
        'backup_count': len(get_backup_list()),
        'port_status': get_port_status()
    })


# ==================== 错误处理 ====================

@app.errorhandler(404)
def not_found(error):
    """404 错误处理"""
    return render_template('index.html', error='页面不存在'), 404


@app.errorhandler(500)
def internal_error(error):
    """500 错误处理"""
    app.logger.error(f'服务器错误: {str(error)}')
    return render_template('index.html', error='服务器内部错误'), 500


# ==================== 主程序入口 ====================

if __name__ == '__main__':
    # 检查 root 权限
    if not check_root_permission():
        print('警告: 建议以 root 用户运行此程序，否则可能无法正常操作配置文件')
    
    # 确保必要目录存在
    os.makedirs(BACKUP_PATH, exist_ok=True)
    
    # 启动 Flask 应用
    print(f'Container Manager 配置管理工具启动中...')
    print(f'访问地址: http://0.0.0.0:{PORT}')
    print(f'配置文件: {DOCKERD_JSON_PATH}')
    print(f'备份目录: {BACKUP_PATH}')
    
    app.logger.info(f'服务启动，监听端口: {PORT}')
    
    # 生产环境配置
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,  # 生产环境禁用调试模式
        threaded=True
    )