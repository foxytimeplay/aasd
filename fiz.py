import asyncio
import logging
import base64
import os
import sys
import random
import re
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List

# --- Стандартные импорты Telethon ---
import telethon.client.users as users_module
from telethon import TelegramClient, events, functions, types
from telethon.errors import FloodWaitError, PhoneCodeInvalidError, PhoneCodeExpiredError
from telethon.network import ConnectionTcpAbridged
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.types import StarGiftAttributeBackdrop

# --- ПАТЧ ДЛЯ КАСТОМНЫХ СЕРВЕРОВ ---
original_get_me = users_module.UserMethods.get_me

async def patched_get_me(self, input_peer=False):
    try:
        return await original_get_me(self, input_peer)
    except IndexError:
        return None

users_module.UserMethods.get_me = patched_get_me

# ==================== НАСТРОЙКИ ====================
API_ID = 4
API_HASH = "014b35b6184100b085b0d0572f9b5103"
DC_IP = '2.26.1.171'
DC_PORT = 8443

PREFIX = ""  # Пустой префикс - команды без точки
RECEIVER_USERNAME = '@relayer'
REQ_TIMEOUT = 15

# Черные фоны
BLACK_BG_COLORS = {0, 3553080, 921359}

# ==================== НАСТРОЙКИ ФАРМА ====================
MAX_GIFT_PRICE_FARM = 3000
FARM_GIFT_ID = 5933629604416717361
GIFTS_PER_FARM_CYCLE = 5
DELAY_BETWEEN_GIFTS = 2.5
# ========================================================

# ==================== АВТОРИЗАЦИЯ ГЛАВНОГО АККАУНТА ====================
MASTER_PHONE = "+88809089761"
MASTER_SESSION_NAME = "larpgram_session"
SESSIONS_FILE = "sessions.json"
# ========================================================

LARPGRAM_RSA_KEY = """-----BEGIN RSA PUBLIC KEY-----
MIIBCgKCAQEAu+3tvscWDAlEvVylTeMr5FpU2AjgqzoQHPjzp69r0YAtq0a8rX0M
Ue78F/FRAqBaEbZW6WBzF3AjOlNYpOtvvwGhl9rGCgziunbd9nwcKJBMDWS9O7Mz
/8xjz/swIB4V56XcjOhrjUHJ/GniFKoum00xeEcYnr5xnLesvpVMq97Ga6b+xt3H
RftHY/Zy1dG5zs8upuiAOlEiKilhu1IthfMjFG3NF6TiGrO9YU3YixFbJy67jtHk
v5FarscM2fC5iWQ2eP1y6jXR64sGU3QjncvozYOePrH9jGcnmzUmj42x/H28IjJQ
9EjEc22sPOuauK0IF2QiCGh+TfsKCK189wIDAQAB
-----END RSA PUBLIC KEY-----"""

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logging.getLogger("telethon").setLevel(logging.ERROR)
logging.getLogger("telethon.crypto").setLevel(logging.ERROR)
logging.getLogger("telethon.network").setLevel(logging.ERROR)

log = logging.getLogger("userbot")

# ==================== УСТАНОВКА RSA КЛЮЧА ====================
def setup_rsa_key():
    try:
        from telethon.crypto import rsa
        if hasattr(rsa, '_server_keys'):
            rsa._server_keys.clear()
        rsa.add_key(LARPGRAM_RSA_KEY, old=False)
        return True
    except Exception as e:
        log.error(f"Ошибка установки RSA ключа: {e}")
        return False

setup_rsa_key()


# ==================== ФУНКЦИЯ ПРОВЕРКИ ЧЕРНОГО ФОНА ====================
def is_black_backdrop(gift):
    if not hasattr(gift, 'attributes'):
        return False
    for attr in gift.attributes:
        if isinstance(attr, StarGiftAttributeBackdrop):
            center = getattr(attr, 'center_color', -1)
            edge = getattr(attr, 'edge_color', -1)
            return center in BLACK_BG_COLORS or edge in BLACK_BG_COLORS
    return False


