import requests
import os
import re
import hashlib
import time
import pandas as pd
import configparser
from bs4 import BeautifulSoup
from pandas import notna


class EBirdMediaUploader:
    def __init__(self, config_path, library_path):
        self.username, self.password = self._load_secrets(config_path)
        self.library_path = library_path
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        self.species_dict = self._load_species_library()

    def _load_secrets(self, path):
        """从 .ini 文件读取用户名和密码"""
        if not os.path.exists(path):
            raise FileNotFoundError(f"找不到配置文件: {path}，请确保它不在 git 追踪范围内")
        config = configparser.ConfigParser()
        config.read(path, encoding='utf-8')
        return config.get('ebird', 'username'), config.get('ebird', 'password')

    def _load_species_library(self):
        """预加载鸟种库，解决拉丁名匹配问题"""
        if not os.path.exists(self.library_path):
            print(f"[-] 警告: 库文件 {self.library_path} 不存在")
            return {}
        try:
            df = pd.read_excel(self.library_path, engine='openpyxl', dtype=str)
            # 使用小写拉丁名作为键，实现不区分大小写的匹配
            return {
                str(cn).strip().lower(): [cn, str(latin).strip(), eng, ebird]
                for latin, cn, eng, ebird in zip(df['拉丁名'], df['中文名'],df['英文名'],df['ebird'],) if pd.notna(latin)
            }
        except Exception as e:
            print(f"[-] 库文件加载失败: {e}")
            return {}

    def login(self):
        """1. 登录流程：获取 CAS 验证并保持 Session"""
        print("[*] 正在尝试登录 eBird...")
        login_url = "https://secure.birds.cornell.edu/cassso/login?service=https%3A%2F%2Febird.org%2Flogin%2Fcas%3Fportal%3Debird&locale=zh-cn"
        try:
            resp = self.session.get(login_url)
            soup = BeautifulSoup(resp.text, 'html.parser')
            execution = soup.find('input', {'name': 'execution'})['value']

            payload = {
                'service': 'https://ebird.org/login/cas?portal=ebird',
                'locale': 'zh-cn',
                'username': self.username,
                'password': self.password,
                'rememberMe': 'on',
                'execution': execution,
                '_eventId': 'submit'
            }

            res = self.session.post("https://secure.birds.cornell.edu/cassso/login", data=payload)
            if "Sign Out" in res.text or "退出" in res.text:
                print("[+] 登录成功！")
                return True
        except Exception as e:
            print(f"[-] 登录异常: {e}")
        return False

    def get_checklist_info(self, checklist_id):
        """2. 解析流程：获取清单中的 obsId, speciesCode 和 CSRF Token"""
        print(f"[*] 正在解析清单 {checklist_id}...")
        url = f"https://ebird.org/checklist/{checklist_id}?locale=zh_CN"
        resp = self.session.get(url)
        soup = BeautifulSoup(resp.text, 'html.parser')

        # 提取 Token (用于后续关联媒体)
        vue_comp = soup.find('checklist-featured-media')
        csrf_token = vue_comp.get('rating-csrf') if vue_comp else None

        bird_map = {}
        # 遍历观测行提取数据
        rows = soup.find_all('li', attrs={"data-observation": True})
        for row in rows:
            link = row.find('a', attrs={"data-species-code": True})
            obs_btn = row.find('button', attrs={"data-obsid": True})
            if link and obs_btn:
                cn_name = link.find('span', class_='Heading-main').get_text(strip=True)
                bird_map[cn_name] = {
                    "obsId": obs_btn.get('data-obsid'),
                    "speciesCode": link.get('data-species-code')
                }
        return bird_map, csrf_token

    def upload_media(self, checklist_id, file_path, obs_id, species_code, csrf_token):
        """3. 上传流程：获取 Policy -> 上传 S3 -> 关联清单"""
        file_name = os.path.basename(file_path)

        # 计算 MD5
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""): hash_md5.update(chunk)
        md5_val = hash_md5.hexdigest()

        # A. 获取 S3 Policy
        policy_url = f"https://ebird.org/media-upload/checklist/{checklist_id}/policy"
        resp_p = self.session.get(policy_url,
                                  params={"fileName": file_name, "md5sum": md5_val, "contentType": "image/jpeg"})
        if resp_p.status_code != 200: return False
        p_data = resp_p.json()

        # B. 上传至 S3 存储桶
        with open(file_path, 'rb') as f:
            # S3 上传不带 Session Headers
            requests.post(p_data['uploadUrl'], data=p_data['policy'], files={'file': f})

        # C. 媒体与记录关联
        add_url = f"https://ebird.org/media-assets/add/{checklist_id}"
        payload = [{"obsId": obs_id, "speciesCode": species_code,
                    "assets": [{"assetId": p_data['assetId'], "mediaType": "P"}]}]
        resp_assoc = self.session.post(add_url, json=payload, headers={"x-csrf-token": csrf_token})
        return resp_assoc.status_code == 200

    def run_folder_upload(self, checklist_id, folder_path):
        """执行文件夹自动化上传"""
        bird_map, csrf_token = self.get_checklist_info(checklist_id)
        if not bird_map or not csrf_token: return

        for file_name in os.listdir(folder_path):
            # 匹配 "鸟名_Y.jpg" 格式
            match = re.match(r"^(.+?)_Y.*?\.(jpg|jpeg|JPG|JPEG)$", file_name)
            if match:
                bird_name = match.group(1)
                # 如果找不到映射，则 fallback 使用原名species_dict: [中文名，拉丁名，英文名，ebird名]
                target_name = self.species_dict.get(bird_name, bird_name)
                ebird_target_name = target_name[-1] if pd.notna(target_name[-1]) else target_name[0]  # 如有指定的ebird值 如虎斑地鸫 (怀氏虎鸫)，用指定值，否则用现有中文
                info = bird_map[ebird_target_name]

                if ebird_target_name in bird_map:  # 查到有数据
                    print(f"[*] 处理: {file_name}")
                    f_path = os.path.join(folder_path, file_name)

                    if self.upload_media(checklist_id, f_path, info['obsId'], info['speciesCode'], csrf_token):
                        print(f"[+] 成功: {ebird_target_name}")
                    else:
                        print(f"[-] 失败: {ebird_target_name}")


# ================= 运行 =================
if __name__ == "__main__":
    uploader = EBirdMediaUploader("secrets.ini", "bird_species_library.xlsx")
    if uploader.login():
        uploader.run_folder_upload("S301899422", "D:\\birds\\20260218 虞山国家森林公园")
