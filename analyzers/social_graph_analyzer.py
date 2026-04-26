from collections import Counter
from analyzers.base_analyzer import BaseAnalyzer


class SocialGraphAnalyzer(BaseAnalyzer):
    def analyze(self, profile, friend_profiles=None, friends_hidden=False):
        score = 0
        reasons = []

        # 1. Скрытые друзья или 0 друзей
        if friends_hidden:
            score += 12
            reasons.append('Список друзей скрыт (частый признак бота)')
        elif profile.get_friends_count() == 0:
            score += 10
            reasons.append('0 друзей в профиле')

        if not friend_profiles or len(friend_profiles) < 3:
            return score, reasons

        # Безопасное получение городов (всегда определено)
        cities = [f.city for f in friend_profiles if f.city]

        # 2. География
        if profile.city and len(cities) >= 5:
            city_counts = Counter(cities)
            ratio = city_counts.get(profile.city, 0) / len(cities)
            if ratio < 0.1:
                score += 20
                top = city_counts.most_common(1)[0]
                reasons.append(f'Гео-аномалия: пользователь из {profile.city}, но только {int(ratio*100)}% друзей оттуда (чаще: {top[0]})')
            elif ratio < 0.25:
                score += 8
                reasons.append(f'Мало друзей из родного города: {int(ratio*100)}%')

        # 3. Концентрация в чужом городе
        if len(cities) >= 10:
            city_counts = Counter(cities)
            top_city, top_cnt = city_counts.most_common(1)[0]
            if top_city != profile.city and top_cnt / len(cities) > 0.6:
                score += 15
                reasons.append(f'Друзья сконцентрированы в {top_city} ({top_cnt}/{len(cities)}), а пользователь из другого города')

        # 4. Аватарки друзей
        no_photo = sum(1 for f in friend_profiles if not f.has_photo)
        if no_photo / len(friend_profiles) > 0.7:
            score += 12
            reasons.append(f'У большинства друзей нет аватарок ({no_photo}/{len(friend_profiles)})')

        # 5. Близкие ID (массовая регистрация)
        ids = sorted([f.id for f in friend_profiles if f.id])
        if len(ids) >= 5:
            close = sum(1 for i in range(len(ids)-1) if 0 < ids[i+1] - ids[i] < 50000)
            if close / len(ids) > 0.3:
                score += 18
                reasons.append(f'Много друзей с близкими ID ({close}/{len(ids)}) — массовая регистрация')

        return min(score, 100), reasons