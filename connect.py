"""Sign in directly at vc.ru and store the browser state in a GitHub Actions secret."""

import json
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright
from vc_editor import BASE, launch

REPO = 'proavitology-droid/telegram-vc-autopost'


def is_signed_in(tab):
    response = tab.goto(f'{BASE}/settings', wait_until='domcontentloaded')
    address = urlsplit(tab.url)
    return (response is not None and response.ok and address.hostname == 'vc.ru'
            and (address.path == '/settings' or address.path.startswith('/settings/'))
            and 'auth' not in parse_qs(address.query).get('modal', ()))


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
        tab.goto(BASE, wait_until='domcontentloaded')
        while True:
            input('Дождитесь, пока на vc.ru появится ваш профиль, затем нажмите Enter здесь. ')
            if is_signed_in(tab):
                break
            print('Вход в vc.ru ещё не подтверждён. Завершите вход в открытом окне.')
            tab.bring_to_front()
        state = context.storage_state()
        cookies = [cookie for cookie in state['cookies']
                   if cookie['domain'].lstrip('.') == 'vc.ru'
                   or cookie['domain'].lstrip('.').endswith('.vc.ru')]
        origins = [origin for origin in state['origins'] if origin['origin'] == BASE]
        if not cookies:
            raise SystemExit('Сессия vc.ru не найдена.')
        session = {'cookies': cookies, 'origins': origins}
        verification = browser.new_context(locale='ru-RU', storage_state=session)
        if not is_signed_in(verification.new_page()):
            raise SystemExit('Сессия vc.ru не открылась при повторной проверке.')
        verification.close()
        secret = json.dumps(session, ensure_ascii=False)
        subprocess.run(['gh', 'secret', 'set', 'VC_STORAGE_STATE', '--repo', REPO],
                       input=secret.encode(), check=True)
    print('Сессия добавлена в секрет GitHub Actions.')


if __name__ == '__main__':
    main()
