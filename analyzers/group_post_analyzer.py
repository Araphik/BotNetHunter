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
            if isinstance(val, (int, float)):
                return val
            return float(val)
    except Exception:
        pass
    return default


class GroupPostAnalyzer(BaseAnalyzer):

    def _check_high_frequency(self, dates: list) -> tuple[int, str | None]:
        """1. Частота публикаций"""
        if len(dates) < 5:
            return 0, None
        dates_sorted = sorted(dates)
        intervals = [(dates_sorted[i + 1] - dates_sorted[i]) / 60 for i in range(len(dates_sorted) - 1)]
        avg_interval = sum(intervals) / len(intervals)
        if avg_interval < 30:
            penalty = int(_get_settings('penalty_high_freq', 12))
            return penalty, f"Аномально высокая частота публикаций (~{avg_interval:.0f} мин)"
        return 0, None

    def _check_repetitive_content(self, texts: list) -> tuple[int, str | None]:
        """2. Повторяемость контента"""
        if len(texts) < 5:
            return 0, None
        short_texts = [t[:100].lower().strip() for t in texts if t.strip()]
        if short_texts and len(set(short_texts)) / len(short_texts) < 0.2:
            penalty = int(_get_settings('penalty_repetitive_content', 18))
            return penalty, "Высокая повторяемость контента (возможно, автоматизированная публикация)"
        return 0, None

    def _check_link_spam(self, texts: list) -> tuple[int, str | None]:
        """3. Ссылки/сокращатели"""
        if not texts:
            return 0, None
        link_posts = sum(1 for t in texts if re.search(r'vk\.cc|bit\.ly|http', t, re.I))
        if link_posts / len(texts) > 0.7:
            penalty = int(_get_settings('penalty_link_spam', 10))
            return penalty, "Подавляющее большинство публикаций содержат внешние ссылки"
        return 0, None

    def _check_night_posting(self, dates: list) -> tuple[int, str | None]:
        """4. Ночные публикации"""
        if not dates or len(dates) <= 10:
            return 0, None
        night_count = sum(1 for d in dates if 2 <= datetime.fromtimestamp(d).hour <= 5)
        if night_count / len(dates) > 0.6:
            penalty = int(_get_settings('penalty_night_posting', 10))
            return penalty, "Большинство публикаций размещается в ночное время (02:00-05:00)"
        return 0, None

    def _check_caps_spam(self, texts: list) -> tuple[int, str | None]:
        """5. Капс/эмодзи-спам"""
        if not texts:
            return 0, None
        caps_ratio = sum(1 for t in texts if t.isupper() and len(t) > 20) / len(texts)
        if caps_ratio > 0.5:
            penalty = int(_get_settings('penalty_caps', 8))
            return penalty, "Частое использование заглавных букв в публикациях"
        return 0, None

    def analyze(self, posts: list, group_info: dict = None):
        if not posts:
            return 0, []

        dates = [p.get('date') for p in posts if p.get('date')]
        texts = [p.get('text', '') for p in posts if p.get('text')]

        checks = [
            self._check_high_frequency(dates),
            self._check_repetitive_content(texts),
            self._check_link_spam(texts),
            self._check_night_posting(dates),
            self._check_caps_spam(texts),
        ]

        score = sum(penalty for penalty, _ in checks)
        reasons = [reason for _, reason in checks if reason]

        return min(score, 100), reasons