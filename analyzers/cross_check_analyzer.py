from analyzers.base_analyzer import BaseAnalyzer


class CrossCheckAnalyzer(BaseAnalyzer):
    """Кросс-проверка: сопоставление полей профиля между собой и с данными друзей"""

    def analyze(self, profile, friend_profiles=None):
        score = 0
        reasons = []

        if not friend_profiles:
            return 0, []

        # 1. Город пользователя vs город в образовании/работе
        if profile.city and profile.universities:
            uni_cities = [
                u.get('city', '') for u in profile.universities 
                if isinstance(u.get('city'), str) and u.get('city')
            ]
            if uni_cities and profile.city not in uni_cities:
                # Не всегда ошибка, но если все вузы в другом городе — подозрительно
                if all(city != profile.city for city in uni_cities):
                    score += 10
                    reasons.append(
                        f'Город проживания ({profile.city}) не совпадает с городами вузов'
                    )

        # 2. Город пользователя vs родной город
        if profile.city and profile.home_town and profile.city != profile.home_town:
            # Допустимо, но если друзья из home_town, а не из city — странно
            friend_hometowns = [f.home_town for f in friend_profiles if f.home_town]
            if friend_hometowns:
                hometown_ratio = friend_hometowns.count(profile.home_town) / len(friend_hometowns)
                city_ratio = [f.city for f in friend_profiles if f.city].count(profile.city) / len([f.city for f in friend_profiles if f.city]) if any(f.city for f in friend_profiles) else 0
                if hometown_ratio > 0.5 and city_ratio < 0.1:
                    score += 8
                    reasons.append(
                        f'Друзья чаще из родного города ({profile.home_town}), '
                        f'чем из заявленного ({profile.city})'
                    )

        # 3. Заполненность профиля пользователя относительно друзей
        user_completeness = sum([
            profile.has_photo, profile.has_about, 
            profile.has_interests, bool(profile.city), bool(profile.bdate)
        ])
        
        friend_completeness = [
            sum([f.has_photo, f.has_about, f.has_interests, bool(f.city), bool(f.bdate)])
            for f in friend_profiles
        ]
        
        if friend_completeness:
            avg_friend = sum(friend_completeness) / len(friend_completeness)
            
            # Пользователь значительно беднее друзей
            if user_completeness <= 1 and avg_friend >= 3.5:
                score += 15
                reasons.append(
                    f'Профиль пользователя (заполнено {user_completeness}/5) '
                    f'значительно беднее, чем у друзей (в среднем {avg_friend:.1f}/5)'
                )
            # Пользователь значительно полнее друзей (возможно, фейковые друзья)
            elif user_completeness >= 4 and avg_friend <= 1.5 and len(friend_completeness) >= 10:
                score += 12
                reasons.append(
                    f'Профиль пользователя значительно полнее, чем у друзей '
                    f'(возможно, накрученные подписчики)'
                )

        # 4. Соотношение подписчики/друзья относительно круга общения
        if profile.get_friends_count() > 50 and profile.get_followers_count() > 100:
            user_ratio = profile.get_followers_count() / profile.get_friends_count()
            friend_ratios = [
                f.get_followers_count() / max(f.get_friends_count(), 1)
                for f in friend_profiles if f.get_friends_count() > 0
            ]
            if friend_ratios:
                avg_ratio = sum(friend_ratios) / len(friend_ratios)
                if user_ratio > avg_ratio * 8 and user_ratio > 5:
                    score += 10
                    reasons.append(
                        f'Аномально высокое соотношение подписчики/друзья '
                        f'({profile.get_followers_count()}/{profile.get_friends_count()} = {user_ratio:.1f}x, '
                        f'у друзей в среднем {avg_ratio:.1f}x)'
                    )

        # 5. Возраст аккаунта пользователя относительно друзей
        if profile.id:
            user_age_score = self._id_to_age_score(profile.id)
            friend_age_scores = [self._id_to_age_score(f.id) for f in friend_profiles if f.id]
            if friend_age_scores and len(friend_age_scores) >= 10:
                avg_friend_age = sum(friend_age_scores) / len(friend_age_scores)
                if abs(user_age_score - avg_friend_age) >= 6:
                    score += 12
                    reasons.append(
                        f'Возраст аккаунта не соответствует кругу общения '
                        f'(разница ~{abs(user_age_score - avg_friend_age)} лет по дате регистрации)'
                    )

        return min(score, 100), reasons

    def _id_to_age_score(self, user_id):
        """Преобразует ID в условный возраст аккаунта (меньше = старше)"""
        if user_id > 850_000_000: return 1
        elif user_id > 800_000_000: return 2
        elif user_id > 750_000_000: return 3
        elif user_id > 700_000_000: return 4
        elif user_id > 600_000_000: return 6
        elif user_id > 500_000_000: return 8
        elif user_id > 400_000_000: return 10
        else: return 12