import json
import time
import re
from collections import defaultdict, Counter
import threading
from core.vk_client import VKClient
from models.user_profile import UserProfile
from models.analysis_result import AnalysisResult
from analyzers.profile_analyzer import ProfileAnalyzer
from analyzers.group_post_analyzer import GroupPostAnalyzer
from analyzers.activity_analyzer import ActivityAnalyzer
from utils.helpers import parse_user_input
from utils.logger import logger
from config.settings import REQUEST_DELAY

distribution_lock = threading.Lock()

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

def _normalize_target(target: str) -> str:
    """Преобразует ссылку VK в чистый username"""
    if not target.startswith('http'):
        return target
    match = re.search(r'vk\.(?:com|ru)/([^/?#]+)', target)
    if match:
        return match.group(1)
    return target

def _normalize_group_id(group_input: str) -> str:
    """Преобразует ссылку в формат, понятный VK API"""
    if re.match(r'^-?\d+$', group_input):
        return group_input
    if group_input.startswith('http'):
        match = re.search(r'vk\.(?:com|ru)/([^/?#]+)', group_input)
        if match:
            screen_name = match.group(1)
            return screen_name
    return group_input

def analyze_user(user_input: str, token_manager, fast_mode: bool = True) -> dict | None:
    """
    Анализ профиля пользователя (только быстрый режим).
    Возвращает dict: {'score': 0-100, 'risk_level': str, 'reasons': [{'reason': str, 'points': int}, ...]}
    """
    vk = VKClient(token_manager)
    parsed_id = parse_user_input(user_input)
    
    fields = 'screen_name,photo_max_orig,photo_200,city,country,sex,bdate,last_seen,counters'
    data, status = vk.get_user(parsed_id, fields)
    if status != 'ok' or not data or 'response' not in data or not data['response']:
        logger.error(f'Ошибка {status} при получении данных {parsed_id}')
        return None
    
    profile = UserProfile(data['response'][0])
    total_score = 0
    all_reasons = []
    
    # Быстрый анализ профиля
    score, reasons = ProfileAnalyzer(vk).analyze(profile)
    for reason in reasons:
        # Извлекаем баллы из строки вида "Нет аватарки (+18 бал.)"
        match = re.search(r'\+\s*(\d+)\s*бал', reason)
        points = int(match.group(1)) if match else 0
        clean_reason = re.sub(r'\s*\(\+\d+\s*бал\.\)$', '', reason)
        all_reasons.append({'reason': clean_reason, 'points': points})
        total_score += points
    
    # Проверка друзей (гео-аномалия)
    try:
        fr_data, fr_status = vk.get_friends(profile.id, count=30)
        if fr_status == 'ok' and fr_data and 'response' in fr_data and fr_data['response'].get('items'):
            ids = [f['id'] for f in fr_data['response']['items'][:30] if f['id'] > 0]
            if ids and profile.city:
                f_data, _ = vk.get_user(','.join(map(str, ids)), 'city')
                if f_data and 'response' in f_data:
                    cities = [f.get('city', {}).get('title') for f in f_data['response'] if f.get('city')]
                    if cities:
                        city_counts = Counter(cities)
                        ratio = city_counts.get(profile.city, 0) / len(cities)
                        if ratio < 0.1:
                            penalty = int(_get_settings('penalty_prof_geo_anomaly', 20))
                            total_score += penalty
                            top = city_counts.most_common(1)[0]
                            all_reasons.append({
                                'reason': f'Гео-аномалия: пользователь из {profile.city}, но только {int(ratio*100)}% друзей оттуда (чаще: {top[0]})',
                                'points': penalty
                            })
    except Exception:
        pass
    
    return {
        'score': min(total_score, 100),
        'risk_level': 'HIGH' if total_score >= 70 else 'MEDIUM' if total_score >= 40 else 'LOW' if total_score >= 15 else 'NORMAL',
        'reasons': all_reasons
    }

