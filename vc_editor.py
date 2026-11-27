"""Use the normal vc.ru editor and verify the destination before publishing."""

import base64
import json
import os
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as BrowserTimeout, expect, sync_playwright

from telegram_feed import POST_ROOT, download

BASE = 'https://vc.ru'
EDITOR = f'{BASE}/editor'
BLOG = f'{BASE}/id795193'
BLOG_PATHS = {'/id795193', '/otchayny_avitolog'}
BLOG_NAME = 'ОтЧАЙный Авитолог'
ARTICLE_PATH = re.compile(r'^/[^/]+/\d+(?:-[^/]+)?/?$')
DRAFT_PATH = re.compile(r'^/editor/\d+/?$')
TITLE_FIELDS = (
    '[contenteditable="true"][data-placeholder*="Заголовок"],'
    '[contenteditable="true"][data-placeholder*="заголовок"],'
    '[contenteditable="true"][aria-label*="Заголовок"],'
    'textarea[placeholder*="Заголовок"], input[placeholder*="Заголовок"]'
)


def launch(browser_type, *, headed=False):
    executable_path = os.environ.get('VC_CHROME_PATH')
    options = {'executable_path': executable_path} if executable_path else {'channel': 'chrome'}
    return browser_type.launch(headless=not headed, **options)


def check_editor():
    with sync_playwright() as p:
        browser = launch(p.chromium)
        tab = browser.new_page(locale='ru-RU')
        response = tab.goto(EDITOR, wait_until='domcontentloaded', timeout=16000)
        if response is None or not response.ok or urlsplit(tab.url).hostname != 'vc.ru':
            raise RuntimeError('Chrome не смог открыть редактор vc.ru.')
        print('vc.ru: Chrome открыл страницу редактора.')


def compact(text):
    return ' '.join(text.split())


def article_link(candidate):
    url = urlsplit(urljoin(BASE, candidate))
    if url.scheme == 'https' and url.netloc == 'vc.ru' and ARTICLE_PATH.fullmatch(url.path):
        return BASE + url.path.rstrip('/')
    return None


def matches_article(url, post_id):
    _, document = download(url, limit=8_000_000, timeout=6)
    targets = []
    for link in BeautifulSoup(document, 'html.parser').select('a[href]'):
        target = urlsplit(urljoin(BASE, link['href']))
        if target.hostname == 'api.vc.ru':
            targets.extend(value for values in parse_qs(target.query).values() for value in values)
        targets.append(target.geturl())
    return (any(target.rstrip('/') == f'{POST_ROOT}{post_id}' for target in targets)
            and any(urlsplit(target).hostname == 'vc.ru'
                    and urlsplit(target).path.rstrip('/') in BLOG_PATHS for target in targets))


def find_publication(post_id, title):
    """Check the public blog before retrying a request with an uncertain outcome."""
    _, document = download(BLOG, limit=8_000_000)
    links = BeautifulSoup(document, 'html.parser').select('a[href]')
    candidates = dict.fromkeys(article_link(link['href']) for link in links
                              if compact(link.get_text()) == compact(title))
    return next((url for url in candidates if url and matches_article(url, post_id)), None)


def confirm_blog(tab):
    """Require the blog name in the editor before making any content changes."""
    for target in (tab.get_by_text(BLOG_NAME, exact=True),
                   tab.get_by_role('button', name=BLOG_NAME, exact=True),
                   tab.get_by_role('link', name=BLOG_NAME, exact=True)):
        if target.count() and target.first.is_visible():
            return
    raise RuntimeError('В редакторе не удалось подтвердить блог «ОтЧАЙный Авитолог».')


def paste_text(context, tab, target, post):
    context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=BASE)
    tab.evaluate('''async ({markup, text}) => {
        await navigator.clipboard.write([new ClipboardItem({
            'text/html': new Blob([markup], {type: 'text/html'}),
            'text/plain': new Blob([text], {type: 'text/plain'})
        })]);
    }''', {'markup': post.markup, 'text': f'{post.text}\n\nОригинал в Telegram: {post.source}'})
    target.click()
    tab.keyboard.press('Control+v')
    expect(tab.get_by_text('Оригинал в Telegram', exact=True).first).to_be_visible(timeout=7000)
    if post.text:
        excerpt = compact(post.text)[:36].strip()
        if excerpt and excerpt not in compact(tab.locator('body').inner_text(timeout=3000)):
            raise RuntimeError('Редактор не показал текст ожидаемой записи.')


