# analyzers/activity_analyzer.py
from analyzers.base_analyzer import BaseAnalyzer
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import re


MSK_TZ = timezone(timedelta(hours=3))

SIMILARITY_THRESHOLD = 0.85
COUNT_REPETITIVE = 3
RAPID_COMMENT_WINDOW_MIN = 5
COMMENTS_PER_TIME_WINDOW = 5
REGULAR_INTERVAL_TOLERANCE_SEC = 10
MIN_INTERVAL_FOR_REGULAR_CHECK = 30
PERCENT_LIKED = 80
CROSS_USER_MIN_GROUP_SIZE = 3

GENERIC_PHRASES = ['класс', 'круто', 'лайк', '+', 'спс', 'хорошо', 'годнота', '👍', '🔥']
PROMO_KEYWORDS = ['заказывайте', 'акция', 'переходи', 'промокод', 'скидка', 'бесплатно', 'выигрыш', 'приз', 'розыгрыш']


def _get_param_value(module_name: str, param_key: str, default: int) -> int:
    try:
        from app.database import SessionLocal
        from app.models import ModuleParameter
        db = SessionLocal()
        param = db.query(ModuleParameter).filter(
            ModuleParameter.module_name == module_name,
            ModuleParameter.param_key == param_key
        ).first()
        db.close()
        return param.param_value if param else default
    except Exception:
        return default


def _format_msk_time(timestamp):
    if not timestamp:
        return ''
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.astimezone(MSK_TZ).strftime('%Y-%m-%d %H:%M:%S')


