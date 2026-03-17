import sqlite3
import os

# 动态定位数据库路径，确保能精准命中你的业务数据库
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 假设此脚本在根目录，database.py 在 src 目录，数据库在 data 目录
DB_PATH = os.path.join(BASE_DIR, 'data', 'tongwen.db')

def connect_db():
    if not os.path.exists(DB_PATH):
        print(f"❌ 找不到数据库文件: {DB_PATH}")
        return None
    return sqlite3.connect(DB_PATH)

def simulate_new_article():
    """测试场景 1：模拟发布了全新的公文 (删除本地最新的一条记录)"""
    conn = connect_db()
    if not conn: return
    
    try:
        cursor = conn.cursor()
        # 锁定最新的一篇文章
        cursor.execute("SELECT id, title FROM articles ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
        
        if row:
            article_id, title = row
            cursor.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            conn.commit()
            print(f"✅ 成功删除最新记录: 《{title}》")
            print("👉 等待后台守护线程轮询，它应该会被当作【全新公文】重新抓取并弹窗！")
        else:
            print("⚠️ 数据库为空，请先让主程序正常抓取一次。")
    finally:
        conn.close()

def simulate_silent_update():
    """测试场景 2：模拟内容暗改 (篡改本地最新记录的 Hash)"""
    conn = connect_db()
    if not conn: return
    
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, raw_hash FROM articles ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
        
        if row:
            article_id, title, old_hash = row
            fake_hash = "fake_hash_triggered_by_test_script_001"
            cursor.execute("UPDATE articles SET raw_hash = ? WHERE id = ?", (fake_hash, article_id))
            conn.commit()
            print(f"✅ 成功篡改哈希值: 《{title}》")
            print(f"   旧哈希: {old_hash}")
            print(f"   新哈希: {fake_hash}")
            print("👉 等待轮询，它应该会因为哈希变动，触发 DeepSeek 重新总结！")
        else:
            print("⚠️ 数据库为空。")
    finally:
        conn.close()

if __name__ == "__main__":
    print("====== 公文通后台静默测试工具 ======")
    print("1. 模拟全新公文发布 (Delete Top Row)")
    print("2. 模拟公文内容暗改 (Corrupt Top Hash)")
    choice = input("请输入测试编号 (1 或 2): ")
    
    if choice == '1':
        simulate_new_article()
    elif choice == '2':
        simulate_silent_update()
    else:
        print("输入无效。")