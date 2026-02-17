import requests
import pandas as pd
import io
import re
import os
import xlwt
import csv
import configparser
from datetime import datetime, timedelta
from bs4 import BeautifulSoup


class BirdReportSync:
    def __init__(self, config_path, library_path):
        """初始化配置并加载凭据"""
        self.config = configparser.ConfigParser()
        self.config.read(config_path, encoding='utf-8')

        # 读取凭据
        self.ebird_user = self.config.get('ebird', 'username')
        self.ebird_pass = self.config.get('ebird', 'password')
        self.br_token = self.config.get('birdreport', 'token')
        self.member_id = self.config.getint('birdreport', 'member_id')

        self.library_path = library_path
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        })
        self.species_df = self._load_species_library()

    def _load_species_library(self):
        """预加载鸟种库，解决拉丁名匹配问题"""
        if not os.path.exists(self.library_path):
            print(f"[-] 警告: 库文件 {self.library_path} 不存在")
            return {}
        try:
            df = pd.read_excel(self.library_path, engine='openpyxl', dtype=str)
            # 使用小写拉丁名作为键，实现不区分大小写的匹配
            return df
        except Exception as e:
            print(f"[-] 库文件加载失败: {e}")
            return {}

    def login_ebird(self):
        """执行 eBird CAS 自动登录，获取动态 Session"""
        print("[*] 正在尝试登录 eBird...")
        login_url = "https://secure.birds.cornell.edu/cassso/login?service=https%3A%2F%2Febird.org%2Flogin%2Fcas%3Fportal%3Debird&locale=zh-cn"
        try:
            # 1. 获取 execution 参数
            resp = self.session.get(login_url)
            soup = BeautifulSoup(resp.text, 'html.parser')
            execution = soup.find('input', {'name': 'execution'})['value']

            # 2. 提交登录
            payload = {
                'service': 'https://ebird.org/login/cas?portal=ebird',
                'username': self.ebird_user,
                'password': self.ebird_pass,
                'execution': execution,
                '_eventId': 'submit',
                'rememberMe': 'on'
            }
            res = self.session.post("https://secure.birds.cornell.edu/cassso/login", data=payload)

            if "Sign Out" in res.text or "退出" in res.text:
                print("[+] eBird 登录成功！")
                return True
        except Exception as e:
            print(f"[-] eBird 登录异常: {e}")
        return False

    def _get_final_cn_name(self, full_name_str):
        """解析拉丁名并查表映射中文名"""
        brackets = re.findall(r'\(([^)]+)\)', full_name_str)
        if not brackets:
            return full_name_str.split("(")[0].strip()

        latin = brackets[-1].split('/')[0].strip()
        try:
            result = self.species_df[self.species_df['拉丁名'].str.strip() == latin]
            if not result.empty:
                return str(result.iloc[0]['中文名']).strip()
            else:  # 如果latin查不到，差ebird列（手动维护）
                result = self.species_df[self.species_df['ebird'].str.strip() == brackets[0]]
                if not result.empty:
                    return str(result.iloc[0]['中文名']).strip()
        except Exception as e:
            print(f"[-] 查表匹配失败 ({latin}): {e}")

        return full_name_str.split("(")[0].strip()

    def fetch_and_transform(self, ebird_subid):
        """下载 eBird 鸟单并转换为上传模板"""
        print(f"[*] 正在下载清单数据: {ebird_subid}")
        url = f"https://ebird.org/ebird/checklist/download?subID={ebird_subid}"

        # 直接使用登录后的 session 请求
        resp = self.session.get(url)
        if resp.status_code != 200:
            raise Exception("鸟单下载失败，请确认是否已成功登录 eBird")

        df = pd.read_csv(io.StringIO(resp.text))

        # 时间圆整处理
        date_val = df['Observation Date'].iloc[0]
        time_val = df['Start Time'].iloc[0]
        duration_val = df['Duration'].iloc[0]

        total_min = 0
        h_match = re.search(r'(\d+)\s*hour', duration_val)
        m_match = re.search(r'(\d+)\s*minute', duration_val)
        if h_match: total_min += int(h_match.group(1)) * 60
        if m_match: total_min += int(m_match.group(1))

        start_dt = datetime.strptime(f"{date_val} {time_val}".replace("  ", " "), "%b %d, %Y %I:%M %p")
        end_dt = start_dt + timedelta(minutes=total_min)

        # 15分钟圆整逻辑
        def round_15(dt):
            nm = (dt.minute // 15 + (1 if dt.minute % 15 > 0 else 0)) * 15
            return dt.replace(minute=0, second=0) + timedelta(hours=1) if nm == 60 else dt.replace(minute=nm, second=0)

        final_start, final_end = round_15(start_dt), round_15(end_dt)
        duration_h = (final_end - final_start).total_seconds() / 3600

        # 生成导出文件
        xls_filename = f"sync_{ebird_subid}.xls"
        workbook = xlwt.Workbook(encoding='utf-8')
        sheet = workbook.add_sheet('鸟种导入')
        sheet.write(0, 0, "中文名");
        sheet.write(0, 1, "数量")

        for i, row in df.iterrows():
            sheet.write(i + 1, 0, self._get_final_cn_name(row['Species']))
            sheet.write(i + 1, 1, int(row['Count']) if str(row['Count']).isdigit() else 1)

        workbook.save(xls_filename)
        return {"start": final_start.strftime("%Y-%m-%d %H:%M:%S"), "end": final_end.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": f"{duration_h:.2f}", "xls_path": xls_filename}

    def sync_to_birdreport(self, ebird_subid, target_point_id):
        """同步主流程"""
        if not self.login_ebird(): return

        # 1. 获取点位信息 (假设已存在 chinese_points.csv)
        point_info = None
        if os.path.exists('chinese_points.csv'):
            with open('chinese_points.csv', mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['point_id'] == str(target_point_id):
                        point_info = row
                        point_info['isopen'], point_info['member_id'] = int(row['isopen']), self.member_id
                        break

        if not point_info:
            print(f"[-] 找不到点位 {target_point_id}")
            return

        # 2. 转换并上传
        times = self.fetch_and_transform(ebird_subid)
        headers = {"Content-Type": "application/json", "X-Auth-Token": self.br_token,
                   "Referer": "https://www.birdreport.cn/"}

        try:
            # A. 创建报告
            resp = self.session.post("https://api.birdreport.cn/member/system/activity/saveReport",
                                     json=
                                     {
                                         "point": point_info,
                                         "activity": {
                                             "id": "",
                                             "start_time": times["start"],
                                             "end_time": times["end"],
                                             "state": "2",
                                             "note": f"Imported from eBird {ebird_subid}",
                                             "keywords": "",
                                             "domain_type": 0,
                                             "member_id": self.member_id
                                         },
                                         "units_activity": []
                                     }, headers=headers)
            act_id = resp.json().get('data', {}).get('activity_id')

            if act_id:
                # B. 上传并推送
                with open(times["xls_path"], 'rb') as f:
                    resp_up = self.session.post("https://api.birdreport.cn/member/system/upload/excel",
                                                headers={"X-Auth-Token": self.br_token,
                                                         "Referer": "https://www.birdreport.cn/"},
                                                files={'file': (os.path.basename(times["xls_path"]), f, 'application/vnd.ms-excel')},
                                                data={'activity_id': act_id})

                up_data = resp_up.json().get('data', [])
                records = [{"activity_id": act_id, "taxon_id": item['taxon_id'], "taxon_count": item['taxon_count'],
                            "member_id": self.member_id, "uuid": item['uuid']} for item in up_data]

                self.session.post("https://api.birdreport.cn/member/system/upload/pushTaxon",
                                  json={"point": {"point_id": point_info["point_id"]}, "activity": {"id": str(act_id)},
                                        "records": records}, headers=headers)

                # C. 更新时长
                self.session.post("https://api.birdreport.cn/member/system/activity/updateOptions",
                                  json={"effective_hours": times["duration"], "reportId": str(act_id),
                                        "eye_all_birds": "1", "real_quantity": "1"}, headers=headers)
                print(f"[+] 同步成功: {ebird_subid} -> BirdReport ID: {act_id}")
        finally:
            if os.path.exists(times["xls_path"]):
                os.remove(times["xls_path"])  # 清理临时文件


# ================= 运行 =================
if __name__ == "__main__":
    syncer = BirdReportSync("secrets.ini", "bird_species_library.xlsx")
    syncer.sync_to_birdreport("S301238171", 491)
