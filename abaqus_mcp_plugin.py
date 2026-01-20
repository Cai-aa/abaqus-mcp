# -*- coding: utf-8 -*-
"""
Abaqus MCP Plugin v3.1 - 文件 IPC 版本

使用文件进行进程间通信，最可靠的方案。

使用方法：
1. File -> Run Script... -> 选择此文件
2. 运行 mcp_loop() 持续处理命令
3. 运行 mcp_stop() 停止循环（或在另一个终端运行 stop_mcp.py）
"""

import os
import json
import time
import traceback
from datetime import datetime

# 尝试导入 Abaqus 模块
try:
    from abaqus import mdb, session
    ABAQUS_AVAILABLE = True
except ImportError:
    ABAQUS_AVAILABLE = False

# 配置
MCP_HOME = os.path.join(os.path.expanduser('~'), '.abaqus-mcp')
COMMANDS_DIR = os.path.join(MCP_HOME, 'commands')
RESULTS_DIR = os.path.join(MCP_HOME, 'results')
SCRIPTS_DIR = os.path.join(MCP_HOME, 'scripts')
STATUS_FILE = os.path.join(MCP_HOME, 'status.json')
STOP_FILE = os.path.join(MCP_HOME, 'stop.flag')


def ensure_dirs():
    for d in [COMMANDS_DIR, RESULTS_DIR, SCRIPTS_DIR]:
        if not os.path.exists(d):
            os.makedirs(d)


def write_status(status, message=""):
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump({
                "status": status,
                "message": message,
                "timestamp": time.time(),
                "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pid": os.getpid()
            }, f, indent=2)
    except:
        pass


def execute_script(script_content, script_id):
    result = {
        "id": script_id,
        "success": False,
        "output": "",
        "error": None,
        "timestamp": time.time()
    }
    
    script_path = os.path.join(SCRIPTS_DIR, "script_" + script_id + ".py")
    try:
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
    except Exception as e:
        result["error"] = str(e)
        return result
    
    exec_globals = {'__name__': '__main__', '__file__': script_path}
    try:
        from abaqus import mdb, session
        exec_globals['mdb'] = mdb
        exec_globals['session'] = session
    except:
        pass
    
    output_lines = []
    exec_globals['print'] = lambda *a, **k: output_lines.append(' '.join(str(x) for x in a))
    
    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            exec(compile(f.read(), script_path, 'exec'), exec_globals)
        result["success"] = True
        result["output"] = '\n'.join(output_lines)
    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
    
    try:
        os.remove(script_path)
    except:
        pass
    
    return result


def get_model_info():
    info = {"models": [], "working_directory": os.getcwd()}
    try:
        from abaqus import mdb, session
        for name in mdb.models.keys():
            m = mdb.models[name]
            info["models"].append({
                "name": name,
                "parts": list(m.parts.keys()) if hasattr(m, 'parts') else [],
                "materials": list(m.materials.keys()) if hasattr(m, 'materials') else [],
                "steps": list(m.steps.keys()) if hasattr(m, 'steps') else [],
            })
        if hasattr(session, 'viewports'):
            info["current_viewport"] = session.currentViewportName
    except Exception as e:
        info["error"] = str(e)
    return info


def process_command(command):
    cmd_id = command.get('id', 'unknown')
    cmd_type = command.get('type', 'unknown')
    result = {"id": cmd_id, "success": False, "timestamp": time.time()}
    
    try:
        if cmd_type == 'execute_script':
            result = execute_script(command.get('script', ''), cmd_id)
        elif cmd_type == 'get_model_info':
            result["success"] = True
            result["data"] = get_model_info()
        elif cmd_type == 'get_message_log':
            result["success"] = True
            result["data"] = "Log not available"
        elif cmd_type == 'ping':
            result["success"] = True
            result["data"] = "pong"
        elif cmd_type == 'stop':
            result["success"] = True
            result["data"] = "stopping"
            # 创建停止标志
            with open(STOP_FILE, 'w') as f:
                f.write('stop')
        else:
            result["error"] = "Unknown command: " + cmd_type
    except Exception as e:
        result["error"] = str(e)
    
    return result


