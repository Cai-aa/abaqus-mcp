# -*- coding: utf-8 -*-
"""
停止 Abaqus MCP 循环

在任意 Python 环境中运行此脚本即可停止 mcp_loop()
"""
import os

stop_file = os.path.join(os.path.expanduser('~'), '.abaqus-mcp', 'stop.flag')
with open(stop_file, 'w') as f:
    f.write('stop')
print("Stop signal sent to Abaqus MCP")
