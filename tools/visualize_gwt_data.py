import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# ================= 配置 =================
CSV_FILE = Path(__file__).parent.parent / "data" / "gwt_time_analysis.csv"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "analysis_plots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 字体适配：增加更多保底中文字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'PingFang SC', 'SimHei', 'Microsoft YaHei', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False
sns.set_theme(style="whitegrid", font='Arial Unicode MS')

def analyze_gwt():
    if not CSV_FILE.exists():
        print(f"❌ 找不到文件: {CSV_FILE}")
        return

    df = pd.read_csv(CSV_FILE)
    print(f"统计：原始数据共有 {len(df)} 条记录")

    # 🌟 核心修复：将“年月日”替换为“-”，让 pandas 认识它
    # 比如 "2026年03月18日 15:04" -> "2026-03-18 15:04"
    df['clean_time'] = df['exact_time'].astype(str) \
        .str.replace('年', '-') \
        .str.replace('月', '-') \
        .str.replace('日', '') \
        .str.strip()

    # 尝试解析
    df['dt'] = pd.to_datetime(df['clean_time'], errors='coerce')
    
    # 看看解析成功了多少
    valid_count = df['dt'].notna().sum()
    print(f"统计：成功解析时间格式的记录有 {valid_count} 条")

    if valid_count == 0:
        print("❌ 解析失败：请检查 CSV 中 exact_time 列的格式是否真的是 '2026年03月18日 15:04'")
        return

    df = df.dropna(subset=['dt']).copy()
    
    # 特征提取
    # 核心数据转换逻辑
    df['hour'] = df['dt'].dt.hour
    df['min_bin'] = (df['dt'].dt.minute // 10) * 10
    
    # 绘图逻辑
    fig, axes = plt.subplots(1, 1, figsize=(16, 12))

    # 构建 24x6 的矩阵
    heatmap_data = df.groupby(['hour', 'min_bin']).size().unstack(fill_value=0)
    heatmap_data = heatmap_data.reindex(index=range(24), columns=[0, 10, 20, 30, 40, 50], fill_value=0)

    # 绘图：10分钟一个刻度
    plt.figure(figsize=(14, 10))
    sns.heatmap(heatmap_data, annot=True, fmt="d", cmap="YlGnBu")
    plt.title("深技大公文发文精确时间点热力分布 (10分钟/格)")
        




    plt.tight_layout()
    plot_path = OUTPUT_DIR / "gwt_full_report2.png"
    plt.savefig(plot_path, dpi=300)
    print(f"✅ 可视化完成！请查看: {plot_path}")

if __name__ == "__main__":
    analyze_gwt()