import os
import copy
import asyncio
import json
import logging
import re
import html as _html
from datetime import datetime
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError,
    ChatWriteForbiddenError, PhoneCodeInvalidError,
    MessageNotModifiedError
)
from telethon.tl.types import InputPeerChannel, InputPeerChat
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.extensions import html as tl_html

# =======================================================
# 설정
# =======================================================
API_ID     = 38892349
API_HASH   = '6ba32569c9e7a8bb1317ddd2c5a1e556'
BOT_TOKEN  = "8645010603:AAEZOQj-dJQn5KarAPxCHf_2NGOGxQrgSYI"
SUPER_ADMINS = [8400579076]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE   = os.path.join(BASE_DIR, 'kkomi_v24_db.json')
LOG_FILE    = os.path.join(BASE_DIR, 'system_log.txt')
MEDIA_DIR   = os.path.join(BASE_DIR, 'ad_media')

if not os.path.exists(MEDIA_DIR):
    os.makedirs(MEDIA_DIR)

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

broadcast_locks = {}
user_states     = {}
temp_clients    = {}
scheduler_tasks = {}

# =======================================================
# DB
# =======================================================
class DB:
    DEFAULTS = {
        "accounts": [],
        "admins": [],
        "bot_session_str": ""
    }
    ACC_DEFAULTS = {
        "session_str": "",
        "phone": "",
        "name": "",
        "ad_msg": None,
        "media_path": None,
        "groups": [],
        "links": [],
        "interval": 60,
        "running": False,
        "stats": {"success": 0, "fail": 0, "last_run": "-"}
    }

    @staticmethod
    def load():
        if not os.path.exists(DATA_FILE):
            return copy.deepcopy(DB.DEFAULTS)
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k, v in DB.DEFAULTS.items():
                if k not in data:
                    data[k] = copy.deepcopy(v)
            return data
        except Exception:
            return copy.deepcopy(DB.DEFAULTS)

    @staticmethod
    def save(data):
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"DB 저장 오류: {e}")

    @staticmethod
    def get_account(idx):
        db = DB.load()
        accs = db.get('accounts', [])
        if 0 <= idx < len(accs):
            acc = accs[idx]
            for k, v in DB.ACC_DEFAULTS.items():
                if k not in acc:
                    acc[k] = copy.deepcopy(v)
            return acc
        return None

    @staticmethod
    def update_account(idx, acc_data):
        db = DB.load()
        if 0 <= idx < len(db['accounts']):
            db['accounts'][idx] = acc_data
            DB.save(db)

# =======================================================
# 클라이언트 초기화
# =======================================================
db_init  = DB.load()
bot_sess = (StringSession(db_init['bot_session_str'])
            if db_init['bot_session_str'] else StringSession())
client   = TelegramClient(bot_sess, API_ID, API_HASH)

# =======================================================
# 유틸
# =======================================================
def is_admin(uid):
    db = DB.load()
    return (uid in SUPER_ADMINS) or (uid in db.get('admins', []))

def get_lock(idx):
    if idx not in broadcast_locks:
        broadcast_locks[idx] = asyncio.Lock()
    return broadcast_locks[idx]

def safe_idx(s, sep='_'):
    try:
        return int(s.split(sep)[-1])
    except Exception:
        return 0

async def safe_edit(event, text, buttons=None):
    try:
        await event.edit(text, buttons=buttons)
    except MessageNotModifiedError:
        pass
    except Exception as e:
        logger.warning(f"safe_edit 오류: {e}")

# =======================================================
# 메인 메뉴
# =======================================================
async def draw_main(event, edit=False):
    db = DB.load()
    accs = db.get('accounts', [])
    running = sum(1 for a in accs if a.get('running'))
    text = (
        f"🎀 **꽃미네 홍보센터 V24** 🎀\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**등록 계정**: {len(accs)}개 | **가동 중**: {running}개\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"계정을 선택해 **개별** 광고·그룹·간격을 설정하세요."
    )
    btns = [
        [Button.inline("🔄 새로고침", b'refresh')],
        [Button.inline("👥 계정 목록", b'view_accounts_0')],
        [Button.inline("👤 계정 추가", b'add_acc')],
        [Button.inline("🛡 관리자 관리", b'admin_menu')],
        [Button.inline("📋 로그 보기", b'view_log_0')],
    ]
    if edit:
        await safe_edit(event, text, btns)
    else:
        await event.respond(text, buttons=btns)