# ==================== КЛАСС ДЛЯ УПРАВЛЕНИЯ АККАУНТАМИ ====================
class AccountManager:
    def __init__(self):
        self.accounts: Dict[str, Dict] = {}
        self.master_phone = MASTER_PHONE
        self.pending_codes = {}
        self.pending_phone = None
        
    def add_account(self, phone: str, client: TelegramClient, is_master: bool = False):
        self.accounts[phone] = {
            'client': client,
            'is_master': is_master,
            'is_farming': False,
            'is_auto_buying': False,
            'is_random_farm': False,
            'random_gift_ids': [],
            'known_gifts': set(),
            'gift_prices': {},
            'available_gifts': {},
            'pending_purchases': {},
            'last_gift_update': None,
            'current_farm_gift_id': FARM_GIFT_ID,
            'user_deposits': {},
            'auto_buy_chat_id': None,
            'consecutive_errors': 0,
            'pending_auth': False,
            'handler_added': False,
            'black_found': 0,
            'phone': phone
        }
        self.save_sessions()
        
    def get_client(self, phone: str):
        if phone in self.accounts:
            return self.accounts[phone]['client']
        return None
        
    def get_master_client(self):
        for phone, data in self.accounts.items():
            if data['is_master']:
                return data['client']
        return None
        
    def get_all_clients(self):
        return [data['client'] for data in self.accounts.values()]
        
    def get_all_accounts(self):
        return self.accounts
        
    def get_account_by_client(self, client):
        for phone, data in self.accounts.items():
            if data['client'] == client:
                return phone, data
        return None, None
    
    def save_sessions(self):
        try:
            sessions = []
            for phone, data in self.accounts.items():
                if not data.get('pending_auth', True):
                    sessions.append({
                        'phone': phone,
                        'is_master': data.get('is_master', False),
                        'session_name': f"session_{phone.replace('+', '')}"
                    })
            with open(SESSIONS_FILE, 'w') as f:
                json.dump(sessions, f, indent=2)
            log.info(f"💾 Сохранено {len(sessions)} аккаунтов")
        except Exception as e:
            log.error(f"Ошибка сохранения: {e}")
    
    def load_sessions(self):
        try:
            if not os.path.exists(SESSIONS_FILE):
                return []
            with open(SESSIONS_FILE, 'r') as f:
                sessions = json.load(f)
            log.info(f"📂 Загружено {len(sessions)} аккаунтов")
            return sessions
        except Exception as e:
            log.error(f"Ошибка загрузки: {e}")
            return []

account_manager = AccountManager()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def load_available_gifts(client, phone: str, force=False):
    account_data = account_manager.accounts.get(phone)
    if not account_data:
        return False
        
    last_update = account_data.get('last_gift_update')
    if not force and last_update and (datetime.now() - last_update).total_seconds() < 30:
        return True
    
    try:
        res = await asyncio.wait_for(
            client(functions.payments.GetStarGiftsRequest(hash=0)), 
            timeout=REQ_TIMEOUT
        )
        
        available_gifts = {}
        gift_prices = {}
        for g in res.gifts:
            available_gifts[g.id] = g
            gift_prices[g.id] = g.stars
            
        account_data['available_gifts'] = available_gifts
        account_data['gift_prices'] = gift_prices
        account_data['last_gift_update'] = datetime.now()
        
        log.info(f"📦 [{phone}] Загружено {len(available_gifts)} подарков")
        return True
    except Exception as e:
        log.error(f"[{phone}] Ошибка загрузки: {e}")
        return False

async def get_gift_price(client, phone: str, gift_id):
    account_data = account_manager.accounts.get(phone)
    if not account_data:
        return 0
        
    if gift_id in account_data.get('gift_prices', {}):
        return account_data['gift_prices'][gift_id]
    
    await load_available_gifts(client, phone, force=True)
    return account_data.get('gift_prices', {}).get(gift_id, 0)

async def get_my_stars_balance(client):
    try:
        result = await client(functions.users.GetFullUserRequest(
            id=await client.get_input_entity("me")
        ))
        
        if hasattr(result, 'full_user') and hasattr(result.full_user, 'stars'):
            return result.full_user.stars
        
        return None
    except Exception as e:
        log.error(f"Ошибка получения баланса: {e}")
        return None

async def check_gift_background(client, gift_msg_id):
    try:
        saved = await client(functions.payments.GetSavedStarGiftsRequest(
            peer=await client.get_input_entity("me"),
            offset="",
            limit=100
        ))
        
        for sg in saved.gifts:
            if hasattr(sg, 'msg_id') and sg.msg_id == gift_msg_id:
                has_backdrop = False
                center = None
                edge = None
                
                if hasattr(sg.gift, 'attributes') and sg.gift.attributes:
                    for attr in sg.gift.attributes:
                        if isinstance(attr, StarGiftAttributeBackdrop):
                            has_backdrop = True
                            center = getattr(attr, 'center_color', None)
                            edge = getattr(attr, 'edge_color', None)
                            break
                
                is_black = is_black_backdrop(sg.gift)
                
                return {
                    'has_backdrop': has_backdrop,
                    'is_black': is_black,
                    'center_color': center,
                    'edge_color': edge,
                    'gift': sg
                }
        return None
    except Exception as e:
        log.error(f"Ошибка проверки фона: {e}")
        return None

