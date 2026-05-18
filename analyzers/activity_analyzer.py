from analyzers.base_analyzer import BaseAnalyzer
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import re
import logging

logger = logging.getLogger(__name__)
MSK_TZ = timezone(timedelta(hours=3))

def _get_settings(key, default):
    """Получает настройку из БД. Ищет ключ с префиксом 'setting_'"""
    try:
        from app.database import SessionLocal
        from app.models import AdminSettings
        db = SessionLocal()
        setting = db.query(AdminSettings).filter(AdminSettings.key == f"setting_{key}").first()
        db.close()
        if setting:
            val = setting.value
            if isinstance(val, (int, float)): return val
            return float(val)
    except Exception:
        pass
    return default

def _format_msk_time(timestamp):
    if not timestamp: return ''
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.astimezone(MSK_TZ).strftime('%Y-%m-%d %H:%M:%S')

def _format_duration(seconds):
    if seconds < 60: return f"{int(seconds)} сек"
    elif seconds < 3600: return f"{int(seconds // 60)} мин"
    else: return f"{int(seconds // 3600)} ч"

class ActivityAnalyzer(BaseAnalyzer):
    def __init__(self, vk_client):
        super().__init__(vk_client)
        # Пороги детектирования (из БД)
        self.SIMILARITY_THRESHOLD = _get_settings('similarity_threshold', 0.85)
        self.COUNT_REPETITIVE = int(_get_settings('count_repetitive', 3))
        self.RAPID_COMMENT_WINDOW_MIN = int(_get_settings('rapid_comment_window_min', 3))
        self.COMMENTS_PER_TIME_WINDOW = int(_get_settings('comments_per_time_window', 5))
        self.REGULAR_INTERVAL_TOLERANCE_SEC = int(_get_settings('regular_interval_tolerance_sec', 10))
        self.MIN_INTERVAL_FOR_REGULAR_CHECK = int(_get_settings('min_interval_for_regular_check', 30))
        self.PERCENT_LIKED = int(_get_settings('percent_liked', 80))
        self.CROSS_USER_MIN_GROUP_SIZE = int(_get_settings('cross_user_min_group_size', 3))
        self.NEW_ACC_ACTIVITY = int(_get_settings('new_acc_activity', 15))
        self.NEW_ACC_ID_THRESHOLD = int(_get_settings('new_acc_id_threshold', 850_000_000))
        self.GENERIC_PHRASES = ['класс', 'круто', 'лайк', '+', 'спс', 'хорошо', 'годнота', '👍', '🔥']

    def analyze(self, posts_with_engagement: list, owner_id: str = None):
        score = 0
        reasons = []
        findings = []
        if not posts_with_engagement: return 0, [], findings

        total_comments = 0
        bot_pattern_count = 0
        user_data = defaultdict(lambda: {'comments': [], 'likes': set(), 'times': []})
        user_ids = set()

        for post in posts_with_engagement:
            post_id = post.get('id')
            comments = post.get('comments', [])
            total_comments += len(comments)
            liked_users = post.get('likes', {}).get('users', [])
            for c in comments:
                uid = c.get('from_id')
                text = c.get('text', '').strip()
                date = c.get('date')
                comment_id = c.get('id')
                if uid and uid > 0 and text:
                    user_ids.add(uid)
                    user_data[uid]['comments'].append({'text': text, 'date': date, 'post_id': post_id, 'comment_id': comment_id})
                    if date: user_data[uid]['times'].append(date)
                    if self._is_generic_comment(text): bot_pattern_count += 1
            for uid in liked_users:
                if uid > 0:
                    user_ids.add(uid)
                    user_data[uid]['likes'].add(post_id)

        user_names = self._fetch_user_names(list(user_ids))
        total_posts_count = len(posts_with_engagement)

        # Глобальный спам
        if total_comments >= 3:
            bot_ratio = bot_pattern_count / total_comments if total_comments > 0 else 0
            if bot_ratio > 0.7:
                penalty = int(_get_settings('penalty_global_spam', 20))
                score += penalty
                reasons.append(f"Аномально много шаблонных комментариев ({bot_ratio*100:.0f}%)")
            elif bot_ratio > 0.4:
                penalty = int(_get_settings('penalty_generic', 10))
                score += penalty
                reasons.append(f"Повышенное количество шаблонных комментариев ({bot_ratio*100:.0f}%)")

        for uid, data in user_data.items():
            comments = data['comments']
            times = data['times']
            likes = list(data['likes'])
            if len(comments) == 0 and len(likes) == 0: continue
            
            user_findings = []

            # 1. Массовые лайки
            if total_posts_count > 0 and len(likes) > 0:
                like_percentage = (len(likes) / total_posts_count) * 100
                if like_percentage >= self.PERCENT_LIKED:
                    penalty = int(_get_settings('penalty_mass_likes', 10))
                    score += penalty
                    reasons.append(f"Пользователь id{uid}: массовые лайки ({len(likes)}/{total_posts_count} постов)")
                    user_findings.append({'type': 'mass_likes', 'severity': 'MEDIUM', 'summary': f"Лайкнул {len(likes)}/{total_posts_count} записей ({like_percentage:.0f}%)", 'examples': []})

            if len(comments) < 2:
                if user_findings:
                    user_name = user_names.get(uid, f"id{uid}")
                    findings.append({'user_id': uid, 'user_name': user_name, 'patterns': user_findings})
                continue

            # 2. Повторяющиеся
            if len(comments) >= self.COUNT_REPETITIVE:
                clusters = self._cluster_similar_comments(comments, threshold=self.SIMILARITY_THRESHOLD)
                repetitive_groups = [cl for cl in clusters if len(cl['comments']) >= self.COUNT_REPETITIVE]
                if repetitive_groups:
                    repetitive_groups.sort(key=lambda x: len(x['comments']), reverse=True)
                    penalty = int(_get_settings('penalty_repetitive', 12))
                    score += penalty
                    reasons.append(f"Пользователь id{uid}: повторяющиеся комментарии")
                    examples = []
                    for group in repetitive_groups:
                        short_text = group['rep_text'][:100].replace('\n', ' ')
                        instances = [{'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id), 'time': _format_msk_time(c['date']), 'text': c['text'][:200]} for c in group['comments']]
                        examples.append({'pattern': short_text, 'count': len(group['comments']), 'instances': instances})
                    user_findings.append({'type': 'repetitive_comments', 'severity': 'HIGH', 'summary': f"Найдено {len(repetitive_groups)} групп повторяющихся текстов", 'examples': examples})

            # 3. Шаблонные
            generic_count = sum(1 for c in comments if self._is_generic_comment(c['text']))
            if generic_count >= len(comments) * 0.6:
                penalty = int(_get_settings('penalty_generic', 8))
                score += penalty
                reasons.append(f"Пользователь id{uid}: {generic_count}/{len(comments)} шаблонных комментариев")
                examples_list = [{'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id), 'time': _format_msk_time(c['date']), 'text': c['text'][:200]} for c in comments if self._is_generic_comment(c['text'])]
                user_findings.append({'type': 'generic_comments', 'severity': 'LOW', 'summary': f"{generic_count}/{len(comments)} комментариев соответствуют шаблонным паттернам", 'examples': [{'instances': examples_list}]})

            # 4. Быстрая серия
            if len(times) >= 3:
                sorted_comments = sorted(comments, key=lambda x: x['date'] or 0)
                sorted_times = [c['date'] for c in sorted_comments if c['date']]
                if len(sorted_times) >= 3:
                    rapid_series_list = []
                    i = 0
                    while i < len(sorted_times) - 2:
                        window_start = sorted_times[i]
                        window_end_limit = window_start + self.RAPID_COMMENT_WINDOW_MIN * 60
                        window_end_idx = i
                        for j in range(i, len(sorted_times)):
                            if sorted_times[j] <= window_end_limit: window_end_idx = j
                            else: break
                        series_length = window_end_idx - i + 1
                        if series_length >= self.COMMENTS_PER_TIME_WINDOW:
                            instances = [{'link': self._build_comment_link(sorted_comments[idx]['post_id'], sorted_comments[idx]['comment_id'], owner_id), 'time': _format_msk_time(sorted_comments[idx]['date']), 'text': sorted_comments[idx]['text'][:200]} for idx in range(i, window_end_idx + 1)]
                            rapid_series_list.append({'type': 'rapid_comments', 'severity': 'HIGH', 'summary': f"{series_length} комментариев за {_format_duration(sorted_times[window_end_idx] - sorted_times[i])}", 'examples': [{'instances': instances}]})
                            i = window_end_idx + 1
                        else: i += 1
                    if rapid_series_list:
                        penalty = int(_get_settings('penalty_rapid', 15))
                        score += penalty * len(rapid_series_list)
                        reasons.append(f"Пользователь id{uid}: {len(rapid_series_list)} серий быстрых комментариев")
                        user_findings.extend(rapid_series_list)

            # 5. Строгий интервал
            if len(comments) >= 3:
                sorted_c = sorted(comments, key=lambda x: x['date'] or 0)
                times_list = [c['date'] for c in sorted_c if c['date']]
                if len(times_list) >= 3:
                    for i in range(len(times_list) - 2):
                        window = times_list[i : i+3]
                        w_intervals = [window[j+1] - window[j] for j in range(2)]
                        if all(iv > 0 for iv in w_intervals):
                            avg_w = sum(w_intervals) / 2
                            max_dev = max(abs(iv - avg_w) for iv in w_intervals)
                            if avg_w >= self.MIN_INTERVAL_FOR_REGULAR_CHECK and max_dev <= self.REGULAR_INTERVAL_TOLERANCE_SEC:
                                penalty = int(_get_settings('penalty_regular', 10))
                                score += penalty
                                reasons.append(f"Пользователь id{uid}: серия из 3 комментариев с интервалом ~{_format_duration(avg_w)}")
                                user_findings.append({'type': 'regular_interval', 'severity': 'MEDIUM', 'summary': f"Серия из 3 комментариев с интервалом ~{_format_duration(avg_w)}"})
                                break

            # 6. Ночная активность
            if len(comments) >= 3:
                times_list = [c['date'] for c in comments if c['date']]
                if times_list:
                    night_count = sum(1 for ts in times_list if 3 <= datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(MSK_TZ).hour <= 5)
                    if night_count / len(times_list) >= 0.5:
                        penalty = int(_get_settings('penalty_night', 8))
                        score += penalty
                        reasons.append(f"Пользователь id{uid}: {night_count}/{len(times_list)} комментариев ночью (03-05)")
                        user_findings.append({'type': 'night_activity', 'severity': 'MEDIUM', 'summary': f"{night_count}/{len(times_list)} комментариев ночью"})

            # 7. Новый аккаунт с высокой активностью
            if uid > self.NEW_ACC_ID_THRESHOLD:
                total_activity = len(comments) + len(likes)
                if total_activity >= self.NEW_ACC_ACTIVITY:
                    penalty = int(_get_settings('penalty_new_acc', 10))
                    score += penalty
                    reasons.append(f"Пользователь id{uid}: высокая активность нового аккаунта")
                    user_findings.append({
                        'type': 'new_account_activity',
                        'severity': 'MEDIUM',
                        'summary': f"Новый аккаунт с большой активностью: {len(comments)} комментариев, {len(likes)} лайков",
                        'examples': [{'instances': [{'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id), 'time': _format_msk_time(c['date']), 'text': c['text'][:200]} for c in comments]}]
                    })

            if user_findings:
                user_name = user_names.get(uid, f"id{uid}")
                findings.append({'user_id': uid, 'user_name': user_name, 'patterns': user_findings})

        # 8. Межпользовательские совпадения
        network_findings = self._detect_coordinated_comments(user_data, owner_id)
        if network_findings:
            penalty = int(_get_settings('penalty_coordination', 15))
            score += penalty
            reasons.append(f"Обнаружено {len(network_findings)} групп скоординированных комментариев")
            findings.append({'type': 'network_coordination', 'groups': network_findings})

        return min(score, 100), reasons, findings

    def _is_generic_comment(self, text):
        text_lower = text.lower().strip()
        if len(text_lower) <= 5: return True
        if re.match(r'^[\W_]+$', text_lower): return True
        return text_lower in self.GENERIC_PHRASES

    def _fetch_user_names(self, user_ids):
        if not user_ids or not self.vk: return {}
        valid_uids = [uid for uid in user_ids if uid > 0]
        if not valid_uids: return {}
        names = {}
        for i in range(0, len(valid_uids), 100):
            batch = valid_uids[i:i+100]
            try:
                data, status = self.vk.request('users.get', {'user_ids': ','.join(map(str, batch))})
                if status == 'ok' and data and 'response' in data:
                    for user in data['response']:
                        uid = user.get('id')
                        fn = user.get('first_name', '').strip()
                        ln = user.get('last_name', '').strip()
                        names[uid] = f"{fn} {ln} (id{uid})" if fn or ln else f"id{uid}"
            except Exception: pass
        return names

    def _cluster_similar_comments(self, comments, threshold=0.85):
        clusters = []
        for c in comments:
            txt = c['text'].lower().strip()
            added = False
            for cl in clusters:
                if self._text_similarity(txt, cl['rep_text'].lower()) >= threshold:
                    cl['comments'].append(c)
                    added = True
                    break
            if not added: clusters.append({'rep_text': txt, 'comments': [c]})
        return clusters

    def _text_similarity(self, a, b):
        if a == b: return 1.0
        if not a or not b: return 0.0
        wa = set(re.findall(r'\w+', a))
        wb = set(re.findall(r'\w+', b))
        if not wa or not wb: return 0.0
        return len(wa & wb) / len(wa | wb)

    def _build_comment_link(self, post_id, comment_id, owner_id=None):
        if not post_id: return "https://vk.com/feed"
        wall_id = owner_id or (str(post_id).split('_')[0] if '_' in str(post_id) else post_id)
        return f"https://vk.com/wall{wall_id}_{post_id}?reply={comment_id}"

    def _detect_coordinated_comments(self, user_data, owner_id=None):
        text_groups = defaultdict(list)
        for uid, data in user_data.items():
            for c in data['comments']:
                norm = re.sub(r'[^\w\s]', '', c['text'].lower().strip())
                norm = re.sub(r'\s+', ' ', norm).strip()
                if len(norm) > 3: text_groups[norm].append({'user_id': uid, 'text': c['text'], 'date': c['date'], 'post_id': c['post_id'], 'comment_id': c['comment_id']})
        
        coordinated = []
        for norm, group in text_groups.items():
            uids = set(item['user_id'] for item in group)
            if len(uids) >= self.CROSS_USER_MIN_GROUP_SIZE:
                examples = [{'user_id': item['user_id'], 'link': self._build_comment_link(item['post_id'], item['comment_id'], owner_id), 'time': _format_msk_time(item['date']), 'text': item['text'][:150]} for item in group]
                coordinated.append({'users': sorted(list(uids)), 'pattern': norm[:100], 'count': len(group), 'examples': examples})
        return sorted(coordinated, key=lambda x: len(x['users']), reverse=True)