import requests
import os
import re
import hashlib
from bs4 import BeautifulSoup

# ================= 配置区 =================
CHECKLIST_ID = "S299905987"  # 你的清单 ID
EBIRD_SESSIONID = "51D1AC91A57D1BE763BCB20D657C667F"  # 你的 SessionID
LOCAL_FOLDER = "D:\\birds\\20260213 虞山国家森林公园2"  # 图片所在文件夹路径
# ==========================================

session = requests.Session()
session.cookies.set("EBIRD_SESSIONID", EBIRD_SESSIONID, domain="ebird.org")
session.cookies.set("I18N_LANGUAGE", "zh_CN", domain="ebird.org")
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
})


def get_md5(file_path):
    """计算文件的 MD5 值"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_checklist_map(checklist_id):
    """请求清单页面并解析鸟种名与 obsId, speciesCode 的映射"""
    print(f"[*] 正在获取清单 {checklist_id} 的数据...")
    url = f"https://ebird.org/checklist/{checklist_id}"
    params = {'locale': 'zh_CN'}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    cookies = {
        'EBIRD_SESSIONID': '51D1AC91A57D1BE763BCB20D657C667F'  # 必须保持你有效的 Session
    }

    response = session.get(url, headers=headers, cookies=cookies, params=params)
    if response.status_code != 200:
        print("获取清单失败，请检查 SessionID")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')

    # --- 提取 Token 逻辑 ---
    csrf_token = None
    # 方案 1: 寻找你提到的 rating-csrf (在 Vue 组件属性中)
    vue_component = soup.find('checklist-featured-media')
    if vue_component and vue_component.get('rating-csrf'):
        csrf_token = vue_component.get('rating-csrf')
        print(f"[!] 成功从组件中获取 Token: {csrf_token}")

    bird_map = {}
    # 遍历每一个观测记录行 (li 标签)
    rows = soup.find_all('li', attrs={"data-observation": True})
    if not rows:
        # 兼容性处理：如果没有找到 li，则尝试寻找所有 section
        rows = soup.find_all('section', class_='Observation')

    for row in rows:
        # 1. 寻找 speciesCode 和 中文名
        link = row.find('a', attrs={"data-species-code": True})
        if not link: continue

        species_code = link.get('data-species-code')
        cn_name_tag = link.find('span', class_='Heading-main')
        cn_name = cn_name_tag.get_text(strip=True) if cn_name_tag else ""

        # 2. 核心：从删除按钮中提取 obsId
        # 结构：<button data-obsid="OBS4069246435">
        obs_btn = row.find('button', attrs={"data-obsid": True})
        obs_id = obs_btn.get('data-obsid') if obs_btn else ""

        if cn_name and obs_id:
            bird_map[cn_name] = {
                "obsId": obs_id,
                "speciesCode": species_code
            }

    print(f"[+] 成功解析出 {len(bird_map)} 种鸟类信息")
    return bird_map, csrf_token


def upload_process(file_path, obs_id, species_code, csrf_token):
    """执行单个文件的完整上传流程"""
    file_name = os.path.basename(file_path)
    md5_val = get_md5(file_path)

    # 1. 获取 S3 Policy
    policy_url = f"https://ebird.org/media-upload/checklist/{CHECKLIST_ID}/policy"
    params = {
        "fileName": file_name,
        "md5sum": md5_val,
        "contentType": "image/jpeg"
    }
    resp_policy = session.get(policy_url, params=params)
    if resp_policy.status_code != 200:
        print(f"   - 获取 Policy 失败: {resp_policy.text}")
        return False

    policy_data = resp_policy.json()
    s3_fields = policy_data['policy']
    asset_id = policy_data['assetId']

    # 2. 上传至 S3
    s3_url = "https://ml-media-transcode-inbox-prod.s3.us-east-1.amazonaws.com/"
    files = {'file': (file_name, open(file_path, 'rb'), 'image/jpeg')}
    resp_s3 = requests.post(s3_url, data=s3_fields, files=files)
    if resp_s3.status_code not in [200, 204]:
        print(f"   - S3 上传失败: {resp_s3.text}")
        return False

    # 3. 关联至清单记录
    add_url = f"https://ebird.org/media-assets/add/{CHECKLIST_ID}"
    payload = [{
        "obsId": obs_id,
        "speciesCode": species_code,
        "assets": [{"assetId": asset_id, "mediaType": "P"}]
    }]
    headers = {"x-csrf-token": csrf_token, "content-type": "application/json"}
    resp_assoc = session.post(add_url, json=payload, headers=headers)

    return resp_assoc.status_code == 200


def main():
    if not os.path.exists(LOCAL_FOLDER):
        print(f"错误: 文件夹 {LOCAL_FOLDER} 不存在")
        return

    # 获取清单映射
    bird_map, dynamic_csrf_token = get_checklist_map(CHECKLIST_ID)
    if not bird_map: return

    # 扫描文件夹
    print(f"[*] 开始扫描文件夹: {LOCAL_FOLDER}")
    for file_name in os.listdir(LOCAL_FOLDER):
        # 匹配格式: 鸟种名_Y.jpg
        match = re.match(r"^(.+?)_Y.*?\.(jpg|jpeg|JPG|JPEG)$", file_name)
        if match:
            bird_name = match.group(1)
            if bird_name in bird_map:
                print(f"\n[处理中] 发现匹配文件: {file_name}")
                info = bird_map[bird_name]
                file_path = os.path.join(LOCAL_FOLDER, file_name)

                if upload_process(file_path, info['obsId'], info['speciesCode'], dynamic_csrf_token):
                    print(f"[成功] {bird_name} 照片已上传并关联")
                else:
                    print(f"[失败] {bird_name} 上传流程出现问题")
            else:
                print(f"[跳过] 清单中未找到鸟种: {bird_name}")


if __name__ == "__main__":
    main()