def paste_photo(tab, url):
    mime, image = download(url, limit=10_000_000, timeout=8)
    if mime not in {'image/jpeg', 'image/png', 'image/webp'}:
        raise RuntimeError('Telegram вернул неподдерживаемый формат изображения.')
    link = tab.get_by_text('Оригинал в Telegram', exact=True).first
    link.evaluate('''element => {
        const block = element.closest('[contenteditable="true"]');
        if (!block) throw new Error('Не найден редактируемый блок.');
        const range = document.createRange();
        range.selectNodeContents(block);
        range.collapse(false);
        block.focus();
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
    }''')
    tab.keyboard.press('Enter')
    tab.evaluate('''async ({mime, encoded}) => {
        const image = new Image();
        image.src = `data:${mime};base64,${encoded}`;
        await image.decode();
        const canvas = document.createElement('canvas');
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        canvas.getContext('2d').drawImage(image, 0, 0);
        const png = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        await navigator.clipboard.write([new ClipboardItem({'image/png': png})]);
    }''', {'mime': mime, 'encoded': base64.b64encode(image).decode('ascii')})
    image_selector = 'img[src*="leonardo.osnova.io"], img[src*="leonardo.osnova"]'
    images = tab.locator(image_selector)
    before = images.count()
    tab.keyboard.press('Control+v')
    expect(images).to_have_count(before + 1, timeout=12000)
    expect(images.nth(before)).to_be_visible(timeout=5000)


def edit_post(post, storage_state, before_publish=None):
    """Save a draft or publish it after persisting the retry marker."""
    with sync_playwright() as p:
        browser = launch(p.chromium)
        context = browser.new_context(storage_state=json.loads(storage_state), locale='ru-RU')
        tab = context.new_page()
        tab.set_default_timeout(9000)
        tab.goto(EDITOR, wait_until='domcontentloaded', timeout=16000)
        try:
            tab.wait_for_selector(f'{TITLE_FIELDS}, [contenteditable="true"]', timeout=10000)
        except BrowserTimeout:
            if 'modal=auth' in tab.url:
                raise RuntimeError('Сессия vc.ru истекла. Нужно снова войти в аккаунт.') from None
            raise RuntimeError('Редактор vc.ru не показал поля публикации.') from None
        confirm_blog(tab)

        fields = tab.locator('[contenteditable]:not([contenteditable="false"]):visible')
        title = tab.locator(TITLE_FIELDS).filter(visible=True)
        body = fields.first
        if title.count() == 0 and fields.count() >= 2:
            title = fields.first
            body = fields.nth(1)
        elif title.count() == 1 and title.evaluate('(element) => element.isContentEditable'):
            body = fields.nth(1)
        if title.count() != 1:
            raise RuntimeError('Не удалось определить поле заголовка vc.ru.')
        title.fill(post.title)
        if body.count() == 0:
            title.press('Enter')
        if body.count() == 0:
            structure = tab.locator('[contenteditable], [role="textbox"], textarea, [data-placeholder]').evaluate_all('''
                elements => elements.slice(0, 25).map(element => ({
                    tag: element.tagName, editable: element.getAttribute('contenteditable'),
                    role: element.getAttribute('role'), class: Array.from(element.classList),
                    visible: Boolean(element.getClientRects().length)
                }))
            ''')
            print(f'Поля редактора: {structure}', flush=True)
        paste_text(context, tab, body, post)
        for photo in reversed(post.photos):
            paste_photo(tab, photo)
        source = tab.get_by_role('link', name='Оригинал в Telegram')
        expect(source.first).to_have_attribute('href', post.source)

        if before_publish is None:
            tab.wait_for_url(lambda url: bool(DRAFT_PATH.fullmatch(urlsplit(url).path)), timeout=10000)
            return BASE + urlsplit(tab.url).path

        publish = tab.get_by_role('button', name='Опубликовать', exact=True).filter(visible=True)
        expect(publish).to_have_count(1)
        expect(publish).to_be_enabled()
        before_publish()
        publish.click()
        confirmation = tab.get_by_role('dialog').get_by_role('button', name='Опубликовать', exact=True)
        try:
            confirmation.wait_for(state='visible', timeout=2000)
            confirmation.click()
        except BrowserTimeout:
            pass
        tab.wait_for_url(lambda url: article_link(url) is not None, timeout=15000)
        published_url = article_link(tab.url)
        if not matches_article(published_url, post.id):
            raise RuntimeError('Получена ссылка, но статья пока не подтверждена в открытом блоге.')
        return published_url