def poll_once():
    """
    处理一个待处理的命令
    
    返回: True 如果处理了命令，False 如果没有命令
    """
    try:
        # 获取命令文件列表
        cmd_files = [f for f in os.listdir(COMMANDS_DIR) if f.endswith('.json')]
        if not cmd_files:
            return False
        
        # 按时间排序，处理最早的命令
        cmd_files.sort()
        cmd_file = cmd_files[0]
        cmd_path = os.path.join(COMMANDS_DIR, cmd_file)
        
        # 读取命令
        with open(cmd_path, 'r', encoding='utf-8') as f:
            command = json.load(f)
        
        cmd_id = command.get('id', 'unknown')
        cmd_type = command.get('type', 'unknown')
        
        # 删除命令文件
        try:
            os.remove(cmd_path)
        except:
            pass
        
        # 处理命令
        result = process_command(command)
        
        # 写入结果
        result_path = os.path.join(RESULTS_DIR, cmd_id + '.json')
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        
        if cmd_type != 'ping':
            status = "OK" if result.get('success') else "FAIL"
            print("MCP: " + cmd_type + " [" + status + "]")
        
        return True
        
    except Exception as e:
        print("MCP: Error: " + str(e))
        return False


def mcp_stop():
    """
    停止 mcp_loop()
    
    在 Abaqus 控制台的另一行输入此命令，或从外部调用
    """
    with open(STOP_FILE, 'w') as f:
        f.write('stop')
    print("MCP: Stop signal sent")


def mcp_loop():
    """
    持续处理 MCP 命令
    
    停止方法：
    1. 在另一个 Python 终端运行: python -c "open(r'C:\\Users\\Cai\\.abaqus-mcp\\stop.flag', 'w').close()"
    2. 或使用 MCP 发送 stop 命令
    """
    # 清除之前的停止标志
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    
    print("MCP: Listening for commands...")
    print("MCP: To stop, run in PowerShell:")
    print('     echo $null > "$env:USERPROFILE\\.abaqus-mcp\\stop.flag"')
    print("")
    write_status("running", "Polling active")
    
    try:
        while True:
            # 检查停止标志
            if os.path.exists(STOP_FILE):
                try:
                    os.remove(STOP_FILE)
                except:
                    pass
                print("MCP: Stopped by stop.flag")
                break
            
            poll_once()
            time.sleep(0.1)  # 100ms 轮询间隔
            
    except KeyboardInterrupt:
        print("\nMCP: Stopped by Ctrl+C")
    except Exception as e:
        print("MCP: Error: " + str(e))
    
    write_status("stopped", "Polling stopped")
    print("MCP: Loop ended")


def mcp_status():
    print("")
    print("=" * 50)
    print("MCP Status")
    print("=" * 50)
    print("Mode: File IPC")
    print("Commands dir: " + COMMANDS_DIR)
    print("Results dir: " + RESULTS_DIR)
    print("Stop file: " + STOP_FILE)
    print("")
    print("Usage:")
    print("  mcp_loop()  - Process commands continuously")
    print("  poll_once() - Process one command")
    print("")
    print("To stop mcp_loop(), run in PowerShell:")
    print('  echo $null > "$env:USERPROFILE\\.abaqus-mcp\\stop.flag"')
    print("=" * 50)


# ========== 初始化 ==========

ensure_dirs()
write_status("ready", "Plugin loaded")

print("")
print("=" * 50)
print("Abaqus MCP Plugin v3.1 (File IPC)")
print("=" * 50)
print("Home: " + MCP_HOME)
print("Abaqus: " + str(ABAQUS_AVAILABLE))
print("")
print("To process MCP commands, run:")
print("  mcp_loop()  - Process continuously")
print("  poll_once() - Process one command")
print("")
print("To stop mcp_loop(), run in PowerShell:")
print('  echo $null > "$env:USERPROFILE\\.abaqus-mcp\\stop.flag"')
print("=" * 50)