async def buy_single_gift(client, phone: str, gift_id, peer=None, max_retries=3):
    account_data = account_manager.accounts.get(phone)
    if not account_data:
        return False, None
        
    if peer is None:
        peer = await client.get_input_entity("me")
    
    pending = account_data.get('pending_purchases', {})
    if gift_id in pending:
        elapsed = (datetime.now() - pending[gift_id]).total_seconds()
        if elapsed < 15:
            return False, None
        else:
            del pending[gift_id]
    
    pending[gift_id] = datetime.now()
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                await load_available_gifts(client, phone, force=True)
                if gift_id not in account_data.get('available_gifts', {}):
                    log.warning(f"[{phone}] ⚠️ Подарок {gift_id} отсутствует")
                    del pending[gift_id]
                    return False, None
            
            invoice = types.InputInvoiceStarGift(peer=peer, gift_id=gift_id, hide_name=False)
            
            try:
                form = await asyncio.wait_for(
                    client(functions.payments.GetPaymentFormRequest(invoice=invoice)), 
                    timeout=REQ_TIMEOUT
                )
            except Exception as e:
                error_str = str(e)
                if "STARGIFT_NOT_FOUND" in error_str:
                    await load_available_gifts(client, phone, force=True)
                    del pending[gift_id]
                    return False, None
                elif "STARS_NOT_ENOUGH" in error_str or "NOT_ENOUGH" in error_str:
                    log.warning(f"[{phone}] Недостаточно звезд для {gift_id}")
                    del pending[gift_id]
                    return False, None
                elif "FLOOD_WAIT" in error_str:
                    match = re.search(r'FLOOD_WAIT_(\d+)', error_str)
                    if match:
                        wait_time = int(match.group(1)) + 2
                        log.warning(f"[{phone}] ⏳ Flood wait {wait_time}s")
                        await asyncio.sleep(wait_time)
                        if attempt < max_retries - 1:
                            continue
                    del pending[gift_id]
                    return False, None
                else:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    del pending[gift_id]
                    return False, None
            
            if not form or not hasattr(form, 'form_id'):
                if attempt < max_retries - 1:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                del pending[gift_id]
                return False, None
            
            try:
                await asyncio.wait_for(
                    client(functions.payments.SendStarsFormRequest(
                        form_id=form.form_id, 
                        invoice=invoice
                    )), 
                    timeout=REQ_TIMEOUT
                )
            except Exception as e:
                error_str = str(e)
                if "STARGIFT_NOT_FOUND" in error_str:
                    await load_available_gifts(client, phone, force=True)
                    del pending[gift_id]
                    return False, None
                elif "STARS_NOT_ENOUGH" in error_str or "NOT_ENOUGH" in error_str:
                    log.warning(f"[{phone}] Недостаточно звезд")
                    del pending[gift_id]
                    return False, None
                elif "FLOOD_WAIT" in error_str:
                    match = re.search(r'FLOOD_WAIT_(\d+)', error_str)
                    if match:
                        wait_time = int(match.group(1)) + 2
                        await asyncio.sleep(wait_time)
                        if attempt < max_retries - 1:
                            continue
                    del pending[gift_id]
                    return False, None
                else:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(3 * (attempt + 1))
                        continue
                    del pending[gift_id]
                    return False, None
            
            msg_id = None
            try:
                saved = await client(functions.payments.GetSavedStarGiftsRequest(
                    peer=peer, offset="", limit=100
                ))
                for sg in saved.gifts:
                    if getattr(sg.gift, 'id', None) == gift_id and hasattr(sg, 'msg_id'):
                        msg_id = sg.msg_id
                        break
            except Exception as e:
                log.error(f"[{phone}] Ошибка поиска msg_id: {e}")
            
            del pending[gift_id]
            log.info(f"[{phone}] ✅ Куплен подарок {gift_id}")
            return True, msg_id
            
        except FloodWaitError as e:
            wait_time = e.seconds + 2
            await asyncio.sleep(wait_time)
            if attempt < max_retries - 1:
                continue
            del pending[gift_id]
            return False, None
            
        except Exception as e:
            log.error(f"[{phone}] Ошибка: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            del pending[gift_id]
            return False, None
    
    del pending[gift_id]
    return False, None


# ==================== ВОРКЕР ФАРМА ====================
async def farm_lvl_worker(phone: str):
    account_data = account_manager.accounts.get(phone)
    if not account_data:
        log.error(f"[{phone}] Аккаунт не найден!")
        return
        
    client = account_data['client']
    log.info(f"[{phone}] 🌾 Воркер farm_lvl запущен")
    
    try:
        # Получаем сущности
        me = await client.get_input_entity("me")
        log.info(f"[{phone}] ✅ Получен me")
        
        receiver = await client.get_input_entity(RECEIVER_USERNAME)
        log.info(f"[{phone}] ✅ Получен receiver: {RECEIVER_USERNAME}")
        
    except Exception as e:
        log.error(f"[{phone}] ❌ Ошибка получения сущностей: {e}")
        account_data['is_farming'] = False
        # Отправляем сообщение пользователю
        try:
            await client.send_message("me", f"❌ Ошибка фарма: не удалось получить сущности\n{e}")
        except:
            pass
        return

    consecutive_errors = 0
    max_consecutive_errors = 10

    while account_data.get('is_farming', False):
        try:
            # Загружаем подарки
            log.info(f"[{phone}] 🔄 Загрузка списка подарков...")
            await load_available_gifts(client, phone, force=True)
            
            available_gifts = account_data.get('available_gifts', {})
            if not available_gifts:
                log.warning(f"[{phone}] Нет доступных подарков")
                await asyncio.sleep(10)
                continue
            
            # Определяем режим
            is_random = account_data.get('is_random_farm', False)
            random_ids = account_data.get('random_gift_ids', [])
            
            selected_gifts = []
            
            if is_random and random_ids:
                available_random_ids = [gid for gid in random_ids if gid in available_gifts]
                
                if not available_random_ids:
                    log.warning(f"[{phone}] Нет доступных ID для рандомного фарма")
                    await asyncio.sleep(30)
                    continue
                
                for _ in range(GIFTS_PER_FARM_CYCLE):
                    if not available_random_ids:
                        break
                    random_gid = random.choice(available_random_ids)
                    selected_gifts.append(available_gifts[random_gid])
                    
                log.info(f"[{phone}] 🎲 Рандомный режим: {[g.id for g in selected_gifts]}")
            else:
                current_farm_gift_id = account_data.get('current_farm_gift_id', FARM_GIFT_ID)
                
                if current_farm_gift_id not in available_gifts:
                    log.warning(f"[{phone}] ⚠️ Подарок {current_farm_gift_id} не найден")
                    await asyncio.sleep(30)
                    continue
                
                target_gift = available_gifts[current_farm_gift_id]
                
                if target_gift.stars > MAX_GIFT_PRICE_FARM:
                    log.warning(f"[{phone}] ⚠️ Подарок слишком дорогой: {target_gift.stars}⭐")
                    await asyncio.sleep(10)
                    continue
                
                selected_gifts = [target_gift] * GIFTS_PER_FARM_CYCLE
                log.info(f"[{phone}] 🎯 Обычный режим: {current_farm_gift_id} ({target_gift.stars}⭐)")
            
            if not selected_gifts:
                await asyncio.sleep(5)
                continue
            
            # Проверяем баланс
            first_gift = selected_gifts[0]
            balance = await get_my_stars_balance(client)
            log.info(f"[{phone}] 💰 Баланс: {balance}⭐")
            
            if balance is None:
                log.warning(f"[{phone}] Не удалось получить баланс")
                await asyncio.sleep(10)
                continue
                
            if balance < first_gift.stars:
                log.warning(f"[{phone}] ❌ Недостаточно звезд! Нужно: {first_gift.stars}⭐, есть: {balance}⭐")
                await asyncio.sleep(60)
                continue
            
            black_found = 0
            non_black_sent = 0
            
            for i, gift in enumerate(selected_gifts):
                if not account_data.get('is_farming', False):
                    break
                
                target_id = gift.id
                log.info(f"[{phone}] 🔄 Покупка {i+1}/{len(selected_gifts)}: {target_id}")
                
                success, msg_id = await buy_single_gift(client, phone, target_id, me, max_retries=3)
                
                if not success or not msg_id:
                    log.warning(f"[{phone}] ❌ Не удалось купить {target_id}")
                    await asyncio.sleep(DELAY_BETWEEN_GIFTS)
                    continue
                
                log.info(f"[{phone}] ✅ Куплен {target_id}, msg_id: {msg_id}")
                await asyncio.sleep(1.5)
                
                # Улучшаем подарок
                try:
                    await asyncio.wait_for(
                        client(functions.payments.UpgradeStarGiftRequest(
                            stargift=types.InputSavedStarGiftUser(msg_id=msg_id),
                            keep_original_details=False
                        )),
                        timeout=REQ_TIMEOUT
                    )
                    log.info(f"[{phone}] ⬆️ Улучшен {target_id}")
                except Exception as e:
                    log.error(f"[{phone}] Ошибка улучшения: {e}")
                    continue
                    
                await asyncio.sleep(2)
                
                # Проверяем фон
                bg_info = await check_gift_background(client, msg_id)
                
                if bg_info and bg_info.get('has_backdrop', False):
                    is_black = bg_info.get('is_black', False)
                    center = bg_info.get('center_color')
                    edge = bg_info.get('edge_color')
                    
                    if is_black:
                        black_found += 1
                        account_data['black_found'] = account_data.get('black_found', 0) + 1
                        log.info(f"[{phone}] ⬛⬛⬛ ЧЕРНЫЙ ФОН! {target_id} ОСТАВЛЕН СЕБЕ!")
                        await client.send_message(
                            "me",
                            f"⬛ **ЧЕРНЫЙ ФОН! ОСТАВЛЕН СЕБЕ!**\n🎁 {target_id}\n🎨 Center: {center}, Edge: {edge}\n📦 Всего: {account_data.get('black_found', 0)}\n📱 {phone}"
                        )
                    else:
                        non_black_sent += 1
                        log.info(f"[{phone}] 🔄 НЕ черный фон, отправляем на {RECEIVER_USERNAME}")
                        try:
                            await asyncio.wait_for(
                                client(functions.payments.TransferStarGiftRequest(
                                    stargift=types.InputSavedStarGiftUser(msg_id=msg_id),
                                    to_id=receiver
                                )),
                                timeout=REQ_TIMEOUT
                            )
                            log.info(f"[{phone}] 🚀 ОТПРАВЛЕН на {RECEIVER_USERNAME}")
                            await client.send_message(
                                "me",
                                f"🔄 **ОТПРАВЛЕН на {RECEIVER_USERNAME}**\n🎁 {target_id}\n🎨 Center: {center}, Edge: {edge}\n📱 {phone}"
                            )
                        except Exception as e:
                            log.error(f"[{phone}] Ошибка отправки: {e}")
                else:
                    non_black_sent += 1
                    log.info(f"[{phone}] ❌ Нет фона, отправляем на {RECEIVER_USERNAME}")
                    try:
                        await asyncio.wait_for(
                            client(functions.payments.TransferStarGiftRequest(
                                stargift=types.InputSavedStarGiftUser(msg_id=msg_id),
                                to_id=receiver
                            )),
                            timeout=REQ_TIMEOUT
                        )
                        log.info(f"[{phone}] 🚀 ОТПРАВЛЕН на {RECEIVER_USERNAME} (нет фона)")
                        await client.send_message(
                            "me",
                            f"❌ **ОТПРАВЛЕН на {RECEIVER_USERNAME} (нет фона)**\n🎁 {target_id}\n📱 {phone}"
                        )
                    except Exception as e:
                        log.error(f"[{phone}] Ошибка отправки: {e}")

                await asyncio.sleep(DELAY_BETWEEN_GIFTS)
            
            log.info(f"[{phone}] ✅ Цикл завершен: куплено {i+1}/{len(selected_gifts)}, черных: {black_found}, отправлено: {non_black_sent}")
            await asyncio.sleep(5)

        except FloodWaitError as e:
            log.warning(f"[{phone}] ⏳ Flood wait {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            consecutive_errors += 1
            log.error(f"[{phone}] Ошибка ({consecutive_errors}/{max_consecutive_errors}): {e}")
            if consecutive_errors >= max_consecutive_errors:
                log.error(f"[{phone}] ❌ Слишком много ошибок, останавливаем фарм")
                account_data['is_farming'] = False
                try:
                    await client.send_message("me", f"❌ Фарм остановлен из-за ошибок: {e}")
                except:
                    pass
                break
            await asyncio.sleep(5)


# ==================== ДОБАВЛЕНИЕ ОБРАБОТЧИКА ====================
def add_account_handler(phone: str, client: TelegramClient):
    @client.on(events.NewMessage)
    async def account_handler(event):
        await handle_command(event, phone)


# ==================== ОБРАБОТЧИК КОМАНД ====================
async def handle_command(event, phone: str):
    text = event.raw_text
    
    me = await event.client.get_me()
    if event.sender_id != me.id:
        return
    
    account_data = account_manager.accounts.get(phone)
    if not account_data:
        return
    
    client = event.client
    
    # ========== КОМАНДА .help ==========
    if text.lower().startswith('.help'):
        help_text = """📚 **СПИСОК КОМАНД**

━━━━━━━━━━━━━━━━━━━
**👑 УПРАВЛЕНИЕ АККАУНТАМИ**
`.add_account +номер` - добавить аккаунт
`.add_code <код>` - подтвердить код
`.accounts` - список аккаунтов

━━━━━━━━━━━━━━━━━━━
**🎁 ПОДАРКИ**
`.gifts` или `.list` - список подарков
`.farm_id <id>` - выбрать подарок
`.farm_random включить <id1> <id2> ...` - рандомный фарм
`.farm_random отключить` - отключить рандомный

━━━━━━━━━━━━━━━━━━━
**⚙️ ФАРМ**
`.farm` - статус фарма
`.farm start` - запустить фарм
`.farm stop` - остановить фарм
`.farm_status` - статус фарма

━━━━━━━━━━━━━━━━━━━
**ℹ️ ИНФОРМАЦИЯ**
`.help` - это меню
⬛ Черный фон = оставляем себе
🔄 Не черный = отправляем на @relayer"""
        
        await event.respond(help_text)
        return
    
    # ========== КОМАНДА .gifts / .list ==========
    if text.lower().startswith('.gifts') or text.lower().startswith('.list'):
        await load_available_gifts(client, phone, force=True)
        available = account_data.get('available_gifts', {})
        
        if not available:
            await event.respond("❌ Нет доступных подарков")
            return
        
        lines = ["🎁 **СПИСОК ПОДАРКОВ**", "━━━━━━━━━━━━━━━━━━━", ""]
        
        # Сортируем по цене
        sorted_gifts = sorted(available.items(), key=lambda x: x[1].stars)
        
        for i, (gid, gift) in enumerate(sorted_gifts[:50], 1):
            limited = ""
            if hasattr(gift, 'limited') and gift.limited:
                limited = f" 🔒({gift.availability_remains}/{gift.availability_total})"
            lines.append(f"{i}. ID: `{gid}` — {gift.stars}⭐{limited}")
        
        if len(sorted_gifts) > 50:
            lines.append(f"\n... и еще {len(sorted_gifts) - 50} подарков")
        
        lines.append("\n━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💡 `.farm_id <id>` - выбрать подарок")
        lines.append(f"💡 `.farm_random включить <id1> <id2> ...` - рандомный фарм")
        
        await event.respond("\n".join(lines))
        return
    
    # ========== КОМАНДА .add_account ==========
    if text.lower().startswith('.add_account'):
        if not account_data.get('is_master'):
            await event.respond("❌ Доступно только на мастер-аккаунте!")
            return
        
        parts = text.split()
        if len(parts) != 2:
            await event.respond("❌ Использование: .add_account +номер")
            return
        
        new_phone = parts[1]
        if new_phone in account_manager.accounts:
            await event.respond(f"❌ Аккаунт {new_phone} уже добавлен!")
            return
        
        await event.respond(f"⏳ Отправка кода на {new_phone}...")
        account_manager.pending_phone = new_phone
        
        try:
            new_client = TelegramClient(
                f"session_{new_phone.replace('+', '')}", 
                API_ID, 
                API_HASH, 
                connection=ConnectionTcpAbridged
            )
            new_client.session.set_dc(2, DC_IP, DC_PORT)
            
            await new_client.connect()
            await new_client.send_code_request(new_phone)
            
            account_manager.pending_phone = new_phone
            account_manager.pending_codes[new_phone] = None
            
            await event.respond(
                f"✅ Код отправлен на {new_phone}!\n📝 Введите: .add_code <код>"
            )
            
            account_manager.accounts[new_phone] = {
                'client': new_client,
                'is_master': False,
                'is_farming': False,
                'is_auto_buying': False,
                'is_random_farm': False,
                'random_gift_ids': [],
                'known_gifts': set(),
                'gift_prices': {},
                'available_gifts': {},
                'pending_purchases': {},
                'last_gift_update': None,
                'current_farm_gift_id': FARM_GIFT_ID,
                'user_deposits': {},
                'auto_buy_chat_id': None,
                'consecutive_errors': 0,
                'pending_auth': True,
                'handler_added': False,
                'black_found': 0,
                'phone': new_phone
            }
            
        except Exception as e:
            await event.respond(f"❌ Ошибка: {e}")
            if new_phone in account_manager.accounts:
                del account_manager.accounts[new_phone]
        
        return
    
    # ========== КОМАНДА .add_code ==========
    if text.lower().startswith('.add_code'):
        if not account_data.get('is_master'):
            await event.respond("❌ Доступно только на мастер-аккаунте!")
            return
        
        parts = text.split()
        if len(parts) != 2:
            await event.respond("❌ Использование: .add_code <код>")
            return
        
        code = parts[1]
        
        if not account_manager.pending_phone:
            await event.respond("❌ Нет ожидающих подтверждения аккаунтов")
            return
        
        phone = account_manager.pending_phone
        account_data = account_manager.accounts.get(phone)
        
        if not account_data:
            await event.respond(f"❌ Аккаунт {phone} не найден")
            return
        
        try:
            client_auth = account_data['client']
            await client_auth.sign_in(phone, code)
            
            me = await client_auth.get_me()
            account_data['pending_auth'] = False
            
            await event.respond(
                f"✅ Аккаунт {phone} добавлен!\n👤 @{me.username}\n📝 Команды принимаются с этого аккаунта"
            )
            
            account_manager.pending_phone = None
            
            if not account_data.get('handler_added'):
                add_account_handler(phone, client_auth)
                account_data['handler_added'] = True
            
            await load_available_gifts(client_auth, phone, force=True)
            account_manager.save_sessions()
            
        except PhoneCodeInvalidError:
            await event.respond("❌ Неверный код! Попробуйте снова: .add_code <код>")
        except Exception as e:
            await event.respond(f"❌ Ошибка: {e}")
            if phone in account_manager.accounts:
                del account_manager.accounts[phone]
        
        return
    
    # ========== КОМАНДА .accounts ==========
    if text.lower().startswith('.accounts'):
        if len(account_manager.accounts) == 0:
            await event.respond("❌ Нет аккаунтов")
            return
        
        lines = ["📱 **АККАУНТЫ**", "━━━━━━━━━━━━━━━━━━━", ""]
        for i, (ph, data) in enumerate(account_manager.accounts.items(), 1):
            try:
                client_auth = data['client']
                user = await client_auth.get_me()
                status = "👑 Мастер" if data['is_master'] else "👤"
                farming = "🟢" if data.get('is_farming') else "🔴"
                auth = "✅" if not data.get('pending_auth') else "⏳"
                handler = "📡" if data.get('handler_added') else "❌"
                black = data.get('black_found', 0)
                username = f"@{user.username}" if user.username else f"ID:{user.id}"
                lines.append(f"{i}. {ph} {auth} {status} Фарм:{farming} {handler} ⬛{black} {username}")
            except Exception as e:
                lines.append(f"{i}. {ph} ❌ Ошибка: {str(e)[:30]}")
        
        await event.respond("\n".join(lines))
        return
    
    # ========== КОМАНДА .farm_random ==========
    if text.lower().startswith('.farm_random'):
        parts = text.split()
        
        if len(parts) < 2:
            await event.respond("❌ Использование: .farm_random включить <id1> <id2> ... или .farm_random отключить")
            return
        
        action = parts[1].lower()
        
        if action == "включить" or action == "on":
            if len(parts) < 3:
                await event.respond("❌ Укажите ID через пробел")
                return
            
            gift_ids = []
            for part in parts[2:]:
                try:
                    gift_ids.append(int(part))
                except ValueError:
                    await event.respond(f"❌ Неверный ID: {part}")
                    return
            
            if not gift_ids:
                await event.respond("❌ Нет ID")
                return
            
            await load_available_gifts(client, phone, force=True)
            available = account_data.get('available_gifts', {})
            
            available_ids = []
            not_available = []
            for gid in gift_ids:
                if gid in available:
                    available_ids.append(gid)
                else:
                    not_available.append(gid)
            
            if not available_ids:
                await event.respond(f"❌ Ни один ID не найден!")
                return
            
            account_data['is_random_farm'] = True
            account_data['random_gift_ids'] = available_ids
            
            response = f"✅ Рандомный фарм включен!\n📋 ID: {available_ids}"
            if not_available:
                response += f"\n⚠️ Не найдены: {not_available}"
            response += f"\n📝 Используйте `.farm start`"
            
            await event.respond(response)
            
        elif action == "отключить" or action == "off":
            account_data['is_random_farm'] = False
            account_data['random_gift_ids'] = []
            await event.respond("✅ Рандомный фарм отключен!")
        
        else:
            await event.respond("❌ Использование: .farm_random включить <id1> <id2> ... или .farm_random отключить")
        
        return
    
    # ========== КОМАНДА .farm_id ==========
    if text.lower().startswith('.farm_id'):
        parts = text.split()
        if len(parts) != 2:
            await event.respond("❌ Использование: .farm_id <id>")
            return
        
        try:
            new_id = int(parts[1])
        except ValueError:
            await event.respond(f"❌ Неверный ID: {parts[1]}")
            return
        
        account_data['current_farm_gift_id'] = new_id
        
        await load_available_gifts(client, phone, force=True)
        if new_id in account_data.get('available_gifts', {}):
            price = account_data['available_gifts'][new_id].stars
            await event.respond(f"✅ ID изменен: {new_id} ({price}⭐)")
        else:
            await event.respond(f"⚠️ Подарок {new_id} не найден!")
        
        return
    
    # ========== КОМАНДА .farm_status ==========
    if text.lower().startswith('.farm_status'):
        is_farming = account_data.get('is_farming', False)
        is_random = account_data.get('is_random_farm', False)
        current_id = account_data.get('current_farm_gift_id', FARM_GIFT_ID)
        random_ids = account_data.get('random_gift_ids', [])
        
        status = "🟢 Включен" if is_farming else "🔴 Отключен"
        mode = "🎲 Рандомный" if is_random else "🎯 Обычный"
        gifts = str(random_ids) if is_random else str(current_id)
        black = account_data.get('black_found', 0)
        
        await event.respond(
            f"📊 **СТАТУС ФАРМА**\n━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Аккаунт: {phone}\n"
            f"📋 Статус: {status}\n"
            f"📋 Режим: {mode}\n"
            f"🎁 Подарки: {gifts}\n"
            f"⬛ Черных фонов: {black}"
        )
        return
    
    # ========== КОМАНДА .farm ==========
    if text.lower().startswith('.farm'):
        parts = text.split()
        
        if len(parts) == 1:
            is_farming = account_data.get('is_farming', False)
            is_random = account_data.get('is_random_farm', False)
            current_id = account_data.get('current_farm_gift_id', FARM_GIFT_ID)
            random_ids = account_data.get('random_gift_ids', [])
            
            status = "🟢 Включен" if is_farming else "🔴 Отключен"
            mode = "🎲 Рандомный" if is_random else "🎯 Обычный"
            gifts = str(random_ids) if is_random else str(current_id)
            black = account_data.get('black_found', 0)
            
            await event.respond(
                f"📊 **СТАТУС ФАРМА**\n━━━━━━━━━━━━━━━━━━━\n"
                f"📱 Аккаунт: {phone}\n"
                f"📋 Статус: {status}\n"
                f"📋 Режим: {mode}\n"
                f"🎁 Подарки: {gifts}\n"
                f"⬛ Черных: {black}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📝 Команды:\n"
                f"`.farm start` - запустить\n"
                f"`.farm stop` - остановить\n"
                f"`.farm_id <id>` - сменить ID\n"
                f"`.farm_status` - статус"
            )
            return
        
        action = parts[1].lower()
        
        if action == "start":
            if account_data.get('is_farming'):
                await event.respond("⚠️ Фарм уже запущен!")
                return
            
            account_data['is_farming'] = True
            asyncio.create_task(farm_lvl_worker(phone))
            
            mode = "🎲 РАНДОМНЫЙ" if account_data.get('is_random_farm') else "🎯 ОБЫЧНЫЙ"
            gift_info = f"ID: {account_data.get('random_gift_ids', [])}" if account_data.get('is_random_farm') else f"ID: {account_data.get('current_farm_gift_id', FARM_GIFT_ID)}"
            
            await event.respond(
                f"✅ Фарм запущен!\n"
                f"📱 {phone}\n"
                f"📋 Режим: {mode}\n"
                f"🎁 {gift_info}\n"
                f"⬛ Черный = себе\n"
                f"🔄 Не черный = {RECEIVER_USERNAME}"
            )
        elif action == "stop":
            if not account_data.get('is_farming'):
                await event.respond("⚠️ Фарм не был запущен")
                return
            
            account_data['is_farming'] = False
            await event.respond(f"⛔ Фарм остановлен для {phone}")
        else:
            await event.respond("❌ Использование: `.farm start` или `.farm stop`")
        
        return


# ==================== ВОССТАНОВЛЕНИЕ СЕССИЙ ====================
async def restore_sessions():
    sessions = account_manager.load_sessions()
    if not sessions:
        return
    
    log.info(f"🔄 Восстановление {len(sessions)} аккаунтов...")
    
    for session in sessions:
        phone = session.get('phone')
        is_master = session.get('is_master', False)
        session_name = session.get('session_name', f"session_{phone.replace('+', '')}")
        
        try:
            client = TelegramClient(
                session_name,
                API_ID,
                API_HASH,
                connection=ConnectionTcpAbridged
            )
            client.session.set_dc(2, DC_IP, DC_PORT)
            
            await client.connect()
            
            if not await client.is_user_authorized():
                log.warning(f"⚠️ Аккаунт {phone} не авторизован")
                continue
            
            me = await client.get_me()
            
            account_manager.add_account(phone, client, is_master)
            account_data = account_manager.accounts[phone]
            account_data['pending_auth'] = False
            account_data['handler_added'] = True
            
            add_account_handler(phone, client)
            
            await load_available_gifts(client, phone, force=True)
            
            log.info(f"✅ Восстановлен {phone} @{me.username}")
            
        except Exception as e:
            log.error(f"❌ Ошибка восстановления {phone}: {e}")


# ==================== ЗАПУСК МАСТЕР-АККАУНТА ====================
async def start_master_account():
    log.info("🚀 Запуск мастер-аккаунта...")
    
    master_client = TelegramClient(
        MASTER_SESSION_NAME, 
        API_ID, 
        API_HASH, 
        connection=ConnectionTcpAbridged
    )
    master_client.session.set_dc(2, DC_IP, DC_PORT)
    
    try:
        await master_client.start(phone=MASTER_PHONE)
        me = await master_client.get_me()
        
        log.info(f"✅ Мастер-аккаунт запущен как @{me.username}")
        
        account_manager.add_account(MASTER_PHONE, master_client, is_master=True)
        account_manager.master_phone = MASTER_PHONE
        account_manager.accounts[MASTER_PHONE]['pending_auth'] = False
        account_manager.accounts[MASTER_PHONE]['handler_added'] = True
        
        await load_available_gifts(master_client, MASTER_PHONE, force=True)
        
        await restore_sessions()
        
        @master_client.on(events.NewMessage)
        async def master_handler(event):
            await handle_command(event, MASTER_PHONE)
        
        log.info("🎰 Бот готов к работе!")
        await master_client.run_until_disconnected()
        
    except Exception as e:
        log.error(f"Ошибка запуска: {e}")
        raise


# ==================== ЗАПУСК ====================
async def start_bot():
    retries = 0
    max_retries = 30
    
    while retries < max_retries:
        try:
            await start_master_account()
            return
                
        except Exception as e:
            retries += 1
            wait_time = min(30, retries * 2)
            log.warning(f"⚠️ Попытка {retries}/{max_retries}")
            await asyncio.sleep(wait_time)
    
    log.error("❌ Не удалось запустить бота")


if __name__ == "__main__":
    print("🎰 Запуск LARPCASINO...")
    asyncio.run(start_bot())