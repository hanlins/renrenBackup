# coding: utf8

from datetime import datetime
import hashlib
import json
import logging
import os
import random
import re
import time
import webbrowser

import requests
import requests.utils
from requests.exceptions import ConnectionError, ReadTimeout  # pylint: disable=W0622

from config import config


logger = logging.getLogger(__name__)

class Crawler(object):
    DEFAULT_HEADER = {
        'Accept': 'text/html, application/xhtml+xml, */*',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      + '(KHTML, like Gecko) Chrome/67.0.3396.99 Safari/537.36',
        'Accept-Encoding': 'gzip, deflate',
        'Cache-Control': 'no-cache'
    }

    def get_secret_key(self):
        return re.findall(r"secretKey%22%3A%22(.*)%22%2C%22sessionKey", 
            self.session.headers['cookie'])[0]

    def get_session_key(self):
        return re.findall(r"essionKey%22%3A%22(.*)%22%7D", 
            self.session.headers['cookie'])[0]

    def __init__(self, email=None, password=None, cookies=None):
        self.uid = ''
        self.email = email
        self.password = password
        self.session = requests.session()
        self.session.headers = Crawler.DEFAULT_HEADER

        # if cookies and cookies.get('ln_uact') == email:
        #     self.uid = int(cookies['id'])
        #     self.session.cookies.update(cookies)

        # self.check_login()

    def get_uid(self):
        def parse_cookie(s):
            # parse cookie to a dict
            cookies = {}
            for line in s.split(';'):
                k, v = line.strip().split('=', 1)
                cookies[k] = v
                # unescape v
                if '%' in v:
                    import urllib.parse
                    cookies[k] = urllib.parse.unquote(v)
            return cookies
        def get_username_userid_pic(cookie):
            info = cookie['LOCAL_STORAGE_KEY_RENREN_USER_BASIC_INFO']
            info = eval(info)
            # convert escaped unicode to unicode
            info['userName'] = info['userName'].replace('%', '\\')
            info['userName'] = eval("'" + info['userName'] + "'")
            return info['userId'], info['userName'], info['headUrl']

        return get_username_userid_pic((parse_cookie(self.session.headers['cookie'])))[0]

    @classmethod
    def load_cookie(cls):
        cookies = None

        if os.path.exists(config.COOKIE_FILE):
            with open(config.COOKIE_FILE) as fp:
                try:
                    cookies = requests.utils.cookiejar_from_dict(json.load(fp))
                    logger.info('load cookies from {filename}'.format(filename=config.COOKIE_FILE))
                except json.decoder.JSONDecodeError:
                    cookies = None

        return cookies

    @classmethod
    def load_headers(cls):
        headers = None
        
        if os.path.exists(config.HEADERS_FILE):
            with open(config.HEADERS_FILE) as fp:
                try:
                    headers = json.load(fp)
                    logger.info('load headers from {filename}'.format(filename=config.HEADERS_FILE))
                except json.decoder.JSONDecodeError:
                    headers = None
        
        return headers

    @classmethod
    def input_headers(cls):
        # Read sample headers
        def read_multiple_lines():
            lines = []
            line = input('Paste the node.js fetch script:')
            while True:
                if line:
                    lines.append(line)
                else:
                    break
                line = input()
            return '\n'.join(lines)

        def parse_fetch(s):
            lines = s.split('\n')
            st = None
            ed = None
            for idx, line in enumerate(lines):
                if 'headers' in line:
                    st = idx
                if st is not None and '},' in line:
                    ed = idx
                    break
            return eval('{' + '\n'.join(lines[st+1:ed]) + '}')

        return parse_fetch(read_multiple_lines())


    def dump_cookie(self):
        cookies = self.session.cookies
        for cookie in cookies:
            if cookie.name == 't' and cookie.path != '/':
                cookies.clear(cookie.domain, cookie.path, cookie.name)
        with open(config.COOKIE_FILE, 'w') as fp:
            json.dump(requests.utils.dict_from_cookiejar(cookies), fp)

    def dump_headers(self):
        with open(config.HEADERS_FILE, 'w') as fp:
            json.dump(self.session.headers, fp)

    def get_url(self, url, params=None, data=None, json_=None, method='GET', retry=0, ignore_login=False):
        if not ignore_login and not self.uid:
            logger.info("need login")
            self.login()

        if params is None:
            params = dict()

        if retry >= config.RETRY_TIMES:
            raise TimeoutError("network error, exceed max retry time on get url from {url}".format(url=url))
        try:
            request_args = {
                'url': url,
                'params': params,
                'data': data,
                'json': json_,
                'timeout': config.TIMEOUT,
                'allow_redirects': False
            }
            logger.debug(u"{method} {url} with {params}".format(method=method, url=url, params=params))
            if method == 'POST':
                resp = self.session.post(**request_args)
            else:
                resp = self.session.get(**request_args)
        except (ConnectionError, ReadTimeout):
            time.sleep(2 ** retry)
            return self.get_url(url, params, data, json_, method, retry+1)

        if resp.status_code == 500:
            logger.warning('renren return 500, wait a moment')
            time.sleep(2 ** retry)
            return self.get_url(url, params, data, json_, method, retry+1)

        if resp.status_code == 302 and resp.headers['Location'].find('Login') >= 0:
            logger.info('login expired, re-login')
            self.login()
            return self.get_url(url, params, data, json_, method, retry+1)

        if resp.cookies.get_dict():
            self.session.cookies.update(resp.cookies)

        return resp

    def get_json(self, url, params=None, data=None, json_=None, method='GET', retry=0):
        resp = self.get_url(url, params, data, json_, method)
        try:
            r = json.loads(resp.text)
        except json.decoder.JSONDecodeError:
            r = json.loads(resp.text.replace(',}', '}'))

        if int(r.get('code', 0)):
            if retry >= config.RETRY_TIMES:
                raise Exception("network error, exceed max retry time on get json from {url}".format(url=url))

            time.sleep(2 ** retry)
            retry += 1
            return self.get_json(url, params, data, json_, method, retry)

        return r

    def check_login(self):
        logger.info('  check login, and get homepage for cookie')
        self.get_url("http://www.renren.com/personal/{uid}".format(uid=self.uid))
        logger.info('    login valid')

        self.dump_cookie()

    def login(self, retry=0):
        if retry >= config.RETRY_TIMES:
            raise Exception("Cannot login")

        if not retry:
            self.session.cookies.clear()

        logger.info('prepare login encryt info')
        param = {
            'user': self.email,
            'password': encryptedString(re, rn, self.password),
            'rkey': rk,
            'key_id': 1,
            'captcha_type': 'web_login',
            'icode': icode
        }

        now = datetime.now()
        ts = '{year}{month}{weekday}{hour}{second}{ms}'.format(**{
            'year': now.year,
            'month': now.month-1,
            'weekday': (now.weekday()+1) % 7,
            'hour': now.hour,
            'second': now.second,
            'ms': int(now.microsecond/1000)
        })
        login_url = config.LOGIN_URL.format(ts=ts)

        logger.info('prepare post login request')
        resp = self.get_url(login_url, params=param, method='POST', ignore_login=True)
        login_json = json.loads(resp.text)
        cookies = requests.utils.dict_from_cookiejar(self.session.cookies)
        if not login_json.get('code', False) or 'id' not in cookies:
            try:
                logger.info(u'login failed: {reason}'.format(
                    reason=login_json.get('failDescription', 'unknown reason')
                ))
            except UnicodeEncodeError:
                logger.info('login failed because {failCode}'.format(
                    failCode=login_json.get('failCode', '-1')
                ))

            icode_url = config.ICODE_URL.format(rnd=random.random())
            icode_resp = self.get_url(icode_url, ignore_login=True)
            logger.info('get icode image, output to {filepath}'.format(
                filepath=config.ICODE_FILEPATH
            ))
            with open(config.ICODE_FILEPATH, 'wb') as fp:
                fp.write(icode_resp.content)
                icode_filepath = os.path.abspath(config.ICODE_FILEPATH)
                webbrowser.open('file:///{filepath}'.format(filepath=icode_filepath))

            icode = input("Input text on Captcha icode image: ")
            retry += 1
            time.sleep(retry)
            return self.login(retry, icode, re, rn, rk)

        self.uid = int(cookies['id'])
        logger.info('login success with {email} as {uid}'.format(email=self.email, uid=self.uid))

        self.check_login()
        return True

    def login(self, retry=0):
        if retry >= config.RETRY_TIMES:
            raise Exception("Cannot login")

        headers = self.load_headers()
        if headers is None or retry > 0:  # retry > 0 meaning we need new headers
            headers = self.input_headers()
            headers['accept'] = 'application/json,' + headers['accept'] # needed for gossip
        self.session.headers = headers

        self.uid = self.get_uid()

        from .utils import check_login
        if not check_login():
            logger.fatal('login fail, retry')
            self.login(retry+1)
        else:
            self.dump_headers()
            logger.info('login success as {uid}'.format(uid=self.uid))


