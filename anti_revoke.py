from nonebot import *
import asyncio
import json
import os
import re
import time
from pathlib import Path

from hoshino import Service, aiorequests

bot=get_bot()
sv = Service('防撤回', visible= False, enable_on_default= False, bundle='防撤回', help_='''
- 防止撤回desi~
'''.strip())

# NapCat 在消息撤回后 get_msg 可能取不到正文，改为消息到达时就缓存（参考 astrbot_plugin_anti_revoke 的做法）
# kind: 'cq' = CQ字符串（真实CQ码转占位符、其余原样）；'esc' = 已拼好的占位文本（只需转义）
_cfg_default = {
    'image_recall': True,      # 图片防撤回：收到消息时把图片缓存到磁盘，撤回时重发
    'cache_dir': 'cache',      # 图片缓存目录（相对插件目录）
    'cache_ttl_hours': 6,      # 缓存保留时长（内存与磁盘一致）
    'max_image_mb': 10,        # 单图大小上限
    'max_images_per_msg': 9,   # 单条消息最多缓存的图片数
}
_cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
try:
    with open(_cfg_path, 'r', encoding='utf-8') as f:
        CFG = {**_cfg_default, **json.load(f)}
except Exception:
    CFG = dict(_cfg_default)

RECALL_CACHE = {}   # message_id -> [time.monotonic(), kind, content, 发送者昵称, 图片本地路径list]
CACHE_TTL = CFG['cache_ttl_hours'] * 3600
CACHE_MAX = 1024
IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), CFG['cache_dir'])
os.makedirs(IMG_DIR, exist_ok=True)

CQ_RE = re.compile(r'\[CQ:([a-zA-Z0-9_.\-]+)[^\]]*\]')

def _escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('[', '&#91;').replace(']', '&#93;')

def _render(kind: str, content: str) -> str:
    # 转义防正文里的 [ ] 被二次解析成CQ码；'cq' 的原有 &#91; 等转义序列保持原样
    if not content:
        return ''
    if kind == 'cq':
        return CQ_RE.sub(lambda m: _escape(f'[{m.group(1)}]'), content)
    return _escape(content)

def _seg_arr_text(segments) -> str:
    # 消息段列表（dict 或 nonebot MessageSegment）转回显文本
    parts = []
    for s in segments:
        if isinstance(s, dict):
            t = s.get('type', '')
            d = s.get('data') or {}
            if t == 'text':
                parts.append(d.get('text', ''))
            elif t == 'at':
                parts.append('@' + str(d.get('qq', '')))
            elif t == 'face':
                parts.append('[表情]')
            elif t == 'image':
                parts.append('[图片]')
            elif t == 'record':
                parts.append('[语音]')
            elif t == 'video':
                parts.append('[视频]')
            elif t == 'file':
                name = d.get('name')
                parts.append(f'[文件 {name}]' if name else '[文件]')
            elif t in ('forward', 'node'):
                parts.append('[合并转发]')
            elif t == 'json':
                parts.append('[JSON/小程序]')
            elif t == 'xml':
                parts.append('[XML卡片]')
            else:
                parts.append(f'[{t}]' if t else '[未知消息]')
        else:
            parts.append(_render('cq', str(s)))
    return ''.join(parts)

def _pick_entry(raw, segments):
    if raw:
        return ('cq', raw)
    if isinstance(segments, list):
        return ('esc', _seg_arr_text(segments))
    if segments:
        return ('cq', str(segments))
    return ('esc', '')

