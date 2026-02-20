import requests
from bs4 import BeautifulSoup
import pandas as pd
import re


def get_avibase_malaysia_data(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')

    new_birds = []
    current_order, current_family = "", ""
    rows = soup.select("tr")

    for row in rows:
        header_text = row.get_text(strip=True)
        if ":" in header_text and ("IDAE" in header_text.upper() or "IFORMES" in header_text.upper()):
            parts = header_text.split(":")
            current_order, current_family = parts[0].strip(), parts[1].strip()
            continue

        cols = row.find_all("td")
        if len(cols) >= 3:
            en = cols[0].get_text(strip=True)
            la = cols[1].find("i").get_text(strip=True) if cols[1].find("i") else cols[1].get_text(strip=True)
            cn = cols[2].get_text(strip=True)
            # 清理中文名中的括号备注
            cn = re.sub(r'[\(（].*?[\)）]', '', cn).strip()

            if cn and en:
                new_birds.append({
                    "中文名_新": cn, "拉丁名": la, "英文名": en,
                    "目": current_order, "科": current_family
                })
    return pd.DataFrame(new_birds)


# 1. 爬取马来西亚新数据
url = "https://avibase.bsc-eoc.org/checklist.jsp?lang=ZH&region=my&list=clements&ref=l_asi_my"
df_avibase = get_avibase_malaysia_data(url)

# 2. 读取你的 Sheet1 (旧表)
df_sheet1 = pd.read_excel("bird_species_library.xlsx")

# 3. 执行合并 (以拉丁名为基准)
# 使用 outer join 确保两边的鸟都能保留
df_merged = pd.merge(df_sheet1, df_avibase, on="拉丁名", how="outer", suffixes=('', '_new'))


# 4. 逻辑处理：比较中文名
def handle_names(row):
    cn_old = str(row['中文名']).strip() if pd.notnull(row['中文名']) else ""
    cn_new = str(row['中文名_新']).strip() if pd.notnull(row['中文名_新']) else ""

    # 如果旧表没名，直接用新名
    if not cn_old:
        row['中文名'] = cn_new
        row['备选中文名'] = ""
    # 如果两个名字不同，则记录别名
    elif cn_new and cn_old != cn_new:
        row['备选中文名'] = cn_new
    else:
        row['备选中文名'] = ""

    # 补全目和科（如果旧表是空的）
    if pd.isnull(row['目']): row['目'] = row['目_new']
    if pd.isnull(row['科']): row['科'] = row['科_new']
    if pd.isnull(row['英文名']): row['英文名'] = row['英文名_new']

    return row


df_final = df_merged.apply(handle_names, axis=1)

# 5. 清理和去重（基于拉丁名）
df_final.drop_duplicates(subset=['拉丁名'], keep='first', inplace=True)

# 6. 整理列和序号
# 增加“备选中文名”列
cols_order = ['序号', '中文名', '备选中文名', '拉丁名', '英文名', '目', '科', 'ebird', 'birdreport']
# 确保所有列存在
for col in cols_order:
    if col not in df_final.columns:
        df_final[col] = ""

# 重新编号
df_final = df_final.sort_values(by=['目', '科']).reset_index(drop=True)
df_final['序号'] = df_final.index + 1

# 7. 保存
df_output = df_final[cols_order]
df_output.to_csv("final_merged_birds.csv", index=False, encoding="utf-8-sig")

print(f"合并完成！总计鸟种：{len(df_output)}，已识别并标注不同名称的鸟种。")