def analyze_group(group_id: str, token_manager, max_members: int = 100) -> dict | None:
    vk = VKClient(token_manager)
    normalized_id = _normalize_group_id(group_id)
    logger.info(f"Начало анализа группы {group_id} (нормализовано: {normalized_id})")
    
    posts_limit = _get_param_value("group_post_analyzer", "posts_limit", 100)
    comments_limit = _get_param_value("group_post_analyzer", "comments_limit", 100000)
    
    group_data, status = vk.request('groups.getById', {'group_id': normalized_id})
    if status != 'ok' or not group_data or 'response' not in group_data:
        logger.error(f"Не удалось получить данные группы {normalized_id}")
        return None
        
    group_info = group_data['response'][0] if isinstance(group_data['response'], list) else group_data['response']
    group_id_numeric = group_info.get('id')
    owner_id = f"-{group_id_numeric}"
    
    posts_data, posts_status = vk.request('wall.get', {
        'owner_id': owner_id, 'count': posts_limit, 'filter': 'owner'
    })
    posts = []
    if posts_status == 'ok' and posts_data and 'response' in posts_data:
        posts = posts_data['response'].get('items', [])
        logger.info(f"Загружено постов: {len(posts)}")
        
    post_score, post_reasons = GroupPostAnalyzer(vk).analyze(posts, group_info)
    
    posts_with_engagement = []
    for i, post in enumerate(posts):
        post_id = post.get('id')
        if not post_id: continue
        if i % 10 == 0: logger.info(f"Обработка поста {i+1}/{len(posts)}...")
        likes_data, _ = vk.get_post_likes(owner_id, post_id, count=1000)
        like_users = likes_data.get('items', []) if likes_data and isinstance(likes_data, dict) else []
        comments = vk.get_post_comments_batch(owner_id, post_id, max_count=comments_limit)
        posts_with_engagement.append({
            'id': post_id,
            'text': post.get('text', ''),
            'date': post.get('date'),
            'likes': {'count': len(like_users), 'users': like_users},
            'comments': comments
        })
        time.sleep(REQUEST_DELAY)
        
    logger.info(f"Постов с активностью: {len(posts_with_engagement)}")
    
    activity_score, activity_reasons, activity_findings = ActivityAnalyzer(vk).analyze(
        posts_with_engagement, owner_id=owner_id
    )
    
    # Итоговый скор: взвешенная сумма поста и активности
    total_score = round(post_score * 0.5 + activity_score * 0.5)
    all_reasons = post_reasons + activity_reasons
    
    reason_counts = defaultdict(int)
    for r in all_reasons:
        r_lower = r.lower()
        if "повторяющиеся" in r_lower or "повторяемость" in r_lower:
            reason_counts["Повторяющиеся тексты"] += 1
        elif "шаблон" in r_lower:
            reason_counts["Шаблонные комментарии"] += 1
        elif "быстрых" in r_lower or "серий быстрых" in r_lower:
            reason_counts["Быстрая серия комментариев"] += 1
        elif "интервалом" in r_lower or "строгий интервал" in r_lower:
            reason_counts["Строгий интервал публикаций"] += 1
        elif "ночн" in r_lower or "03-05" in r_lower or "ночью" in r_lower:
            reason_counts["Ночная активность"] += 1
        elif "массовые лайки" in r_lower:
            reason_counts["Массовые лайки"] += 1
        elif "нового аккаунта" in r_lower or "высокую активность" in r_lower:
            reason_counts["Активность новых аккаунтов"] += 1
        elif "скоординированных" in r_lower:
            reason_counts["Скоординированные действия"] += 1
        elif "аномально много" in r_lower or "повышенное количество" in r_lower:
            reason_counts["Общий спам в группе"] += 1

    summary = [{"label": k, "count": v} for k, v in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)]
    
    total_comments = sum(len(p.get('comments', [])) for p in posts_with_engagement)
    unique_commenters = len(set(
        c.get('from_id') for p in posts_with_engagement
        for c in p.get('comments', []) if c.get('from_id') and c.get('from_id') > 0
    ))
    
    details = {
        "reasons": all_reasons,
        "summary": summary,
        "posts_analyzed": len(posts),
        "engagement_posts": len(posts_with_engagement),
        "total_comments": total_comments,
        "unique_commenters": unique_commenters,
        "findings": activity_findings
    }
    
    logger.info(f"Анализ группы завершён. Скор: {total_score}, комментариев: {total_comments}, нарушений: {len(activity_findings)}")
    
    return {
        "type": "group", "group_id": group_id, "members_analyzed": 1,
        "average_score": total_score,
        "distribution": {f"{i}-{i+10}": 1 if i <= total_score < i+10 else 0 for i in range(0, 100, 10)},
        "scores": [total_score], "reasons": all_reasons,
        "posts_analyzed": len(posts), "engagement_posts": len(posts_with_engagement),
        "details": details
    }

def _get_param_value(module_name: str, param_key: str, default: int) -> int:
    """Получает значение параметра из БД или возвращает дефолт (для совместимости с group_post_analyzer)"""
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

__all__ = ['analyze_user', 'analyze_group']