async def draw_accounts(event, page=0, edit=False):
    db = DB.load()
    accs = db.get('accounts', [])
    PER = 6
    total = max(1, (len(accs) + PER - 1) // PER)
    page = max(0, min(page, total - 1))
    start = page * PER
    text = f"👥 **계정 목록** [{page+1}/{total}]\n━━━━━━━━━━━━━━━━━━━━\n"
    if not accs:
        text += "등록된 계정이 없습니다."
    btns = []
    for i, acc in enumerate(accs[start:start+PER], start):
        st = "🟢" if acc.get('running') else "🔴"
        name = _html.escape(acc.get('name') or acc.get('phone', f'계정{i+1}'))
        grp = len(acc.get('groups', []))
        btns.append([Button.inline(
            f"{st} {name} | 그룹 {grp}개",
            f'acc_menu_{i}'.encode()
        )])
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ 이전", f'view_accounts_{page-1}'.encode()))
    if page < total - 1:
        nav.append(Button.inline("▶️ 다음", f'view_accounts_{page+1}'.encode()))
    if nav:
        btns.append(nav)
    btns.append([Button.inline("🏠 메인", b'refresh')])
    if edit:
        await safe_edit(event, text, btns)
    else:
        await event.respond(text, buttons=btns)

async def draw_acc_menu(event, idx, edit=False):
    acc = DB.get_account(idx)
    if acc is None:
        await event.respond("❌ 계정을 찾을 수 없습니다.")
        return
    st = "🟢 가동 중" if acc.get('running') else "🔴 정지됨"
    has_ad = "✅ 설정됨" if acc.get('ad_msg') else "❌ 미설정"
    med_ok = acc.get('media_path') and os.path.exists(acc.get('media_path', ''))
    has_med = "📷 있음" if med_ok else "📄 없음"
    name = _html.escape(acc.get('name') or acc.get('phone', f'계정{idx+1}'))
    text = (
        f"👤 **{name}** (#{idx+1})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**상태**: {st} | **간격**: {acc.get('interval', 60)}분\n"
        f"**그룹**: {len(acc.get('groups',[]))}개 | "
        f"**링크**: {len(acc.get('links',[]))}개\n"
        f"**광고**: {has_ad} | **미디어**: {has_med}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 성공: {acc['stats']['success']} | 실패: {acc['stats']['fail']}\n"
        f"🕒 최근: {acc['stats']['last_run']}"
    )
    btns = [
        [Button.inline("🔄 새로고침", f'acc_menu_{idx}'.encode())],
        [Button.inline("📝 광고 설정", f'set_ad_{idx}'.encode()), Button.inline("🔗 링크 등록", f'bulk_link_{idx}'.encode())],
        [Button.inline("🆔 입장 매핑", f'run_join_{idx}'.encode()), Button.inline("📂 링크 보기", f'view_links_{idx}_0'.encode())],
        [Button.inline("⏱ 간격 설정", f'set_interval_{idx}'.encode())],
        [Button.inline("▶️ 시작", f'start_{idx}'.encode()), Button.inline("⏹ 중지", f'stop_{idx}'.encode())],
        [Button.inline("⚡ 즉시 전송", f'now_{idx}'.encode()), Button.inline("🧹 통계 초기화", f'reset_{idx}'.encode())],
        [Button.inline("🗑 그룹 초기화", f'clear_groups_{idx}'.encode()), Button.inline("🚪 로그아웃", f'logout_{idx}'.encode())],
        [Button.inline("🔙 계정 목록", b'view_accounts_0')],
    ]
    if edit:
        await safe_edit(event, text, btns)
    else:
        await event.respond(text, buttons=btns)

async def draw_links(event, acc_idx, page=0, edit=False):
    acc = DB.get_account(acc_idx)
    if acc is None:
        return
    links = acc.get('links', [])
    PER = 10
    total = max(1, (len(links) + PER - 1) // PER)
    page = max(0, min(page, total - 1))
    start = page * PER
    text = (f"📂 **링크 보관소** 계정{acc_idx+1} "
            f"[{page+1}/{total}]\n━━━━━━━━━━━━━━━━━━━━\n")
    if not links:
        text += "등록된 링크가 없습니다."
    else:
        for i, lk in enumerate(links[start:start+PER], start+1):
            mapped = (f" → `{lk['id']}`" if lk.get('id') else " _(미매핑)_")
            text += f"{i}. {_html.escape(lk['url'])}{mapped}\n"
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ 이전", f'view_links_{acc_idx}_{page-1}'.encode()))
    if page < total - 1:
        nav.append(Button.inline("▶️ 다음", f'view_links_{acc_idx}_{page+1}'.encode()))
    btns = []
    if nav:
        btns.append(nav)
    btns.append([Button.inline("🗑 링크 전체삭제", f'clear_links_{acc_idx}'.encode())])
    btns.append([Button.inline("🔙 뒤로", f'acc_menu_{acc_idx}'.encode())])
    if edit:
        await safe_edit(event, text, btns)
    else:
        await event.respond(text, buttons=btns)

async def draw_admin_menu(event, edit=False):
    db = DB.load()
    admins = db.get('admins', [])
    text = (
        f"🛡 **관리자 관리**\n━━━━━━━━━━━━━━━━━━━━\n"
        f"**슈퍼관리자**: {', '.join(f'`{x}`' for x in SUPER_ADMINS)}\n\n"
        f"**추가 관리자 ({len(admins)}명)**:\n"
    )
    if admins:
        for i, aid in enumerate(admins, 1):
            text += f"{i}. `{aid}`\n"
    else:
        text += "없음\n"
    btns = [
        [Button.inline("➕ 관리자 추가", b'add_admin'), Button.inline("➖ 관리자 삭제", b'del_admin')],
        [Button.inline("🏠 메인", b'refresh')],
    ]
    if edit:
        await safe_edit(event, text, btns)
    else:
        await event.respond(text, buttons=btns)

async def draw_log(event, page=0, edit=False):
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        lines = ["로그 파일이 없습니다.\n"]
    PER = 25
    lines = lines[::-1]
    total = max(1, (len(lines) + PER - 1) // PER)
    page = max(0, min(page, total - 1))
    start = page * PER
    chunk = lines[start:start+PER][::-1]
    body = _html.escape("".join(chunk))
    if len(body) > 3500:
        body = "..." + body[-3500:]
    text = f"📋 **시스템 로그** [{page+1}/{total}]\n`{body}`"
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️ 최신", f'view_log_{page-1}'.encode()))
    if page < total - 1:
        nav.append(Button.inline("▶️ 이전", f'view_log_{page+1}'.encode()))
    btns = []
    if nav:
        btns.append(nav)
    btns.append([Button.inline("🗑 로그 초기화", b'clear_log'), Button.inline("🏠 메인", b'refresh')])
    if edit:
        await safe_edit(event, text, btns)
    else:
        await event.respond(text, buttons=btns)

# =======================================================
# 방송
# =======================================================
async def broadcast(acc_idx, chat_id=None):
    lock = get_lock(acc_idx)
    if lock.locked():
        return
    async with lock:
        acc = DB.get_account(acc_idx)
        if not acc:
            return
        if not acc.get('groups') or not acc.get('ad_msg'):
            if chat_id:
                await client.send_message(chat_id, f"⚠️ 계정{acc_idx+1}: 그룹 또는 광고 메시지를 설정해주세요.")
            return
        success = 0
        fail = 0
        u = TelegramClient(StringSession(acc['session_str']), API_ID, API_HASH)
        try:
            await u.connect()
            if not await u.is_user_authorized():
                logger.warning(f"계정{acc_idx+1} 미인증 — 건너뚁")
                return
            media = None
            if acc.get('media_path') and os.path.exists(acc['media_path']):
                try:
                    media = await u.upload_file(acc['media_path'])
                except Exception as e:
                    logger.error(f"계정{acc_idx+1} 미디어 업로드 실패: {e}")
            for gid_data in acc['groups']:
                raw_id = None
                try:
                    raw_id = int(gid_data['id'])
                    target = (
                        InputPeerChannel(raw_id, int(gid_data.get('hash', 0)))
                        if gid_data['type'] == 'channel'
                        else InputPeerChat(raw_id)
                    )
                    await u.send_message(target, acc['ad_msg']['text'], file=media, parse_mode='html')
                    success += 1
                    await asyncio.sleep(10)
                except ChatWriteForbiddenError:
                    fail += 1
                    logger.error(f"계정{acc_idx+1} 권한없음: {raw_id}")
                except FloodWaitError as e:
                    logger.warning(f"계정{acc_idx+1} FloodWait {e.seconds}초")
                    await asyncio.sleep(e.seconds + 2)
                except Exception as e:
                    fail += 1
                    logger.error(f"계정{acc_idx+1} 전송실패({raw_id}): {e}")
                finally:
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"계정{acc_idx+1} 연결오류: {e}")
        finally:
            await u.disconnect()
        cur = DB.get_account(acc_idx)
        if cur:
            cur['stats']['success'] += success
            cur['stats']['fail'] += fail
            cur['stats']['last_run'] = datetime.now().strftime("%H:%M:%S")
            DB.update_account(acc_idx, cur)
        logger.info(f"계정{acc_idx+1} 전송완료 — 성공:{success} 실패:{fail}")
        if chat_id:
            await client.send_message(chat_id, f"✅ 계정{acc_idx+1} 즉시전송 완료!\n성공: {success} | 실패: {fail}")

async def join_all_links(acc_idx, chat_id):
    acc = DB.get_account(acc_idx)
    if not acc:
        return
    if not acc.get('links'):
        await client.send_message(chat_id, "⚠️ 등록된 링크가 없습니다.")
        return
    joined = 0
    failed = 0
    u = TelegramClient(StringSession(acc['session_str']), API_ID, API_HASH)
    try:
        await u.connect()
        if not await u.is_user_authorized():
            await client.send_message(chat_id, "⚠️ 계정이 인증되지 않았습니다.")
            return
        for lk in acc['links']:
            url = lk['url']
            try:
                if '+' in url.split('t.me/')[-1] or 'joinchat' in url:
                    hash_part = url.split('/')[-1].lstrip('+')
                    result = await u(ImportChatInviteRequest(hash_part))
                else:
                    username = url.split('t.me/')[-1].split('/')[0]
                    result = await u(JoinChannelRequest(username))
                chat_obj = result.chats[0]
                gid = chat_obj.id
                access_hash = getattr(chat_obj, 'access_hash', 0)
                chat_type = ('channel' if hasattr(chat_obj, 'access_hash') else 'group')
                cur = DB.get_account(acc_idx)
                if not any(g['id'] == str(gid) for g in cur['groups']):
                    cur['groups'].append({'id': str(gid), 'hash': str(access_hash), 'type': chat_type, 'url': url})
                for lk2 in cur['links']:
                    if lk2['url'] == url:
                        lk2['id'] = str(gid)
                        break
                DB.update_account(acc_idx, cur)
                joined += 1
                await asyncio.sleep(5)
            except FloodWaitError as e:
                wait = e.seconds + 2
                await client.send_message(chat_id, f"⏳ FloodWait: {wait}초 대기 중...")
                await asyncio.sleep(wait)
                failed += 1
            except Exception as e:
                err = str(e)
                if 'already' in err.lower() or 'USER_ALREADY' in err:
                    joined += 1
                else:
                    failed += 1
                    logger.error(f"입장실패({url}): {e}")
                await asyncio.sleep(3)
    finally:
        await u.disconnect()
    final = DB.get_account(acc_idx)
    await client.send_message(chat_id, f"✅ 입장 완료! 계정{acc_idx+1}\n성공: {joined}개 | 실패: {failed}개\n타겟 그룹: {len(final['groups'])}개")

async def account_scheduler(acc_idx):
    while True:
        try:
            acc = DB.get_account(acc_idx)
            if acc is None:
                break
            if acc.get('running'):
                await broadcast(acc_idx)
                acc = DB.get_account(acc_idx)
                interval = acc.get('interval', 60) if acc else 60
                await asyncio.sleep(interval * 60)
            else:
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"스케줄러({acc_idx}) 오류: {e}")
            await asyncio.sleep(30)

def ensure_scheduler(acc_idx):
    if acc_idx not in scheduler_tasks or scheduler_tasks[acc_idx].done():
        scheduler_tasks[acc_idx] = asyncio.create_task(account_scheduler(acc_idx))

def restart_all_schedulers():
    for t in scheduler_tasks.values():
        t.cancel()
    scheduler_tasks.clear()
    db = DB.load()
    for i in range(len(db.get('accounts', []))):
        ensure_scheduler(i)

@client.on(events.NewMessage(pattern=r'(?i)^/start$'))
async def cmd_start(event):
    if is_admin(event.sender_id):
        await draw_main(event)

@client.on(events.CallbackQuery)
async def cb_handler(event):
    if not is_admin(event.sender_id):
        return
    await event.answer()
    data = event.data.decode()
    try:
        if data == 'refresh':
            await draw_main(event, True)
        elif data.startswith('view_accounts_'):
            await draw_accounts(event, safe_idx(data), True)
        elif data == 'add_acc':
            user_states[event.sender_id] = {'step': 'login_phone'}
            await event.respond("📱 부계정 전화번호를 입력하세요. (+8210...)")
        elif data.startswith('acc_menu_'):
            await draw_acc_menu(event, safe_idx(data), True)
        elif data.startswith('set_ad_'):
            idx = safe_idx(data)
            user_states[event.sender_id] = {'step': 'input_ad', 'acc_idx': idx}
            await event.respond(f"📝 계정{idx+1}의 광고를 보내주세요.\n(사진+텍스트 또는 텍스트만 가능)")
        elif data.startswith('bulk_link_'):
            idx = safe_idx(data)
            user_states[event.sender_id] = {'step': 'input_bulk', 'acc_idx': idx}
            await event.respond(f"🔗 계정{idx+1}의 홍보 링크를 한 줄에 하나씩 보내주세요.")
        elif data.startswith('set_interval_'):
            idx = safe_idx(data)
            acc = DB.get_account(idx)
            cur = acc.get('interval', 60) if acc else 60
            user_states[event.sender_id] = {'step': 'set_interval', 'acc_idx': idx}
            await event.respond(f"⏱ 계정{idx+1}의 전송 간격(분)을 입력하세요. (현재: {cur}분)")
        elif data.startswith('run_join_'):
            idx = safe_idx(data)
            asyncio.create_task(join_all_links(idx, event.chat_id))
            await event.respond(f"🏃 계정{idx+1} 입장을 시작합니다.")
        elif data.startswith('view_links_'):
            parts = data.split('_')
            acc_val = int(parts[2])
            pg = int(parts[3]) if len(parts) > 3 else 0
            await draw_links(event, acc_val, pg, True)
        elif data.startswith('clear_links_'):
            idx = safe_idx(data)
            acc = DB.get_account(idx)
            if acc:
                acc['links'] = []
                DB.update_account(idx, acc)
            await draw_links(event, idx, 0, True)
        elif data.startswith('clear_groups_'):
            idx = safe_idx(data)
            acc = DB.get_account(idx)
            if acc:
                acc['groups'] = []
                DB.update_account(idx, acc)
            await draw_acc_menu(event, idx, True)
        elif data.startswith('start_'):
            idx = safe_idx(data)
            acc = DB.get_account(idx)
            if acc:
                acc['running'] = True
                DB.update_account(idx, acc)
                ensure_scheduler(idx)
            await draw_acc_menu(event, idx, True)
        elif data.startswith('stop_'):
            idx = safe_idx(data)
            acc = DB.get_account(idx)
            if acc:
                acc['running'] = False
                DB.update_account(idx, acc)
            await draw_acc_menu(event, idx, True)
        elif data.startswith('now_'):
            idx = safe_idx(data)
            asyncio.create_task(broadcast(idx, event.chat_id))
            await event.respond(f"⚡ 계정{idx+1} 즉시 전송 시작...")
        elif data.startswith('reset_'):
            idx = safe_idx(data)
            acc = DB.get_account(idx)
            if acc:
                acc['stats'] = {"success": 0, "fail": 0, "last_run": "-"}
                DB.update_account(idx, acc)
            await draw_acc_menu(event, idx, True)
        elif data.startswith('logout_'):
            idx = safe_idx(data)
            db = DB.load()
            if 0 <= idx < len(db['accounts']):
                removed = db['accounts'].pop(idx)
                DB.save(db)
                restart_all_schedulers()
                name = _html.escape(removed.get('name') or removed.get('phone', f'계정{idx+1}'))
                await event.respond(f"🚪 '{name}' 로그아웃 완료")
            await draw_accounts(event, 0, False)
        elif data == 'admin_menu':
            await draw_admin_menu(event, True)
        elif data == 'add_admin':
            user_states[event.sender_id] = {'step': 'add_admin'}
            await event.respond("🛡 추가할 관리자의 텔레그램 ID(숫자)를 입력하세요.")
        elif data == 'del_admin':
            db = DB.load()
            admins = db.get('admins', [])
            if not admins:
                await event.respond("삭제할 관리자가 없습니다.")
                return
            user_states[event.sender_id] = {'step': 'del_admin'}
            lines = "🛡 삭제할 관리자 ID를 입력하세요:\n"
            for i, a in enumerate(admins, 1):
                lines += f"{i}. `{a}`\n"
            await event.respond(lines)
        elif data.startswith('view_log_'):
            await draw_log(event, safe_idx(data), True)
        elif data == 'clear_log':
            try:
                open(LOG_FILE, 'w').close()
            except Exception as e:
                logger.error(f"로그 초기화 오류: {e}")
            await draw_log(event, 0, True)
    except Exception as e:
        logger.error(f"콜백 오류 ({data}): {e}")
        try:
            await event.respond(f"❌ 오류: {_html.escape(str(e))}")
        except Exception:
            pass

@client.on(events.NewMessage)
async def input_handler(event):
    if not is_admin(event.sender_id) or event.raw_text.startswith('/'):
        return
    sd = user_states.get(event.sender_id)
    if not sd:
        return
    step = sd.get('step')
    acc_idx = sd.get('acc_idx', 0)
    try:
        if step == 'input_ad':
            media_path = None
            if event.media:
                try:
                    media_path = await event.download_media(file=MEDIA_DIR)
                except Exception as e:
                    logger.error(f"미디어 다운로드: {e}")
            try:
                html_text = tl_html.unparse(event.message.message or '', event.message.entities or [])
            except Exception:
                html_text = event.raw_text
            acc = DB.get_account(acc_idx)
            if acc:
                acc['ad_msg'] = {'text': html_text}
                if media_path:
                    acc['media_path'] = media_path
                DB.update_account(acc_idx, acc)
            del user_states[event.sender_id]
            await event.reply(f"✅ 계정{acc_idx+1} 광고 설정 완료!")
            await draw_acc_menu(event, acc_idx)
        elif step == 'input_bulk':
            links = re.findall(r'https?://t\.me/\S+', event.raw_text)
            added = 0
            acc = DB.get_account(acc_idx)
            if acc:
                for l in links:
                    l = l.rstrip('.,)>')
                    if not any(x['url'] == l for x in acc['links']):
                        acc['links'].append({"url": l, "id": None})
                        added += 1
                DB.update_account(acc_idx, acc)
                total_cnt = len(acc['links'])
            else:
                total_cnt = 0
            del user_states[event.sender_id]
            await event.reply(f"✅ {added}개 링크 등록 완료! (총 {total_cnt}개)")
            await draw_acc_menu(event, acc_idx)
        elif step == 'set_interval':
            try:
                mins = int(event.raw_text.strip())
                if mins < 1:
                    raise ValueError
                acc = DB.get_account(acc_idx)
                if acc:
                    acc['interval'] = mins
                    DB.update_account(acc_idx, acc)
                del user_states[event.sender_id]
                await event.reply(f"✅ 계정{acc_idx+1} 간격을 {mins}분으로 설정했습니다.")
                await draw_acc_menu(event, acc_idx)
            except ValueError:
                await event.reply("❌ 숫자만 입력하세요. (예: 30)")
        elif step == 'add_admin':
            try:
                new_id = int(event.raw_text.strip())
                if new_id in SUPER_ADMINS:
                    await event.reply("이미 슈퍼관리자입니다.")
                else:
                    db = DB.load()
                    if new_id not in db['admins']:
                        db['admins'].append(new_id)
                        DB.save(db)
                        await event.reply(f"✅ 관리자 `{new_id}` 추가 완료!")
                    else:
                        await event.reply("이미 등록된 관리자입니다.")
                del user_states[event.sender_id]
                await draw_admin_menu(event)
            except ValueError:
                await event.reply("❌ 숫자(텔레그램 ID)를 입력하세요.")
        elif step == 'del_admin':
            try:
                del_id = int(event.raw_text.strip())
                db = DB.load()
                if del_id in db['admins']:
                    db['admins'].remove(del_id)
                    DB.save(db)
                    await event.reply(f"✅ 관리자 `{del_id}` 삭제 완료!")
                else:
                    await event.reply("등록되지 않은 관리자 ID입니다.")
                del user_states[event.sender_id]
                await draw_admin_menu(event)
            except ValueError:
                await event.reply("❌ 숫자(ID)를 입력하세요.")
        elif step == 'login_phone':
            phone = event.raw_text.strip()
            tmp = TelegramClient(StringSession(), API_ID, API_HASH)
            try:
                await tmp.connect()
                sent = await tmp.send_code_request(phone)
                temp_clients[event.sender_id] = {'client': tmp, 'phone': phone, 'phone_code_hash': sent.phone_code_hash}
                user_states[event.sender_id] = {'step': 'login_code'}
                await event.reply(f"📲 {phone}로 인증코드가 전송됐습니다.\n코드를 입력하세요.")
            except Exception as e:
                await tmp.disconnect()
                temp_clients.pop(event.sender_id, None)
                del user_states[event.sender_id]
                await event.reply(f"❌ 전화번호 오류: {e}")
        elif step == 'login_code':
            code = event.raw_text.strip().replace(' ', '')
            tmp_data = temp_clients.get(event.sender_id)
            if not tmp_data:
                await event.reply("❌ 세션 만료. 다시 시도하세요.")
                user_states.pop(event.sender_id, None)
                return
            tmp = tmp_data['client']
            try:
                await tmp.sign_in(tmp_data['phone'], code, phone_code_hash=tmp_data['phone_code_hash'])
                me = await tmp.get_me()
                sess_str = tmp.session.save()
                await tmp.disconnect()
                db = DB.load()
                name = (f"{me.first_name or ''} {me.last_name or ''}").strip() or tmp_data['phone']
                db['accounts'].append({**copy.deepcopy(DB.ACC_DEFAULTS), 'session_str': sess_str, 'phone': tmp_data['phone'], 'name': name})
                DB.save(db)
                new_idx = len(db['accounts']) - 1
                ensure_scheduler(new_idx)
                temp_clients.pop(event.sender_id, None)
                del user_states[event.sender_id]
                await event.reply(f"✅ 계정 등록 완료! (총 {len(db['accounts'])}개)\n이름: {_html.escape(name)}")
                await draw_accounts(event)
            except SessionPasswordNeededError:
                user_states[event.sender_id] = {'step': 'login_2fa'}
                await event.reply("🔐 2단계 인증 비밀번호를 입력하세요.")
            except PhoneCodeInvalidError:
                await event.reply("❌ 인증코드가 올바르지 않습니다. 다시 입력하세요.")
            except Exception as e:
                await tmp.disconnect()
                temp_clients.pop(event.sender_id, None)
                del user_states[event.sender_id]
                await event.reply(f"❌ 로그인 실패: {e}")
        elif step == 'login_2fa':
            password = event.raw_text.strip()
            tmp_data = temp_clients.get(event.sender_id)
            if not tmp_data:
                await event.reply("❌ 세션 만료. 다시 시도하세요.")
                user_states.pop(event.sender_id, None)
                return
            tmp = tmp_data['client']
            try:
                await tmp.sign_in(password=password)
                me = await tmp.get_me()
                sess_str = tmp.session.save()
                await tmp.disconnect()
                db = DB.load()
                name = (f"{me.first_name or ''} {me.last_name or ''}").strip() or tmp_data['phone']
                db['accounts'].append({**copy.deepcopy(DB.ACC_DEFAULTS), 'session_str': sess_str, 'phone': tmp_data['phone'], 'name': name})
                DB.save(db)
                new_idx = len(db['accounts']) - 1
                ensure_scheduler(new_idx)
                temp_clients.pop(event.sender_id, None)
                del user_states[event.sender_id]
                await event.reply(f"✅ 2FA 계정 등록 완료! (총 {len(db['accounts'])}개)")
                await draw_accounts(event)
            except Exception as e:
                await tmp.disconnect()
                temp_clients.pop(event.sender_id, None)
                del user_states[event.sender_id]
                await event.reply(f"❌ 2FA 실패: {e}")
    except Exception as e:
        logger.error(f"input_handler 오류 ({step}): {e}")
        user_states.pop(event.sender_id, None)
        try:
            await event.reply(f"❌ 처리 중 오류: {_html.escape(str(e))}")
        except Exception:
            pass

async def main():
    client.parse_mode = 'html'
    await client.start(bot_token=BOT_TOKEN)
    db = DB.load()
    db['bot_session_str'] = client.session.save()
    DB.save(db)
    logger.info("🐾 꽃미네 봇 V24 Final 가동")
    for i, acc in enumerate(db.get('accounts', [])):
        if acc.get('running'):
            ensure_scheduler(i)
            logger.info(f"계정{i+1} 스케줄러 복구 완료")
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("봇 수동 종료")
    except Exception as e:
        logger.error(f"치명적 오류: {e}")
