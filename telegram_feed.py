"""Read the public web preview of a single Telegram channel."""

import html
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

CHANNEL = 'otchayny_avitolog'
FEED_URL = f'https://t.me/s/{CHANNEL}'
POST_ROOT = f'https://t.me/{CHANNEL}/'
USER_AGENT = 'Mozilla/5.0 (compatible; Telegram to vc.ru publisher)'
SAFE_TAGS = {'a', 'b', 'strong', 'i', 'em', 'u', 's', 'del', 'br', 'code', 'pre', 'blockquote', 'p', 'ul', 'ol', 'li'}
PHOTO_URL = re.compile(r'''url\(\s*['"]?(https://[^'"\)\s]+)''')


@dataclass(frozen=True)
class Post:
    id: int
    title: str
    text: str
    markup: str
    photos: tuple[str, ...]
    needs_review: bool
    published_at: datetime | None = None

    @property
    def source(self):
        return f'{POST_ROOT}{self.id}'


def download(url, limit, timeout=12):
    request = Request(url, headers={'User-Agent': USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        content = response.read(limit + 1)
        if len(content) > limit:
            raise ValueError('Ответ превышает допустимый размер.')
        return response.headers.get_content_type(), content


def parse_page(raw_html):
    soup = BeautifulSoup(raw_html, 'html.parser')
    posts = []
    for message in soup.select(f'.tgme_widget_message[data-post^="{CHANNEL}/"]'):
        post_id = int(message['data-post'].rsplit('/', 1)[1])
        date = message.select_one('.tgme_widget_message_date time[datetime]')
        published_at = datetime.fromisoformat(date['datetime'].replace('Z', '+00:00')) if date else None
        content = message.select_one('.tgme_widget_message_text')
        cleaned = BeautifulSoup(content.decode_contents() if content else '', 'html.parser')
        for element in cleaned.find_all(['script', 'style', 'iframe', 'object']):
            element.decompose()
        for element in cleaned.find_all(True):
            if element.name not in SAFE_TAGS:
                element.unwrap()
                continue
            href = element.get('href', '')
            element.attrs = {}
            if element.name == 'a' and urlsplit(href).scheme in {'http', 'https'}:
                element['href'] = href
        text_markup = str(cleaned)
        for line_break in cleaned.find_all('br'):
            line_break.replace_with('\n')
        body = cleaned.get_text().strip()
        title = next((line.strip() for line in body.splitlines() if line.strip()), '')
        if len(title) > 120:
            title = title[:117].rstrip() + '…'
        photos = []
        for photo in message.select('.tgme_widget_message_photo_wrap'):
            match = PHOTO_URL.search(photo.get('style', ''))
            if match:
                photos.append(match.group(1))
        needs_review = bool(message.select_one(
            '.tgme_widget_message_video, .tgme_widget_message_document, .tgme_widget_message_voice,'
            ' .tgme_widget_message_poll, .tgme_widget_message_forwarded_from,'
            ' .tgme_widget_message_sticker, .tgme_widget_message_unsupported'
        ))
        source = f'{POST_ROOT}{post_id}'
        markup = f'<p>{text_markup}</p><p><a href="{html.escape(source)}">Оригинал в Telegram</a></p>'
        posts.append(Post(post_id, title or 'Публикация из Telegram', body, markup,
                          tuple(dict.fromkeys(photos)), needs_review or not (body or photos), published_at))
    return sorted(posts, key=lambda post: post.id)


def page(before=None):
    url = FEED_URL if before is None else f'{FEED_URL}?{urlencode({"before": before})}'
    _, result = download(url, limit=3_000_000)
    posts = parse_page(result)
    if not posts and before is None:
        raise RuntimeError('Telegram не вернул публичные записи канала.')
    return posts


def since(cursor):
    """Return all reachable newer posts oldest first, even after a missed run."""
    pending = {}
    before = None
    for _ in range(12):
        posts = page(before)
        if not posts:
            return sorted(pending.values(), key=lambda post: post.id)
        pending.update({post.id: post for post in posts if post.id > cursor})
        oldest = posts[0].id
        if oldest <= cursor:
            return sorted(pending.values(), key=lambda post: post.id)
        if before is not None and oldest >= before:
            raise RuntimeError('Telegram повторил страницу. Очередь сохранена.')
        before = oldest
    raise RuntimeError('Слишком много страниц новых записей. Очередь сохранена.')