class ActivityAnalyzer(BaseAnalyzer):
    """Анализ активности пользователей под постами: комментарии, лайки, паттерны поведения"""
    
    def __init__(self, vk_client):
        super().__init__(vk_client)

    def analyze(self, posts_with_engagement: list, owner_id: str = None):
        score = 0
        reasons = []
        findings = []

        if not posts_with_engagement:
            return 0, [], findings

        total_comments = 0
        bot_pattern_count = 0
        user_data = defaultdict(lambda: {'comments': [], 'likes': 0, 'times': []})
        user_ids = set()

        for post in posts_with_engagement:
            post_id = post.get('id')
            comments = post.get('comments', [])
            total_comments += len(comments)
            
            for c in comments:
                uid = c.get('from_id')
                text = c.get('text', '').strip()
                date = c.get('date')
                comment_id = c.get('id')
                
                if uid and uid > 0 and text:
                    user_ids.add(uid)
                    user_data[uid]['comments'].append({
                        'text': text, 'date': date, 'post_id': post_id, 'comment_id': comment_id
                    })
                    if date:
                        user_data[uid]['times'].append(date)
                    if self._is_generic_comment(text):
                        bot_pattern_count += 1
        
        user_names = self._fetch_user_names(list(user_ids))
        
        # Общая статистика
        if total_comments >= 3:
            bot_ratio = bot_pattern_count / total_comments if total_comments > 0 else 0
            if bot_ratio > 0.7:
                score += 20
                reasons.append(f"Аномально много шаблонных комментариев ({bot_ratio*100:.0f}%)")
            elif bot_ratio > 0.4:
                score += 10
                reasons.append(f"Повышенное количество шаблонных комментариев ({bot_ratio*100:.0f}%)")

        # Анализ по пользователям
        for uid, data in user_data.items():
            comments = data['comments']
            times = data['times']
            
            if len(comments) < 2:
                continue
            
            user_findings = []
            
            # 1. Повторяющиеся комментарии
            if len(comments) >= COUNT_REPETITIVE:
                clusters = self._cluster_similar_comments(comments, threshold=SIMILARITY_THRESHOLD)
                repetitive_groups = [cl for cl in clusters if len(cl['comments']) >= COUNT_REPETITIVE]
                
                if repetitive_groups:
                    repetitive_groups.sort(key=lambda x: len(x['comments']), reverse=True)
                    score += 12
                    reasons.append(f"Пользователь id{uid}: повторяющиеся комментарии")
                    
                    examples = []
                    for group in repetitive_groups[:3]:
                        short_text = group['rep_text'][:100].replace('\n', ' ')
                        if len(group['rep_text']) > 100:
                            short_text += "..."
                        instances = []
                        for c in group['comments'][:10]:
                            instances.append({
                                'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id),
                                'time': _format_msk_time(c['date']),
                                'text': c['text'][:200]
                            })
                        examples.append({
                            'pattern': short_text,
                            'count': len(group['comments']),
                            'instances': instances
                        })
                    
                    user_findings.append({
                        'type': 'repetitive_comments',
                        'severity': 'HIGH',
                        'summary': f"Найдено {len(repetitive_groups)} групп повторяющихся текстов",
                        'examples': examples
                    })

            # 2. Шаблонные комментарии
            generic_count = sum(1 for c in comments if self._is_generic_comment(c['text']))
            if generic_count >= len(comments) * 0.6:
                score += 8
                reasons.append(f"Пользователь id{uid}: {generic_count}/{len(comments)} шаблонных комментариев")
                
                examples_list = []
                for c in [x for x in comments if self._is_generic_comment(x['text'])][:5]:
                    examples_list.append({
                        'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id),
                        'time': _format_msk_time(c['date']),
                        'text': c['text'][:200]
                    })
                
                user_findings.append({
                    'type': 'generic_comments',
                    'severity': 'LOW',
                    'summary': f"{generic_count}/{len(comments)} комментариев соответствуют шаблонным паттернам",
                    'examples': [{'instances': examples_list}]
                })

            # 3. Быстрая серия
            if len(times) >= 3:
                sorted_comments = sorted(comments, key=lambda x: x['date'] or 0)
                sorted_times = [c['date'] for c in sorted_comments if c['date']]
                if len(sorted_times) >= 3:
                    for i in range(len(sorted_times) - 2):
                        window = sorted_times[i:i+3]
                        if window[-1] - window[0] <= RAPID_COMMENT_WINDOW_MIN * 60:
                            score += 15
                            reasons.append(f"Пользователь id{uid}: 5+ комментариев за 5 минут")
                            
                            instances = []
                            for c in sorted_comments[i:i+3]:
                                instances.append({
                                    'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id),
                                    'time': _format_msk_time(c['date']),
                                    'text': c['text'][:200]
                                })
                            
                            user_findings.append({
                                'type': 'rapid_comments',
                                'severity': 'HIGH',
                                'summary': f"3 комментария за {window[-1] - window[0]} секунд",
                                'examples': [{'instances': instances}]
                            })
                            break

            # 4. Строгий интервал
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
                            if avg_w >= MIN_INTERVAL_FOR_REGULAR_CHECK and max_dev <= REGULAR_INTERVAL_TOLERANCE_SEC:
                                score += 10
                                reasons.append(f"Пользователь id{uid}: серия из 3 комментариев с интервалом ~{int(avg_w)}с")
                                
                                instances = []
                                for c in sorted_c[i : i+3]:
                                    instances.append({
                                        'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id),
                                        'time': _format_msk_time(c['date']),
                                        'text': c['text'][:200]
                                    })
                                
                                user_findings.append({
                                    'type': 'regular_interval',
                                    'severity': 'MEDIUM',
                                    'summary': f"Серия из 3 комментариев с интервалом ~{int(avg_w)}с (+/-{int(max_dev)}с)",
                                    'examples': [{'instances': instances}]
                                })
                                break

            # 5. Ночная активность (03:00-05:00)
            if len(comments) >= 3:
                times_list = [c['date'] for c in comments if c['date']]
                if times_list:
                    night_count = sum(1 for ts in times_list if 3 <= datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(MSK_TZ).hour <= 5)
                    if night_count / len(times_list) >= 0.5:
                        score += 8
                        reasons.append(f"Пользователь id{uid}: {night_count}/{len(times_list)} комментариев в период 03:00-05:00")
                        
                        examples_list = []
                        for c in [x for x in comments if x['date'] and 3 <= datetime.fromtimestamp(x['date'], tz=timezone.utc).astimezone(MSK_TZ).hour <= 5][:5]:
                            examples_list.append({
                                'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id),
                                'time': _format_msk_time(c['date']),
                                'text': c['text'][:200]
                            })
                        
                        user_findings.append({
                            'type': 'night_activity',
                            'severity': 'MEDIUM',
                            'summary': f"{night_count}/{len(times_list)} комментариев в период 03:00-05:00",
                            'examples': [{'instances': examples_list}]
                        })

            # 6. Новый аккаунт с высокой активностью
            if uid > 800_000_000:
                activity_score = len(comments) + data['likes']
                if activity_score >= 5:
                    score += 10
                    reasons.append(f"Пользователь id{uid}: новый аккаунт с высокой активностью ({activity_score} действий)")
                    
                    instances = []
                    for c in comments[:5]:
                        instances.append({
                            'link': self._build_comment_link(c['post_id'], c['comment_id'], owner_id),
                            'time': _format_msk_time(c['date']),
                            'text': c['text'][:200]
                        })
                    
                    user_findings.append({
                        'type': 'new_account_high_activity',
                        'severity': 'MEDIUM',
                        'summary': f"Аккаунт ~2022+, всего действий: {activity_score}",
                        'examples': [{'instances': instances}]
                    })

            if user_findings:
                user_name = user_names.get(uid, f"id{uid}")
                findings.append({
                    'user_id': uid,
                    'user_name': user_name,
                    'patterns': user_findings
                })

        # 7. Межпользовательские совпадения (координация)
        network_findings = self._detect_coordinated_comments(user_data, owner_id)
        if network_findings:
            score += 15
            reasons.append(f"Обнаружено {len(network_findings)} групп скоординированных комментариев")
            findings.append({'type': 'network_coordination', 'groups': network_findings})

        # 8. Массовые лайки
        total_posts = len(posts_with_engagement)
        if total_posts >= 3:
            threshold_count = int(total_posts * PERCENT_LIKED / 100)
            for uid, data in user_data.items():
                like_activity = len([p for p in posts_with_engagement if p.get('likes', {}).get('count', 0) > 0])
                if like_activity >= threshold_count and like_activity > 2:
                    score += 10
                    reasons.append(f"Пользователь id{uid}: массовые лайки ({like_activity}/{total_posts} постов)")
                    
                    findings.append({
                        'user_id': uid,
                        'user_name': user_names.get(uid, f"id{uid}"),
                        'patterns': [{
                            'type': 'mass_likes',
                            'severity': 'MEDIUM',
                            'summary': f"Лайкнул {like_activity}/{total_posts} записей ({PERCENT_LIKED}%+)",
                            'examples': []
                        }]
                    })

        return min(score, 100), reasons, findings

    def _is_generic_comment(self, text):
        text_lower = text.lower().strip()
        if len(text_lower) <= 5:
            return True
        if re.match(r'^[\W_]+$', text_lower):
            return True
        return text_lower in GENERIC_PHRASES

    def _fetch_user_names(self, user_ids):
        if not user_ids or not self.vk:
            return {}
        
        names = {}
        batches = [user_ids[i:i+500] for i in range(0, len(user_ids), 500)]
        
        for batch in batches:
            try:
                data, status = self.vk.request('users.get', {
                    'user_ids': ','.join(map(str, batch)),
                    'fields': ''
                })
                
                if status == 'ok' and data and 'response' in data:
                    for user in data['response']:
                        uid = user.get('id')
                        first_name = user.get('first_name', '')
                        last_name = user.get('last_name', '')
                        names[uid] = f"{first_name} {last_name} (id{uid})"
            except Exception:
                pass
        
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
            if not added:
                clusters.append({'rep_text': txt, 'comments': [c]})
        return clusters

    def _text_similarity(self, a, b):
        if a == b:
            return 1.0
        if not a or not b:
            return 0.0
        wa = set(re.findall(r'\w+', a))
        wb = set(re.findall(r'\w+', b))
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa | wb)

    def _build_comment_link(self, post_id, comment_id, owner_id=None):
        if not post_id:
            return "https://vk.com/feed"
        
        if owner_id:
            wall_id = owner_id
        else:
            wall_id = str(post_id).split('_')[0] if '_' in str(post_id) else post_id
        
        return f"https://vk.com/wall{wall_id}_{post_id}?reply={comment_id}"

    def _detect_coordinated_comments(self, user_data, owner_id=None):
        text_groups = defaultdict(list)
        
        for uid, data in user_data.items():
            for c in data['comments']:
                norm = re.sub(r'[^\w\s]', '', c['text'].lower().strip())
                norm = re.sub(r'\s+', ' ', norm).strip()
                if len(norm) > 3:
                    text_groups[norm].append({
                        'user_id': uid,
                        'text': c['text'],
                        'date': c['date'],
                        'post_id': c['post_id'],
                        'comment_id': c['comment_id']
                    })
        
        coordinated = []
        for norm, group in text_groups.items():
            uids = set(item['user_id'] for item in group)
            if len(uids) >= CROSS_USER_MIN_GROUP_SIZE:
                examples = []
                for item in group[:5]:
                    examples.append({
                        'user_id': item['user_id'],
                        'link': self._build_comment_link(item['post_id'], item['comment_id'], owner_id),
                        'time': _format_msk_time(item['date']),
                        'text': item['text'][:150]
                    })
                coordinated.append({
                    'users': sorted(list(uids)),
                    'pattern': norm[:100],
                    'count': len(group),
                    'examples': examples
                })
        
        return sorted(coordinated, key=lambda x: len(x['users']), reverse=True)[:5]