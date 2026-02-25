import requests
import os
import re
import configparser
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime

from EBirdSessionManager import EBirdSessionManager


class EBirdChecklistManager(EBirdSessionManager):
    def __init__(self, csv_path, md_path):
        self.csv_path = csv_path
        self.md_path = md_path
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        super().__init__()
        self.get_valid_session()


    def fetch_remote_checklists(self):
        """解析 mychecklists 页面提取清单信息"""
        # url = "https://ebird.org/mychecklists"
        url= "https://ebird.org/mychecklists?year=&m=&d=&sharedFilter=all&currentRow=1&rowsPerPage=308"
        response = self.session.get(url)
        if response.status_code != 200:
            print("[-] 无法访问清单页面，请检查登录状态")
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        checklist_data = []

        # 解析 eBird 的清单行 (根据常见 HTML 结构)
        # 包含地点、日期、ID 和 区域
        rows = soup.select('li.ResultsStats--manageMyChecklists')

        for row in rows:
            try:
                # 1. 提取 Checklist ID (从 id 属性，如 checklist-S302929842)
                row_id = row.get('id', '')
                sub_id = row_id.replace('checklist-', '') if 'checklist-' in row_id else None
                if not sub_id: continue

                # 2. 提取日期和时间 (从链接标题或文本)
                # 这里的文本结构通常是 "22 二月 2026" + "3:44 下午"
                date_main = row.select_one('.Heading-main').get_text(strip=True) if row.select_one(
                    '.Heading-main') else ""
                time_sub = row.select_one('.Heading-sub').get_text(strip=True) if row.select_one('.Heading-sub') else ""
                full_dt = f"{date_main} {time_sub}"

                # 3. 提取地点
                loc_node = row.select_one('.ResultsStats-details-location')
                loc_name = loc_node.get_text(strip=True) if loc_node else "未知地点"

                # 4. 判断国家 (China) 用于设置同步状态
                # 查找包含 China 文本的元素
                country_nodes = row.select('.ResultsStats-details-stateCountry')
                countries = [c.get_text(strip=True) for c in country_nodes]
                is_china = "China" in countries or "中国" in countries

                # 1. 郡/县 (Central Athens)
                county_el = row.select_one(".ResultsStats-details-county")
                county = county_el.get_text(strip=True) if county_el else ""

                # 2. 州/省 (Jiangsu)
                # 注意：这两个字段 class 相同，可能需要根据父级 div 的序号或内容特征区分
                state_el = row.select_one(".ResultsStats-details-stateCountry")
                state = state_el.get_text(strip=True) if state_el else ""

                if sub_id:
                    checklist_data.append({
                        "checklist ID": sub_id,
                        "日期/时间": full_dt,
                        "地点": loc_name,
                        "is_china": is_china,
                        "国家": countries,
                        "州/省": state,
                        "郡/县": county
                    })
            except Exception as e:
                print(f"[-] 解析单条记录出错: {e}")
                continue

        return checklist_data

    def sync_data(self):
        """核心同步逻辑：更新 CSV 和 Markdown"""
        new_items = self.fetch_remote_checklists()
        if not new_items:
            print("[-] 未获取到任何清单数据")
            return

        # 1. 加载本地 CSV (如果没有则新建)
        if os.path.exists(self.csv_path):
            df = pd.read_csv(self.csv_path)
        else:
            df = pd.DataFrame(
                columns=["编号", "checklist ID", "日期/时间", "地点", "照片处理是否完成", "笔记是否更新完成",
                         "同步记录是否完成"])

        existing_ids = set(df['checklist ID'].astype(str).tolist())
        added_list = []

        # 2. 对比 Checklist ID
        for item in new_items:
            if item['checklist ID'] not in existing_ids:
                # 默认值逻辑
                sync_status = "否" if item['is_china'] else "NA"

                # 计算新编号 (基于当前长度)
                new_no = len(df) + len(added_list) + 1

                new_row = {
                    "编号": new_no,
                    "checklist ID": item['checklist ID'],
                    "日期/时间": item['日期/时间'],
                    "地点": item['地点'],
                    "国家": item['国家'],
                    "州/省": item['州/省'],
                    "郡/县": item['郡/县'],
                    "照片处理是否完成": "否",
                    "笔记是否更新完成": "否",
                    "同步记录是否完成": sync_status
                }
                added_list.append(new_row)

        if not added_list:
            print("[+] 数据已是最新，无需更新")
            return

        # 3. 将新记录插入 CSV 顶部（最新到最老）
        new_df = pd.DataFrame(added_list)
        final_df = pd.concat([new_df, df], ignore_index=True)
        # 2. 转换数据类型（确保该列是 datetime 对象，否则排序会按字符逻辑出错）
        final_df['日期/时间'] = pd.to_datetime(final_df['日期/时间'])

        # 3. 按日期从晚到早排序（最新在最前）
        final_df = final_df.sort_values(by='日期/时间', ascending=False).reset_index(drop=True)
        final_df.to_csv(self.csv_path, index=False)

        # 4. 更新 Markdown 笔记标题
        # 由于要求最新到最老，我们需要读取旧内容再重写
        old_content = ""
        if os.path.exists(self.md_path):
            with open(self.md_path, "r", encoding="utf-8") as f:
                old_content = f.read()

        new_titles = ""
        for item in added_list:
            title = f"## {item['checklist ID']}_{item['日期/时间']}_{item['地点']}"
            new_titles += f"{title}\n\n\n\n\n\n"

        with open(self.md_path, "w", encoding="utf-8") as f:
            f.write(new_titles + old_content)

        print(f"[+] 成功更新 {len(added_list)} 条新清单到 CSV 和 Markdown")

# # --- 使用示例 ---
# manager = EBirdChecklistManager("resource/观鸟记录表.csv", "resource/birding_notes.md")
# manager.sync_data()
