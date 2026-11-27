"""Sign in directly at vc.ru and store the browser state in a GitHub Actions secret."""

import json
import shutil
import subprocess

from playwright.sync_api import sync_playwright
from vc_editor import EDITOR, TITLE_FIELDS, launch

REPO = 'proavitology-droid/telegram-vc-autopost'


def main():
    if not shutil.which('gh'):
        raise SystemExit('Нужен GitHub CLI (gh) и вход в ваш аккаунт GitHub.')
    login = subprocess.run(['gh', 'api', 'user', '--jq', '.login'], check=True,
                           capture_output=True, text=True).stdout.strip()
    if login.lower() != REPO.split('/')[0].lower():
        raise SystemExit(f'В GitHub CLI нужно войти как {REPO.split("/")[0]}. Сейчас используется {login}.')
    with sync_playwright() as p:
        browser = launch(p.chromium, headed=True)
        context = browser.new_context(locale='ru-RU')
        tab = context.new_page()
        tab.goto('https://vc.ru/', wait_until='domcontentloaded')
        input('Войдите в свой аккаунт на vc.ru в открывшемся окне, затем нажмите Enter здесь. ')
        tab.goto(EDITOR, wait_until='domcontentloaded')
        tab.wait_for_selector(f'{TITLE_FIELDS}, [contenteditable="true"]', timeout=12000)
        state = context.storage_state()
        cookies = [cookie for cookie in state['cookies']
                   if cookie['domain'].lstrip('.') == 'vc.ru'
                   or cookie['domain'].lstrip('.').endswith('.vc.ru')]
        origins = [origin for origin in state['origins'] if origin['origin'] == 'https://vc.ru']
        if not cookies:
            raise SystemExit('Сессия vc.ru не найдена.')
        secret = json.dumps({'cookies': cookies, 'origins': origins}, ensure_ascii=False)
        subprocess.run(['gh', 'secret', 'set', 'VC_STORAGE_STATE', '--repo', REPO],
                       input=secret.encode(), check=True)
    print('Сессия добавлена в секрет GitHub Actions.')


if __name__ == '__main__':
    main()
