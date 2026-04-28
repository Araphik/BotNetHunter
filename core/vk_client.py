import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from config.settings import VK_API_URL, VK_API_VERSION, REQUEST_DELAY, FLOOD_WAIT

# Пул потоков для параллельных запросов
executor = ThreadPoolExecutor(max_workers=5)


class VKClient:
    def __init__(self, token_manager):
        self.token_manager = token_manager
        self.last_request_time = 0

    def _rate_limit(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self.last_request_time = time.time()

    def _make_request(self, method, params, token):
        url = f'{VK_API_URL}/{method}'
        params = {**params, 'access_token': token, 'v': VK_API_VERSION}
        try:
            response = requests.post(url, data=params, timeout=30)
            return response.json()
        except Exception as e:
            return None

    def request(self, method, params, max_retries=None):
        # ✅ ИСПРАВЛЕНО: используем метод вместо прямого доступа к .tokens
        if max_retries is None:
            max_retries = max(self.token_manager.get_tokens_count(), 1)

        for attempt in range(max_retries):
            token = self.token_manager.get_current_token()
            if not token:
                return None, 'no_token'

            self._rate_limit()
            data = self._make_request(method, params, token)

            if data is None:
                time.sleep(2)
                if self.token_manager.switch_token():
                    continue
                return None, 'network_error'

            if 'error' in data:
                code = data['error'].get('error_code')
                if code in (5, 6, 9, 1116):
                    if self.token_manager.switch_token():
                        time.sleep(FLOOD_WAIT)
                        continue
                    else:
                        return data, f'error_{code}'
                return data, f'error_{code}'

            return data, 'ok'

        return None, 'exhausted'

    def get_user(self, user_ids, fields):
        return self.request('users.get', {'user_ids': user_ids, 'fields': fields})

    def get_friends(self, user_id, count=1000):
        return self.request('friends.get', {'user_id': user_id, 'count': count})

    def get_wall(self, owner_id, count=50):
        return self.request('wall.get', {'owner_id': owner_id, 'count': count, 'filter': 'owner'})

    def get_group_members(self, group_id, count=1000, offset=0):
        return self.request('groups.getMembers', {
            'group_id': group_id,
            'count': count,
            'offset': offset,
            'fields': 'city,sex,bdate,photo_200'
        })

    def get_users_batch(self, user_ids_list, fields):
        from models.user_profile import UserProfile
        
        results = []
        # Разбиваем на пачки по 50 (лимит VK API)
        batches = [user_ids_list[i:i+50] for i in range(0, len(user_ids_list), 50)]
        
        for batch in batches:
            data, status = self.get_user(','.join(map(str, batch)), fields)
            if status == 'ok' and data and 'response' in data:
                for raw_profile in data['response']:
                    try:
                        results.append(UserProfile(raw_profile))
                    except Exception as e:
                        from utils.logger import logger
                        logger.warning(f"Ошибка создания UserProfile: {e}")
            time.sleep(REQUEST_DELAY)  # Пауза между пачками
        
        return results