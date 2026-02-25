import os
import configparser
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


class EBirdSessionManager:
    def __init__(self, secrets_path="secrets.ini"):
        self.secrets_path = secrets_path
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

        # 1. 加载账号密码
        self.username, self.password, self.cookie = self._load_secrets(self.secrets_path)

        # 2. 尝试从本地加载已有的 Cookie
        self._load_cached_cookies()

    def _load_secrets(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"找不到配置文件: {path}")
        config = configparser.ConfigParser()
        config.read(path, encoding='utf-8')
        return config.get('ebird', 'username'), config.get('ebird', 'password'), config.get('ebird', 'cookie_string')

    def _load_cached_cookies(self):
        """从 auth.ini 加载持久化的 Cookie"""
        if self.cookie:
            for item in self.cookie.split('; '):
                if '=' in item:
                    k, v = item.split('=', 1)
                    self.session.cookies.set(k, v, domain="ebird.org")

    def _save_cookies_to_cache(self):
        """将当前 Session 里的有效 Cookie 保存到 auth.ini"""
        config = configparser.ConfigParser()
        cookie_dict = self.session.cookies.get_dict()
        cookie_str = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])

        config['ebird'] = {'cookie_string': cookie_str}
        with open(self.secrets_path, 'w', encoding='utf-8') as f:
            config.write(f)

    def login_cas(self):
        """基础登录流程：获取 CAS 验证"""
        print("[*] 正在通过 CAS 接口尝试登录...")
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
                print("[+] CAS 登录成功！")
                self._save_cookies_to_cache()
                return True
        except Exception as e:
            print(f"[-] CAS 登录异常: {e}")
        return False

    def get_valid_session(self):
        """核心业务逻辑：获取可用的 Session，失效则自动重登"""
        # 设置 rowsPerPage=308 的目标地址
        target_url = "https://ebird.org/mychecklists?year=&m=&d=&sharedFilter=all&currentRow=1&rowsPerPage=308"

        print("[*] 正在校验 Session 有效性...")
        try:
            resp = self.session.get(target_url, allow_redirects=True)

            # 判断逻辑：如果内容包含登录字样，说明 Cookie 失效
            if "Sign in to your Cornell Lab Account" in resp.text or "登录您的" in resp.text:
                print("[!] Cookie 已失效，准备重新登录...")
                if self.login_cas():
                    # 登录成功后重新请求目标页面
                    resp = self.session.get(target_url)
                    return self.session, resp.text
                else:
                    return None, None

            print("[+] Session 仍然有效，直接复用。")
            return self.session, resp.text

        except Exception as e:
            print(f"[-] 请求异常: {e}")
            return None, None