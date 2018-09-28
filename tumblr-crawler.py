# coding:utf-8
"""
Created by tzw0745 at 18-9-28
"""
import json
import os
import re
import time
import traceback
from threading import Thread

import requests
from lxml import etree, html

try:
    # Python3 import
    from urllib.parse import urlsplit
    from queue import Queue
except ImportError:
    # Python2 import
    from urlparse import urlparse as urlsplit
    from Queue import Queue

task_down = Queue()  # 待下载队列
task_fail = Queue()  # 下载失败队列
stop_sign = False  # 线程停止信号


def task_handler(thread_name, session, overwrite=False, interval=0.5):
    """
    持续下载文件，直到stop_sing为True
    :param thread_name: 线程名称，用于输出
    :param session: request.Session
    :param overwrite: 是否覆盖已存在文件
    :param interval: 单个进程下载文件的间隔，减少出现异常的概率
    :return:
    """
    if not isinstance(session, requests.Session):
        raise TypeError('Param "session" must be request.Session')

    msg = ' '.join(['Thread', str(thread_name), '{}: {}'])
    global task_down, task_fail, stop_sign
    while not stop_sign:
        if task_down.empty():
            continue
        task_path, task_url = task_down.get()
        # 判断文件是否存在
        if not overwrite and os.path.isfile(task_path):
            print(msg.format('Exists', task_path))
            continue
        # 向url发送请求
        time.sleep(interval)
        try:
            r = session.get(task_url, timeout=3)
        except requests.exceptions.RequestException:
            # 请求失败
            print(msg.format('RequestException', task_path))
            task_fail.put((task_path, task_url))
            continue
        # 写入文件
        chunk_size = 10 * 1024 * 1024  # 10M缓存
        try:
            with open(task_path, 'wb') as f:
                for content in r.iter_content(chunk_size=chunk_size):
                    f.write(content)
        except (IOError, OSError):
            print(msg.format('IO/OSError', task_path))
            task_fail.put((task_path, task_url))
            continue
        print(msg.format('Completed', task_path))


def tumblr_posts(session, site, post_type):
    """
    获取tumblr博客下所有的文章
    :param session: request.Session，用于发送请求
    :param site: 站点id
    :param post_type: 文章类型，包括photo和video
    :return: 文章列表迭代器
    """
    if not isinstance(session, requests.Session):
        raise TypeError('Param "s" must be requests.Session')
    if not re.match(r'^[a-zA-Z0-9_]+$', site):
        raise ValueError('Param "site" not match "^[a-zA-Z0-9_]+$"')
    if post_type not in ('photo', 'video'):
        raise ValueError('Param "post_type" must be "photo" or "video"')

    def _max_width_sub(node, sub_name):
        """
        获取node下max-width属性最大的子节点的文本
        :param node: xml父节点
        :param sub_name: 子节点名称
        :return: 子节点的文本
        """
        return sorted(
            node.findall(sub_name),
            key=lambda _i: int(_i.get('max-width', '0'))
        )[-1].text

    page_size, start = 50, 0
    while True:
        api = 'http://{}.tumblr.com/api/read'.format(site)
        params = {'type': post_type, 'num': page_size, 'start': start}
        start += page_size
        # 获取文章列表
        r = session.get(api, params=params, timeout=3)
        posts = etree.fromstring(r.content).find('posts').findall('post')
        if not posts:
            break

        for post in posts:
            post_info = {
                'id': post.get('id'),
                'date': post.get('date-gmt'),
                'type': post_type
            }
            if post_type == 'photo':
                # 获取文章下所有图片链接
                photos = []
                for photo_set in post.iterfind('photoset'):
                    for photo in photo_set.iterfind('photo'):
                        photos.append(_max_width_sub(photo, 'photo-url'))
                first_photo = _max_width_sub(post, 'photo-url')
                photos.append(first_photo) if first_photo not in photos else None
                post_info['photos'] = photos
                yield post_info
            elif post_type == 'video':
                # 获取视频链接
                video_ext = post.find('video-source').find('extension').text
                tree = html.fromstring(_max_width_sub(post, 'video-player'))
                options = json.loads(tree.get('data-crt-options'))
                post_info.update({'video': options['hdUrl'], 'ext': video_ext})
                yield post_info


def main():
    site = 'liamtbyrne'
    save_dir = os.path.expanduser('~/Pictures/liamtbyrne')
    os.mkdir(save_dir) if not os.path.isdir(save_dir) else None

    session = requests.session()
    proxy = 'socks5h://127.0.0.1:1080'
    session.proxies = {'http': proxy, 'https': proxy}

    worker_num = 5
    retry_num = 3
    # 创建线程池
    worker_pool = []
    for i in range(worker_num):
        _t = Thread(target=task_handler, args=(i, session))
        _t.setDaemon(True)
        _t.start()
        worker_pool.append(_t)

    global task_down, task_fail, stop_sign
    for post in tumblr_posts(session, site, 'photo'):
        post_id, date = post['id'], post['date']
        # 将图片url加入下载队列
        for photo_url in post['photos']:
            photo_name = os.path.split(urlsplit(photo_url).path)[-1]
            photo_name = '{}.{}.{}'.format(date, post_id, photo_name)
            photo_path = os.path.join(save_dir, photo_name)
            task_down.put((photo_path, photo_url))
    for post in tumblr_posts(session, site, 'video'):
        # 将视频url加入下载队列
        video_name = '{i[date]}.{i[id]}.{i[ext]}'.format(i=post)
        video_path = os.path.join(save_dir, video_name)
        task_down.put((video_path, post['video']))

    for _retry in range(retry_num):
        # 下载队列清空后停止所有下载线程
        while not task_down.empty():
            continue
        stop_sign = True
        if task_fail.empty():
            break
        # 存在下载失败任务则重试
        task_down, task_fail = task_fail, task_down
        stop_sign = False
        for worker in worker_pool:
            worker.start()


if __name__ == '__main__':
    try:
        print(time.asctime().rjust(80, '-'))
        main()
        print('\nall done')
    except Exception as e:
        print(''.join([str(e), traceback.format_exc()]))
    finally:
        print(time.asctime().rjust(80, '-'))