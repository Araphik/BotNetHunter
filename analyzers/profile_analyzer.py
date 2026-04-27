# analyzers/profile_analyzer.py
import re
from analyzers.base_analyzer import BaseAnalyzer


def _get_param_value(module_name: str, param_key: str, default: int) -> int:
    """Получает значение параметра из БД или возвращает дефолт"""
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
    except:
        return default


class ProfileAnalyzer(BaseAnalyzer):
    def analyze(self, profile):
        score = 0
        reasons = []

        if profile.id:
            if profile.id > 850_000_000:
                penalty = _get_param_value("profile_analyzer", "new_account_2024", 20)
                score += penalty
                reasons.append(f'Очень новый аккаунт (2024+, +{penalty} бал.)')
            elif profile.id > 780_000_000:
                penalty = _get_param_value("profile_analyzer", "new_account_2022", 12)
                score += penalty
                reasons.append(f'Новый аккаунт (2022-2024, +{penalty} бал.)')

        full_name = f"{profile.first_name} {profile.last_name}".lower()
        if re.search(r'<[a-z]|on\w+=|javascript:|alert\s*\(', full_name, re.I):
            score += 25
            reasons.append('Подозрительное имя (XSS/спам-паттерн)')
        else:
            bot_keywords = ['bot', 'spam', 'подпишись', 'накрут', 'раскрут', 'продвиж', 'лайк', 'фолловер']
            for kw in bot_keywords:
                if kw in full_name:
                    penalty = _get_param_value("profile_analyzer", "bot_keyword_name", 15)
                    score += penalty
                    reasons.append(f"Имя содержит бот-ключ: '{kw}' (+{penalty} бал.)")
                    break

        if len(profile.first_name) < 2 or len(profile.last_name) < 2:
            penalty = _get_param_value("profile_analyzer", "short_name", 10)
            score += penalty
            reasons.append(f'Слишком короткое имя/фамилия (+{penalty} бал.)')

        if not profile.has_photo:
            penalty = _get_param_value("profile_analyzer", "no_avatar", 18)
            score += penalty
            reasons.append(f'Нет аватарки (+{penalty} бал.)')
        elif self._is_default_avatar(profile):
            penalty = _get_param_value("profile_analyzer", "default_avatar", 8)
            score += penalty
            reasons.append(f'Дефолтная аватарка (+{penalty} бал.)')

        filled = sum([bool(profile.city), bool(profile.bdate), profile.has_about, profile.has_interests])
        if filled == 0:
            penalty = _get_param_value("profile_analyzer", "empty_profile_0", 25)
            score += penalty
            reasons.append(f'Профиль полностью пуст (0/4, +{penalty} бал.)')
        elif filled == 1:
            penalty = _get_param_value("profile_analyzer", "empty_profile_1", 15)
            score += penalty
            reasons.append(f'Почти пустой профиль (1/4, +{penalty} бал.)')
        elif filled == 2:
            penalty = _get_param_value("profile_analyzer", "empty_profile_2", 8)
            score += penalty
            reasons.append(f'Мало данных в профиле (2/4, +{penalty} бал.)')

        if profile.bdate and not self._validate_bdate(profile.bdate):
            penalty = _get_param_value("profile_analyzer", "invalid_bdate", 8)
            score += penalty
            reasons.append(f'Подозрительная дата рождения (+{penalty} бал.)')

        return min(score, 100), reasons

    def _is_default_avatar(self, profile):
        url = profile.get_photo_url()
        if not url:
            return False
        return any(p in url.lower() for p in ['camera', 'question', 'no_photo', 'default', 'standart', 'emoji'])

    def _validate_bdate(self, bdate):
        try:
            parts = bdate.split('.')
            if len(parts) == 3:
                year = int(parts[2])
                return 1920 <= year <= 2010
            return True
        except:
            return False