def _sniff_ext(b: bytes) -> str:
    if b[:3] == b'\xff\xd8\xff':
        return '.jpg'
    if b[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if b[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return '.webp'
    if b[:2] == b'BM':
        return '.bmp'
    return '.jpg'

def _sweep_img_dir():
    try:
        cutoff = time.time() - CACHE_TTL
        for fn in os.listdir(IMG_DIR):
            p = os.path.join(IMG_DIR, fn)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass

def _cache_trim():
    if len(RECALL_CACHE) <= CACHE_MAX:
        return
    now = time.monotonic()
    for k in [k for k, v in RECALL_CACHE.items() if now - v[0] > CACHE_TTL]:
        del RECALL_CACHE[k]
    while len(RECALL_CACHE) > CACHE_MAX:
        RECALL_CACHE.pop(next(iter(RECALL_CACHE)))
    _sweep_img_dir()

async def _cache_images(mid, segments) -> list:
    max_mb = CFG['max_image_mb'] * 1024 * 1024
    max_n = CFG['max_images_per_msg']
    imgs = []
    for seg in segments:
        if len(imgs) >= max_n:
            break
        if not isinstance(seg, dict) or seg.get('type') != 'image':
            continue
        url = (seg.get('data') or {}).get('url')
        if not url:
            continue
        try:
            resp = await aiorequests.get(url, timeout=15)
            content = await resp.content
            if not content or len(content) > max_mb:
                continue
            p = os.path.join(IMG_DIR, f'{mid}_{len(imgs)}{_sniff_ext(content)}')
            with open(p, 'wb') as f:
                f.write(content)
            imgs.append(p)
        except Exception as e:
            sv.logger.warning(f'防撤回: 图片缓存失败 mid={mid}: {type(e).__name__}: {e}')
    return imgs

async def _dl_to_entry(mid, segments):
    try:
        imgs = await _cache_images(mid, segments)
        ent = RECALL_CACHE.get(mid)
        if ent is not None and imgs:
            ent[4] = imgs
    except Exception as e:
        sv.logger.warning(f'防撤回: 图片缓存任务异常 mid={mid}: {type(e).__name__}: {e}')

@sv.on_message('group')
async def cache_for_recall(bot, ctx):
    mid = ctx.get('message_id')
    if mid is None:
        return
    kind, content = _pick_entry(ctx.get('raw_message'), ctx.get('message'))
    name = (ctx.get('sender') or {}).get('nickname') or ''
    RECALL_CACHE[mid] = [time.monotonic(), kind, content, name, []]
    _cache_trim()
    if CFG.get('image_recall'):
        segs = [s for s in (ctx.get('message') or []) if isinstance(s, dict)]
        if any(s.get('type') == 'image' for s in segs):
            asyncio.ensure_future(_dl_to_entry(mid, segs))

@sv.on_notice('group_recall')
async def send_recalled(session: NoticeSession):
    ev = session.event
    pid = ev['operator_id']
    if pid == ev['self_id']:
        return
    _sweep_img_dir()
    mid = ev['message_id']
    kind, content, name, imgs = 'esc', '', '', []
    hit = RECALL_CACHE.pop(mid, None)
    if hit:
        _, kind, content, name, imgs = hit
    else:
        try:
            msgs = await bot.get_msg(message_id=mid)
            kind, content = _pick_entry(msgs.get('raw_message'), msgs.get('message'))
            name = (msgs.get('sender') or {}).get('nickname') or ''
            if CFG.get('image_recall') and isinstance(msgs.get('message'), list):
                segs = [s for s in msgs['message'] if isinstance(s, dict)]
                if any(s.get('type') == 'image' for s in segs):
                    imgs = await _cache_images(mid, segs)
        except Exception as e:
            sv.logger.error(f'防撤回: 获取被撤回消息 {mid} 失败: {e}')
    text = _render(kind, content)
    if not text:
        text = '（无文本内容）'
        sv.logger.info(f'防撤回: message_id={mid} 缓存{"命中" if hit else "未命中"}但内容为空')
    if len(text) > 400:
        text = text[:400] + '…'
    img_cqs = ''.join(f'[CQ:image,file={Path(p).as_uri()}]' for p in imgs if os.path.isfile(p))
    await session.send(f'{MessageSegment.at(pid)}撤回了{name or ev["user_id"]}的消息\n>{text}{img_cqs}')
