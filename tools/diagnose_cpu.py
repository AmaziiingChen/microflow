#!/usr/bin/env python3
"""CPU占用诊断工具 - 找出哪个线程在消耗CPU"""

import sys
import os
import time
import threading

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("🔍 开始诊断CPU占用问题...\n")
print("请保持应用运行，观察10秒...\n")

# 导入主要模块
from src.api import Api
from src.database import db

print("✅ 模块导入成功\n")

# 创建API实例
api = Api()

print("✅ API实例创建成功\n")
print("📊 当前活跃线程列表：\n")

for thread in threading.enumerate():
    print(f"  - {thread.name} (daemon={thread.daemon}, alive={thread.is_alive()})")

print("\n⏱️ 等待10秒，观察CPU占用...")
print("请打开活动监视器查看 python 进程的CPU占用\n")

for i in range(10, 0, -1):
    print(f"  {i}秒...", end='\r')
    time.sleep(1)

print("\n✅ 诊断完成！")
print("\n💡 如果CPU占用很高，说明问题在某个后台线程的忙等待。")
print("   已修复的问题：JS执行线程的 timeout=0 改为 timeout=1.0")
print("\n🔧 建议：重启应用查看CPU占用是否降低。")
