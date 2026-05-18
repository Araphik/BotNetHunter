import re
from analyzers.base_analyzer import BaseAnalyzer

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

class ProfileAnalyzer(BaseAnalyzer):
    def analyze(self, profile):
        score = 0
        reasons = []
        
        if profile.id:
            if profile.id > 850_000_000:
                penalty = int(_get_settings('penalty_prof_new_2024', 20))
                score += penalty
                reasons.append(f'Очень новый аккаунт (+{penalty} бал.)')
            elif profile.id > 780_000_000:
                penalty = int(_get_settings('penalty_prof_new_2022', 12))
                score += penalty
                reasons.append(f'Новый аккаунт (+{penalty} бал.)')
        
        full_name = f"{profile.first_name} {profile.last_name}".lower()
        bot_keywords = ['bot', 'spam', 'подпишись', 'накрут', 'раскрут', 'продвиж', 'лайк', 'фолловер']
        if any(kw in full_name for kw in bot_keywords):
            penalty = int(_get_settings('penalty_prof_bot_name', 15))
            score += penalty
            reasons.append(f"Имя содержит бот-ключ (+{penalty} бал.)")
        
        if len(profile.first_name) < 2 or len(profile.last_name) < 2:
            penalty = int(_get_settings('penalty_prof_short_name', 10))
            score += penalty
            reasons.append(f'Слишком короткое имя/фамилия (+{penalty} бал.)')
        
        if not profile.has_photo:
            penalty = int(_get_settings('penalty_prof_no_photo', 18))
            score += penalty
            reasons.append(f'Нет аватарки (+{penalty} бал.)')
        
        filled = sum([bool(profile.city), bool(profile.bdate), profile.has_about, profile.has_interests])
        if filled == 0:
            penalty = int(_get_settings('penalty_prof_empty_0', 25))
            score += penalty
            reasons.append(f'Профиль пуст (0/4, +{penalty} бал.)')
        elif filled == 1:
            penalty = int(_get_settings('penalty_prof_empty_1', 15))
            score += penalty
            reasons.append(f'Профиль почти пуст (1/4, +{penalty} бал.)')
        
        return min(score, 100), reasons