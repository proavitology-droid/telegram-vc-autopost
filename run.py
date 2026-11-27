"""Run one safe publishing cycle and keep its cursor in the repository."""

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from telegram_feed import page, since

STATE = Path(__file__).with_name('state.json')


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'state.json'], check=True)
    subprocess.run(['git', 'commit', '-qm', 'Обновить очередь публикаций'], check=True)
    subprocess.run(['git', 'push', 'origin', 'HEAD:main'], check=True)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding='utf-8'))
    return None


def record_publication(state, post_id, url):
    state['cursor'] = post_id
    state['pending'] = None
    state['last_publication'] = {'telegram_id': post_id, 'vc_url': url}
    save_state(state)
    print(f'Опубликовано: {url}')


def run(mode):
    state = load_state()
    if mode == 'check':
        posts = page()
        print(f'Telegram: {len(posts)} записей доступны, последняя #{posts[-1].id}.')
        from vc_editor import check_editor
        check_editor()
        return
    if mode == 'initialize':
        if state is not None:
            raise RuntimeError('Точка начала уже сохранена.')
        newest = page()[-1].id
        save_state({'cursor': newest, 'pending': None})
        print(f'Записи после Telegram #{newest} попадут в очередь.')
        return

    if mode == 'draft':
        post = next((p for p in reversed(page()) if p.text and not p.needs_review), None)
        if not post:
            raise RuntimeError('В ленте нет записи, подходящей для тестового черновика.')
    else:
        if state is None:
            raise RuntimeError('Сначала установите точку начала запуском initialize.')
        publish_from = datetime.fromisoformat(state['publish_from']) if state.get('publish_from') else None
        if publish_from and datetime.now(timezone.utc) < publish_from:
            print(f'Публикация начнётся с {publish_from.isoformat()}.')
            return
        pending = state['pending']
        if pending:
            from vc_editor import find_publication
            url = find_publication(pending['id'], pending['title'])
            if not url:
                raise RuntimeError(f'Проверьте в блоге запись Telegram #{pending["id"]} перед повтором.')
            record_publication(state, pending['id'], url)
            return
        posts = since(state['cursor'])
        if publish_from:
            cursor = state['cursor']
            for post in posts:
                if post.published_at is None or post.published_at.utcoffset() is None:
                    raise RuntimeError(f'Не удалось проверить дату записи Telegram #{post.id}.')
                if post.published_at >= publish_from:
                    break
                cursor = post.id
            if cursor != state['cursor']:
                state['cursor'] = cursor
                save_state(state)
                posts = [post for post in posts if post.id > cursor]
        if not posts:
            print('Новых записей нет.')
            return
        post = posts[0]
        if post.needs_review:
            state['cursor'] = post.id
            state.setdefault('review', []).append(post.source)
            save_state(state)
            raise RuntimeError(f'Запись добавлена в список для ручной проверки: {post.source}')

    storage_state = os.environ.get('VC_STORAGE_STATE', '')
    if not storage_state:
        raise RuntimeError('В GitHub Actions нужно подключить сессию vc.ru.')
    from vc_editor import edit_post
    if mode == 'draft':
        draft_url = edit_post(post, storage_state)
        print(f'Черновик для проверки: {draft_url}')
        return

    def before_publish():
        state['pending'] = {'id': post.id, 'title': post.title}
        save_state(state)

    url = edit_post(post, storage_state, before_publish=before_publish)
    record_publication(state, post.id, url)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['check', 'initialize', 'draft', 'publish'])
    arguments = parser.parse_args()
    run(arguments.mode)
