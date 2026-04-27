from datetime import datetime
from analyzers.base_analyzer import BaseAnalyzer


class BehaviorAnalyzer(BaseAnalyzer):
    def analyze(self, profile, wall_posts=None, wall_hidden=False):
        score = 0
        reasons = []

        # 1. Стена
        if wall_hidden:
            score += 10
            reasons.append('Стена закрыта или недоступна')
        elif not wall_posts:
            score += 8
            reasons.append('Нет постов на стене')
        else:
            texts = [p.get('text', '').strip() for p in wall_posts if p.get('text')]
            if len(texts) >= 5:
                # Повторы
                short = [t[:80] for t in texts]
                unique = len(set(short)) / len(short)
                if unique < 0.3:
                    score += 15
                    reasons.append('Высокая повторяемость постов (автопостинг)')
                # Ссылки
                links = sum(1 for t in texts if 'http' in t.lower() or 'vk.cc' in t.lower())
                if links / len(texts) > 0.8:
                    score += 10
                    reasons.append('Подавляющее большинство постов содержат ссылки')
                # Регулярность
                dates = sorted([p.get('date') for p in wall_posts if p.get('date')])
                if len(dates) >= 5:
                    intervals = [dates[i+1] - dates[i] for i in range(len(dates)-1)]
                    avg = sum(intervals) / len(intervals)
                    if avg > 0:
                        var = sum((x - avg)**2 for x in intervals) / len(intervals)
                        if (var**0.5) / avg < 0.15:
                            score += 20
                            reasons.append('Слишком регулярные интервалы между постами (бот-расписание)')

        # 2. Активность
        last = profile.last_seen.get('time') if profile.last_seen else None
        if last:
            days = (datetime.now().timestamp() - last) / 86400
            if days > 365:
                score += 12
                reasons.append(f'Не активен более года ({int(days)} дней)')
            elif days > 180:
                score += 6
                reasons.append(f'Не активен {int(days)} дней')

        return min(score, 100), reasons