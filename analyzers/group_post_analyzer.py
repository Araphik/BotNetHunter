from datetime import datetime
from analyzers.base_analyzer import BaseAnalyzer
import re

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

class GroupPostAnalyzer(BaseAnalyzer):
    def analyze(self, posts: list, group_info: dict = None):
        score = 0
        reasons = []
        
        if not posts:
            return 0, []  # Убран штраф за отсутствие постов
            
        dates = [p.get('date') for p in posts if p.get('date')]
        texts = [p.get('text', '') for p in posts if p.get('text')]
        
        # 1. Частота публикаций
        if len(dates) >= 5:
            dates_sorted = sorted(dates)
            intervals = [(dates_sorted[i+1] - dates_sorted[i]) / 60 for i in range(len(dates_sorted)-1)]
            avg_interval = sum(intervals) / len(intervals)
            if avg_interval < 30:
                penalty = int(_get_settings('penalty_high_freq', 12))
                score += penalty
                reasons.append(f"Аномально высокая частота публикаций (~{avg_interval:.0f} мин)")
                
        # 2. Повторяемость контента
        if len(texts) >= 5:
            short_texts = [t[:100].lower().strip() for t in texts if t.strip()]
            if short_texts:
                unique_ratio = len(set(short_texts)) / len(short_texts)
                if unique_ratio < 0.2:
                    penalty = int(_get_settings('penalty_repetitive_content', 18))
                    score += penalty
                    reasons.append("Высокая повторяемость контента (возможно, автоматизированная публикация)")
                    
        # 3. Ссылки/сокращатели
        if texts:
            link_posts = sum(1 for t in texts if re.search(r'vk\.cc|bit\.ly|http', t, re.I))
            if link_posts / len(texts) > 0.7:
                penalty = int(_get_settings('penalty_link_spam', 10))
                score += penalty
                reasons.append("Подавляющее большинство публикаций содержат внешние ссылки")
                
        # 4. Ночные публикации
        if dates:
            night_count = sum(1 for d in dates if 2 <= datetime.fromtimestamp(d).hour <= 5)
            if len(dates) > 10 and night_count / len(dates) > 0.6:
                penalty = int(_get_settings('penalty_night_posting', 10))
                score += penalty
                reasons.append("Большинство публикаций размещается в ночное время (02:00-05:00)")
                
        # 5. Капс/эмодзи-спам
        if texts:
            caps_ratio = sum(1 for t in texts if t.isupper() and len(t) > 20) / len(texts)
            if caps_ratio > 0.5:
                penalty = int(_get_settings('penalty_caps', 8))
                score += penalty
                reasons.append("Частое использование заглавных букв в публикациях")
                
        return min(score, 100), reasons