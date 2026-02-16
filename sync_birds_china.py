import requests
import pandas as pd
import io
import re
import os
import xlwt
import csv
from datetime import datetime, timedelta

# ================= 配置区 =================
# eBird 配置
EBIRD_SUBID = "S301238171"
EBIRD_SESSIONID = "29002FD4154012021EF81EEB0C8C0D01"

# BirdReport 配置
BIRDREPORT_TOKEN = "BC984F7CA1584889BF65C6508DB9E42D"
MEMBER_ID = 31349
TARGET_POINT_ID = 491
LIBRARY_XLSX = '郑四鸟种名录.xlsx'

# ==========================================

session = requests.Session()


def get_point_by_id(target_id, file_path='chinese_points.csv'):
    """根据 point_id 从 CSV 中读取对应的点位字典"""
    with open(file_path, mode='r', encoding='utf-8') as f:
        # 使用 DictReader 直接读取为字典格式
        reader = csv.DictReader(f)
        for row in reader:
            if row['point_id'] == str(target_id):
                # 转换部分数值类型（如果需要严格匹配 API）
                row['isopen'] = int(row['isopen'])
                return row
    return None


def round_time_15min(dt):
    """将时间圆整到最近的15分钟点 (如 10分->15分, 25分->30分)"""
    new_minute = (dt.minute // 15 + (1 if dt.minute % 15 > 0 else 0)) * 15
    if new_minute == 60:
        return dt.replace(minute=0, second=0) + timedelta(hours=1)
    return dt.replace(minute=new_minute, second=0)


def extract_clean_latin_name(full_text):
    """
    逻辑：提取最后一个括号里的内容。
    例子：'织女银鸥/蒙古银鸥 (西伯利亚银鸥) (Larus vegae/mongolicus)'
    -> 提取 'Larus vegae/mongolicus' -> 取斜杠前 -> 'Larus vegae'
    """
    # 1. 使用正则匹配所有括号内的内容
    brackets = re.findall(r'\(([^)]+)\)', full_text)
    if not brackets:
        return None

    # 2. 取最后一个括号的内容
    last_bracket = brackets[-1].strip()

    # 3. 如果包含斜杠 /，取前面的部分
    clean_name = last_bracket.split('/')[0].strip()

    return clean_name


def get_cn_name_from_xlsx(latin_name, xlsx_path="bird_library.xlsx"):
    """
    根据清洗后的拉丁名在 XLSX 文件的 C 列(拉丁名)查找对应的 B 列(中文名)
    """
    try:
        # 读取 Excel，假设 B列是中文名，C列是拉丁名
        df = pd.read_excel(xlsx_path)

        # 在拉丁名列中匹配（假设列名分别为 '中文名' 和 '拉丁名'，请根据实际表头修改）
        # 如果没有表头，可以使用 df.iloc
        match = df[df['拉丁名'].str.contains(latin_name, case=False, na=False)]

        if not match.empty:
            return match.iloc[0]['中文名']
    except Exception as e:
        print(f"查询库文件失败: {e}")

    return "未在库中找到"


def get_final_cn_name(full_name_str, library_path):
    """
    解析拉丁名并查表获取中文名
    """
    # 提取拉丁名
    latin = extract_clean_latin_name(full_name_str)
    if not latin:
        return full_name_str.split("(")[0]  # 找不到直接用原来的名字

    # 加载库文件查表
    try:
        # 注意：这里假设库文件的 C列是拉丁名，B列是中文名
        lib_df = pd.read_excel(library_path, engine='openpyxl')
        # 匹配逻辑
        result = lib_df[lib_df['拉丁名'].str.strip() == latin]
        if not result.empty:
            return result.iloc[0]['中文名']
    except Exception as e:
        print(f"查表匹配失败 ({latin}): {e}")

    return full_name_str.split("(")[0]  # 找不到直接用原来的名字


def step1_ebird_fetch_and_transform():
    print("[Step 1] 正在下载 eBird 鸟单并处理时间...")
    url = f"https://ebird.org/ebird/checklist/download?subID={EBIRD_SUBID}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    cookies = {'EBIRD_SESSIONID': EBIRD_SESSIONID}

    resp = session.get(url, headers=headers, cookies=cookies)
    if resp.status_code != 200:
        raise Exception("eBird 鸟单下载失败，请检查 SessionID")

    # 读取 CSV (eBird 下载格式)
    df = pd.read_csv(io.StringIO(resp.text))

    # 提取时间信息 (假设 eBird CSV 列名为 Date, Start Time, Duration (Min))
    date_val = df['Observation Date'].iloc[0]
    time_val = df['Start Time'].iloc[0]
    duration_val = df['Duration'].iloc[0]

    h_match = re.search(r'(\d+)\s*hour', duration_val)
    m_match = re.search(r'(\d+)\s*minute', duration_val)


    if m_match:
        total_min = int(m_match.group(1))
        if h_match:
            hours = int(h_match.group(1))
            total_min += hours * 60

    full_str = f"{date_val} {time_val}".replace("  ", " ")
    start_dt = datetime.strptime(full_str, "%b %d, %Y %I:%M %p")
    end_dt = start_dt + timedelta(minutes=total_min)

    # 按照需求圆整时间
    final_start = round_time_15min(start_dt)
    final_end = round_time_15min(end_dt)
    effective_hours = (final_end - final_start).total_seconds() / 3600

    print(f"    - 原始时间: {start_dt.strftime('%H:%M')} -> {end_dt.strftime('%H:%M')}")
    print(
        f"    - 圆整时间: {final_start.strftime('%H:%M')} -> {final_end.strftime('%H:%M')} (时长: {effective_hours}h)")

    # # 2. 鸟种名映射替换
    # name_mapper = load_name_mapper()
    #
    # # 生成 BirdReport 要求的 XLS 文件
    # xls_filename = f"bird_report_{EBIRD_SUBID}.xls"
    # workbook = xlwt.Workbook(encoding='utf-8')
    # sheet = workbook.add_sheet('鸟种导入模板')
    #
    # sheet.write(0, 0, "中文名")
    # sheet.write(0, 1, "数量")
    #
    # for i, row in df.iterrows():
    #     ebird_name = row['Species'].split("(")[0]
    #     # 执行替换逻辑：如果在映射表里就换掉，不在就用原名
    #     final_name = name_mapper.get(ebird_name, ebird_name)
    #     count = row['Count'] if str(row['Count']).isdigit() else 1
    #     sheet.write(i + 1, 0, final_name)
    #     sheet.write(i + 1, 1, int(count))
    #
    # workbook.save(xls_filename)
    # 创建上传用的 Excel
    xls_filename = f"transformed_{EBIRD_SUBID}.xls"
    workbook = xlwt.Workbook(encoding='utf-8')
    sheet = workbook.add_sheet('鸟种导入')
    sheet.write(0, 0, "中文名")
    sheet.write(0, 1, "数量")

    for i, row in df.iterrows():
        raw_name = row['Species']  # 格式如：织女银鸥/蒙古银鸥 (西伯利亚银鸥) (Larus vegae/mongolicus)
        count = row['Count'] if str(row['Count']).isdigit() else 1

        # 执行新的逻辑：提取拉丁名 -> 查表获取中文名
        final_cn_name = get_final_cn_name(raw_name, LIBRARY_XLSX)

        sheet.write(i + 1, 0, final_cn_name)
        sheet.write(i + 1, 1, int(count))
        print(f"    - 处理: {raw_name} -> 匹配到: {final_cn_name}")

    workbook.save(xls_filename)
    print(f"[OK] 转换完成，生成文件: {xls_filename}")

    return {
        "start_time": final_start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": final_end.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": f"{effective_hours:.2f}",
        "xls_path": xls_filename
    }


def step2_birdreport_save_report(times, point_info):
    print("[Step 2] 正在 BirdReport 新建报告并获取 ID...")
    url = "https://api.birdreport.cn/member/system/activity/saveReport"
    headers = {
        "Content-Type": "application/json",
        "X-Auth-Token": BIRDREPORT_TOKEN,
        "Referer": "https://www.birdreport.cn/"
    }

    payload = {
        "point": point_info,
        "activity": {
            "id": "",
            "start_time": times["start_time"],
            "end_time": times["end_time"],
            "state": "2",
            "note": f"Imported from eBird {EBIRD_SUBID}",
            "keywords": "",
            "domain_type": 0,
            "member_id": MEMBER_ID
        },
        "units_activity": []
    }

    resp = session.post(url, json=payload, headers=headers)
    res_data = resp.json()

    # 从返回的复杂结构中提取 activity_id (通常在 data.activity.id)
    try:
        activity_id = res_data['data']['activity_id']
        print(f"    - 报告创建成功，ID: {activity_id}")
        return activity_id
    except:
        print(f"[-] 创建报告失败: {res_data}")
        return None


def step3_birdreport_upload_data(activity_id, times, point_info):
    print("[Step 3] 正在上传 Excel 记录并推送鸟种...")
    headers = {"X-Auth-Token": BIRDREPORT_TOKEN,
               "Referer": "https://www.birdreport.cn/"
               }

    # 3.1 上传 Excel 文件
    upload_url = "https://api.birdreport.cn/member/system/upload/excel"
    with open(times["xls_path"], 'rb') as f:
        files = {'file': (os.path.basename(times["xls_path"]), f, 'application/vnd.ms-excel')}
        data = {'activity_id': activity_id}
        resp_up = session.post(upload_url, headers=headers, files=files, data=data)

    upload_res = resp_up.json()
    if not upload_res.get('success'):
        print(f"[-] Excel 解析失败: {upload_res}")
        return

    # 3.2 推送鸟种记录 (pushTaxon)
    push_url = "https://api.birdreport.cn/member/system/upload/pushTaxon"
    records = []
    for item in upload_res['data']:
        records.append({
            "activity_id": activity_id,
            "taxon_id": item['taxon_id'],
            "taxon_count": item['taxon_count'],
            "member_id": MEMBER_ID,
            "uuid": item['uuid']
        })

    push_payload = {
        "point": {"point_id": point_info["point_id"]},
        "activity": {"id": str(activity_id)},
        "records": records
    }
    session.post(push_url, json=push_payload, headers=headers)

    # 3.3 更新时长 (updateOptions)
    update_url = "https://api.birdreport.cn/member/system/activity/updateOptions"
    update_payload = {
        "eye_all_birds": "1",
        "real_quantity": "1",
        "effective_hours": times["duration"],
        "reportId": str(activity_id)
    }
    session.post(update_url, json=update_payload, headers=headers)
    print(f"[+] 流程完成！清单 {EBIRD_SUBID} 已成功同步。")

    if os.path.exists(times["xls_path"]):
        os.remove(times["xls_path"])
        print(f"[*] 已清理本地临时模板文件: {times['xls_path']}")


def main():
    try:
        point_info = get_point_by_id(TARGET_POINT_ID)
        if not point_info:
            return
        point_info['member_id'] = MEMBER_ID
        # 第一步：数据转换
        processed_data = step1_ebird_fetch_and_transform()

        # 第二步：新建报告
        act_id = step2_birdreport_save_report(processed_data, point_info)

        # 第三步：数据上传与更新
        if act_id:
            step3_birdreport_upload_data(act_id, processed_data, point_info)

    except Exception as e:
        print(f"[!] 发生错误: {e}")


if __name__ == "__main__":
    main()