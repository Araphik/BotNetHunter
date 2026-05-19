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
        if not posts_with_engagement: return 0, [], []

        total_penalty = 0
        reasons = []
        findings = []
        total_comments, bot_pattern_count, user_data, user_ids = self._collect_activity(posts_with_engagement)

        user_names = self._fetch_user_names(list(user_ids))
        total_posts_count = len(posts_with_engagement)
        penalty, reason = self._get_global_generic_comment_penalty(total_comments, bot_pattern_count)

        if reason:
            total_penalty += penalty
            reasons.append(reason)

        for uid, data in user_data.items():
            user_penalty, user_reasons, user_findings = self._analyze_user_activity(uid, data, total_posts_count, owner_id)
            total_penalty += user_penalty
            reasons.extend(user_reasons)

            if user_findings:
                user_name = user_names.get(uid, f"id{uid}")
                findings.append({'user_id': uid, 'user_name': user_name, 'patterns': user_findings})

        network_findings = self._detect_coordinated_comments(user_data, owner_id)
        if network_findings:
            penalty = int(_get_settings('penalty_coordination', 15))
            total_penalty += penalty
            
            reasons.append(f"Обнаружено {len(network_findings)} групп скоординированных комментариев")
            findings.append({'type': 'network_coordination', 'groups': network_findings})

        risk_coefficient = int(_get_settings('risk_coefficient', 5))
        if total_comments > 0:
            calculated_score = (total_penalty * risk_coefficient) / total_comments * 1000
        else:
            calculated_score = 0
        final_score = min(calculated_score, 100)
        
        return final_score, reasons, findings

    def _collect_activity(self, posts_with_engagement):
        total_comments = 0
        bot_pattern_count = 0
        user_data = defaultdict(lambda: {'comments': [], 'likes': set()})
        user_ids = set()

        for post in posts_with_engagement:
            post_comments, post_bot_count, post_user_ids = self._collect_post_activity(post, user_data)
            total_comments += post_comments
            bot_pattern_count += post_bot_count
            user_ids.update(post_user_ids)

        return total_comments, bot_pattern_count, user_data, user_ids

    def _collect_post_activity(self, post, user_data):
        post_id = post.get('id')
        comments = post.get('comments', [])
        user_ids = set()
        bot_pattern_count = 0

        for comment in comments:
            uid = self._store_comment_activity(comment, post_id, user_data)
            if uid:
                user_ids.add(uid)
                if self._is_generic_comment(comment.get('text', '').strip()):
                    bot_pattern_count += 1

        for uid in post.get('likes', {}).get('users', []):
            if uid > 0:
                user_ids.add(uid)
                user_data[uid]['likes'].add(post_id)

        return len(comments), bot_pattern_count, user_ids

    def _store_comment_activity(self, comment, post_id, user_data):
        uid = comment.get('from_id')
        text = comment.get('text', '').strip()
        if not uid or uid <= 0 or not text:
            return None

        user_data[uid]['comments'].append({
            'text': text,
            'date': comment.get('date'),
            'post_id': post_id,
            'comment_id': comment.get('id'),
        })
        return uid

    def _get_global_generic_comment_penalty(self, total_comments, bot_pattern_count):
        if total_comments < 3:
            return 0, None

        bot_ratio = bot_pattern_count / total_comments
        if bot_ratio > 0.7:
            return (
                int(_get_settings('penalty_global_spam', 20)),
                f"Аномально много шаблонных комментариев ({bot_ratio*100:.0f}%)",
            )
        if bot_ratio > 0.4:
            return (
                int(_get_settings('penalty_generic', 10)),
                f"Повышенное количество шаблонных комментариев ({bot_ratio*100:.0f}%)",
            )
        return 0, None

    def _analyze_user_activity(self, uid, data, total_posts_count, owner_id):
        comments = data['comments']
        likes = list(data['likes'])
        result = {'penalty': 0, 'reasons': [], 'findings': []}

        if not comments and not likes:
            return 0, [], []

        self._add_mass_likes_finding(result, uid, likes, total_posts_count)
        if len(comments) >= 2:
            self._add_repetitive_comment_findings(result, uid, comments, owner_id)
            self._add_generic_comment_findings(result, uid, comments, owner_id)
            self._add_rapid_comment_findings(result, uid, comments, owner_id)
            self._add_regular_interval_finding(result, uid, comments, owner_id)
            self._add_night_activity_finding(result, uid, comments, owner_id)
            self._add_new_account_finding(result, uid, comments, likes, owner_id)

        return result['penalty'], result['reasons'], result['findings']

    def _add_penalty(self, result, penalty, reason):
        result['penalty'] += penalty
        result['reasons'].append(reason)

    def _comment_instance(self, comment, owner_id):
        return {
            'link': self._build_comment_link(comment['post_id'], comment['comment_id'], owner_id),
            'time': _format_msk_time(comment['date']),
            'text': comment['text'][:200],
        }

    def _add_mass_likes_finding(self, result, uid, likes, total_posts_count):
        if total_posts_count <= 0 or not likes:
            return

        like_percentage = (len(likes) / total_posts_count) * 100
        if like_percentage < self.PERCENT_LIKED:
            return

        penalty = int(_get_settings('penalty_mass_likes', 10))
        self._add_penalty(result, penalty, f"Пользователь id{uid}: массовые лайки ({len(likes)}/{total_posts_count} постов)")
        result['findings'].append({
            'type': 'mass_likes',
            'severity': 'MEDIUM',
            'summary': f"Лайкнул {len(likes)}/{total_posts_count} записей ({like_percentage:.0f}%)",
            'examples': [],
        })

    def _add_repetitive_comment_findings(self, result, uid, comments, owner_id):
        if len(comments) < self.COUNT_REPETITIVE:
            return

        clusters = self._cluster_similar_comments(comments, threshold=self.SIMILARITY_THRESHOLD)
        repetitive_groups = [cl for cl in clusters if len(cl['comments']) >= self.COUNT_REPETITIVE]
        if not repetitive_groups:
            return

        repetitive_groups.sort(key=lambda item: len(item['comments']), reverse=True)
        penalty = int(_get_settings('penalty_repetitive', 12))
        self._add_penalty(result, penalty, f"Пользователь id{uid}: повторяющиеся комментарии")
        result['findings'].append({
            'type': 'repetitive_comments',
            'severity': 'HIGH',
            'summary': f"Найдено {len(repetitive_groups)} групп повторяющихся текстов",
            'examples': self._build_repetitive_examples(repetitive_groups, owner_id),
        })

    def _build_repetitive_examples(self, repetitive_groups, owner_id):
        examples = []
        for group in repetitive_groups:
            short_text = group['rep_text'][:100].replace('\n', ' ')
            instances = [self._comment_instance(comment, owner_id) for comment in group['comments']]
            examples.append({'pattern': short_text, 'count': len(group['comments']), 'instances': instances})
        return examples

    def _add_generic_comment_findings(self, result, uid, comments, owner_id):
        generic_comments = [comment for comment in comments if self._is_generic_comment(comment['text'])]
        if not generic_comments or len(generic_comments) < len(comments) * 0.6:
            return

        penalty = int(_get_settings('penalty_generic', 8))
        self._add_penalty(result, penalty, f"Пользователь id{uid}: {len(generic_comments)}/{len(comments)} шаблонных комментариев")
        result['findings'].append({
            'type': 'generic_comments',
            'severity': 'LOW',
            'summary': f"{len(generic_comments)}/{len(comments)} комментариев соответствуют шаблонным паттернам",
            'examples': [{'instances': [self._comment_instance(comment, owner_id) for comment in generic_comments]}],
        })

    def _add_rapid_comment_findings(self, result, uid, comments, owner_id):
        rapid_series = self._find_rapid_comment_series(comments, owner_id)
        if not rapid_series:
            return

        penalty = int(_get_settings('penalty_rapid', 15))
        self._add_penalty(result, penalty, f"Пользователь id{uid}: {len(rapid_series)} серий быстрых комментариев")
        result['findings'].extend(rapid_series)

    def _find_rapid_comment_series(self, comments, owner_id):
        dated_comments = self._sorted_dated_comments(comments)
        series = []
        index = 0

        while index < len(dated_comments) - 2:
            window_end_index = self._rapid_window_end_index(dated_comments, index)
            series_length = window_end_index - index + 1
            if series_length >= self.COMMENTS_PER_TIME_WINDOW:
                series.append(self._build_rapid_series(dated_comments, index, window_end_index, owner_id))
                index = window_end_index + 1
            else:
                index += 1

        return series

    def _rapid_window_end_index(self, dated_comments, start_index):
        window_end_limit = dated_comments[start_index]['date'] + self.RAPID_COMMENT_WINDOW_MIN * 60
        end_index = start_index
        for index in range(start_index, len(dated_comments)):
            if dated_comments[index]['date'] <= window_end_limit:
                end_index = index
            else:
                break
        return end_index

    def _build_rapid_series(self, dated_comments, start_index, end_index, owner_id):
        start_time = dated_comments[start_index]['date']
        end_time = dated_comments[end_index]['date']
        instances = [self._comment_instance(comment, owner_id) for comment in dated_comments[start_index:end_index + 1]]
        return {
            'type': 'rapid_comments',
            'severity': 'HIGH',
            'summary': f"{len(instances)} комментариев за {_format_duration(end_time - start_time)}",
            'examples': [{'instances': instances}],
        }

    def _add_regular_interval_finding(self, result, uid, comments, owner_id):
        dated_comments = self._sorted_dated_comments(comments)
        for index in range(len(dated_comments) - 2):
            window = dated_comments[index:index + 3]
            interval = self._regular_interval(window)
            if interval is None:
                continue

            penalty = int(_get_settings('penalty_regular', 10))
            self._add_penalty(result, penalty, f"Пользователь id{uid}: серия из 3 комментариев с интервалом ~{_format_duration(interval)}")
            result['findings'].append({
                'type': 'regular_interval',
                'severity': 'MEDIUM',
                'summary': f"Серия из 3 комментариев с интервалом ~{_format_duration(interval)}",
                'examples': [{'instances': [self._comment_instance(comment, owner_id) for comment in window]}],
            })
            return

    def _regular_interval(self, comments_window):
        intervals = [
            comments_window[1]['date'] - comments_window[0]['date'],
            comments_window[2]['date'] - comments_window[1]['date'],
        ]
        if any(interval <= 0 for interval in intervals):
            return None

        average_interval = sum(intervals) / len(intervals)
        max_deviation = max(abs(interval - average_interval) for interval in intervals)
        if average_interval < self.MIN_INTERVAL_FOR_REGULAR_CHECK:
            return None
        if max_deviation > self.REGULAR_INTERVAL_TOLERANCE_SEC:
            return None
        return average_interval

    def _add_night_activity_finding(self, result, uid, comments, owner_id):
        dated_comments = self._sorted_dated_comments(comments)
        night_comments = [comment for comment in dated_comments if self._is_night_comment(comment)]
        if len(dated_comments) < 3 or len(night_comments) / len(dated_comments) < 0.5:
            return

        penalty = int(_get_settings('penalty_night', 8))
        self._add_penalty(result, penalty, f"Пользователь id{uid}: {len(night_comments)}/{len(dated_comments)} комментариев ночью (03-05)")
        result['findings'].append({
            'type': 'night_activity',
            'severity': 'MEDIUM',
            'summary': f"{len(night_comments)}/{len(dated_comments)} комментариев ночью",
            'examples': [{'instances': [self._comment_instance(comment, owner_id) for comment in night_comments]}],
        })

    def _is_night_comment(self, comment):
        hour = datetime.fromtimestamp(comment['date'], tz=timezone.utc).astimezone(MSK_TZ).hour
        return 3 <= hour <= 5

    def _add_new_account_finding(self, result, uid, comments, likes, owner_id):
        total_activity = len(comments) + len(likes)
        if uid <= self.NEW_ACC_ID_THRESHOLD or total_activity < self.NEW_ACC_ACTIVITY:
            return

        penalty = int(_get_settings('penalty_new_acc', 10))
        self._add_penalty(result, penalty, f"Пользователь id{uid}: высокая активность нового аккаунта")
        result['findings'].append({
            'type': 'new_account_activity',
            'severity': 'MEDIUM',
            'summary': f"Новый аккаунт с большой активностью: {len(comments)} комментариев, {len(likes)} лайков",
            'examples': [{'instances': [self._comment_instance(comment, owner_id) for comment in comments]}],
        })

    def _sorted_dated_comments(self, comments):
        return sorted(
            [comment for comment in comments if comment.get('date')],
            key=lambda comment: comment['date'],
        )

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
            names.update(self._fetch_user_names_batch(batch))
        return names

    def _fetch_user_names_batch(self, batch):
        try:
            data, status = self.vk.request('users.get', {'user_ids': ','.join(map(str, batch))})
        except Exception:
            return {}

        if status != 'ok' or not data or 'response' not in data:
            return {}

        return {
            user.get('id'): self._format_user_name(user)
            for user in data['response']
        }

    def _format_user_name(self, user):
        uid = user.get('id')
        first_name = user.get('first_name', '').strip()
        last_name = user.get('last_name', '').strip()
        if first_name or last_name:
            return f"{first_name} {last_name} (id{uid})"
        return f"id{uid}"

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
