# api/endpoints.py
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from core.vk_client import VKClient
from models.user_profile import UserProfile
from models.analysis_result import AnalysisResult
from analyzers.profile_analyzer import ProfileAnalyzer
from analyzers.social_graph_analyzer import SocialGraphAnalyzer
from analyzers.behavior_analyzer import BehaviorAnalyzer
from analyzers.cross_check_analyzer import CrossCheckAnalyzer
from analyzers.group_post_analyzer import GroupPostAnalyzer
from analyzers.activity_analyzer import ActivityAnalyzer
from utils.helpers import parse_user_input
from utils.logger import logger
from config.settings import REQUEST_DELAY

# Лок для безопасного обновления распределения в многопоточном режиме
distribution_lock = threading.Lock()


def _get_param_value(module_name: str, param_key: str, default: int) -> int:
    """Получает значение параметра из БД или возвращает значение по умолчанию"""
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


def _analyze_single_profile_fast(profile: UserProfile, vk: VKClient) -> int:
    """
    Упрощённый, но комплексный анализ профиля для группы.
    Возвращает только score (0-100).
    """
    score = 0
    
    # 1. Базовые проверки (аналог ProfileAnalyzer)
    if profile.id:
        if profile.id > 850_000_000:
            score += 20  # Очень новый аккаунт
        elif profile.id > 780_000_000:
            score += 12
    
    # Имя
    full_name = f"{profile.first_name} {profile.last_name}".lower()
    bot_keywords = ['bot', 'spam', 'подпишись', 'накрут', 'раскрут', 'продвиж', 'лайк', 'фолловер']
    if any(kw in full_name for kw in bot_keywords):
        score += 15
    
    if len(profile.first_name) < 2 or len(profile.last_name) < 2:
        score += 10
    
    # Аватарка
    if not profile.has_photo:
        score += 18
    
    # Заполненность профиля
    filled = sum([bool(profile.city), bool(profile.bdate), profile.has_about, profile.has_interests])
    if filled == 0:
        score += 25
    elif filled == 1:
        score += 15
    elif filled == 2:
        score += 8
    
    # 2. Быстрая проверка друзей
    try:
        fr_data, fr_status = vk.get_friends(profile.id, count=30)
        if fr_status == 'ok' and fr_data and 'response' in fr_data:
            friends_count = len(fr_data['response'].get('items', []))
            if friends_count == 0:
                score += 10
            elif friends_count < 10:
                score += 5
        else:
            score += 8  # Друзья скрыты
    except Exception:
        pass
    
    # 3. Быстрая проверка стены
    try:
        w_data, w_status = vk.get_wall(profile.id, count=20)
        if w_status == 'ok' and w_data and 'response' in w_data:
            posts = w_data['response'].get('items', [])
            if not posts:
                score += 8  # Нет постов
            else:
                texts = [p.get('text', '') for p in posts if p.get('text')]
                if texts:
                    link_posts = sum(1 for t in texts if 'http' in t.lower() or 'vk.cc' in t.lower())
                    if link_posts / len(texts) > 0.7:
                        score += 10
    except Exception:
        pass
    
    return min(score, 100)


def analyze_user(user_input: str, token_manager) -> AnalysisResult | None:
    """Анализ одиночного профиля (полноценный)"""
    vk = VKClient(token_manager)
    parsed_id = parse_user_input(user_input)
    
    fields = 'screen_name,photo_max_orig,photo_200,city,country,sex,bdate,last_seen,counters,about,interests,universities,career,contacts'
    data, status = vk.get_user(parsed_id, fields)
    
    if status != 'ok' or not data or 'response' not in data or not data['response']:
        logger.error(f'Ошибка {status} при получении данных {parsed_id}')
        return None

    profile = UserProfile(data['response'][0])
    
    # Полноценный анализ через все модули
    result = AnalysisResult(profile.id)
    result.profile_data = profile

    # 1. Профиль
    score, reasons = ProfileAnalyzer(vk).analyze(profile)
    for reason in reasons:
        result.add_score(score, reason, 'high' if score >= 15 else 'medium' if score >= 8 else 'low')

    # 2. Друзья
    fr_data, fr_status = vk.get_friends(profile.id, count=50)
    friends_hidden = fr_status != 'ok' or not fr_data or 'error' in fr_data
    friend_profiles = None
    if not friends_hidden and fr_data and 'response' in fr_data and fr_data['response'].get('items'):
        ids = fr_data['response']['items'][:30]
        if ids:
            f_data, _ = vk.get_user(','.join(map(str, ids)), 'city,about,interests,counters,photo_200')
            if f_data and 'response' in f_data:
                friend_profiles = [UserProfile(f) for f in f_data['response']]
    
    score, reasons = SocialGraphAnalyzer(vk).analyze(profile, friend_profiles, friends_hidden)
    for reason in reasons:
        result.add_score(score, reason, 'high' if score >= 15 else 'medium' if score >= 8 else 'low')

    # 3. Стена
    w_data, w_status = vk.get_wall(profile.id, count=30)
    wall_hidden = w_status != 'ok' or not w_data or 'error' in w_data
    wall_posts = w_data['response']['items'] if not wall_hidden else None
    score, reasons = BehaviorAnalyzer(vk).analyze(profile, wall_posts, wall_hidden)
    for reason in reasons:
        result.add_score(score, reason, 'high' if score >= 15 else 'medium' if score >= 8 else 'low')

    # 4. Кросс-проверка
    if friend_profiles:
        score, reasons = CrossCheckAnalyzer(vk).analyze(profile, friend_profiles)
        for reason in reasons:
            result.add_score(score, reason, 'high' if score >= 15 else 'medium' if score >= 8 else 'low')

    result.calculate_risk()
    return result


def analyze_group(group_id: str, token_manager, max_members: int = 100) -> dict | None:
    """
    Анализ группы: посты и активность под ними.
    Списывается 1 запрос у пользователя независимо от количества постов.
    """
    vk = VKClient(token_manager)
    logger.info(f"Начало анализа группы {group_id}")
    
    posts_limit = _get_param_value("group_post_analyzer", "posts_limit", 100)
    comments_limit = _get_param_value("group_post_analyzer", "comments_limit", 10000)
    
    group_data, status = vk.request('groups.getById', {'group_id': group_id})
    if status != 'ok' or not group_data or 'response' not in group_data:
        logger.error(f"Не удалось получить данные группы {group_id}")
        return None
        
    group_info = group_data['response'][0] if isinstance(group_data['response'], list) else group_data['response']
    group_id_numeric = group_info.get('id')
    owner_id = f"-{group_id_numeric}"
    
    posts_data, posts_status = vk.request('wall.get', {
        'owner_id': owner_id,
        'count': posts_limit,
        'filter': 'owner'
    })
    
    posts = []
    if posts_status == 'ok' and posts_data and 'response' in posts_data:
        posts = posts_data['response'].get('items', [])
    
    logger.info(f"Загружено постов: {len(posts)}")
    
    post_score, post_reasons = GroupPostAnalyzer(vk).analyze(posts, group_info)
    
    # Собираем активность по ВСЕМ постам (без искусственного ограничения)
    posts_with_engagement = []
    for i, post in enumerate(posts):
        post_id = post.get('id')
        if not post_id:
            continue
        
        if i % 10 == 0:
            logger.info(f"Обработка поста {i+1}/{len(posts)}...")
        
        likes_data, _ = vk.get_post_likes(owner_id, post_id, count=1000)
        likes_count = len(likes_data.get('items', [])) if likes_data and 'response' in likes_data else 0
        
        comments = vk.get_post_comments_batch(owner_id, post_id, max_count=comments_limit)
        
        posts_with_engagement.append({
            'id': post_id,
            'text': post.get('text', ''),
            'date': post.get('date'),
            'likes': {'count': likes_count},
            'comments': comments
        })
        time.sleep(REQUEST_DELAY)
    
    logger.info(f"Постов с активностью: {len(posts_with_engagement)}")
    
    # Исправлено: ActivityAnalyzer вместо EngagementAnalyzer
    activity_score, activity_reasons, activity_findings = ActivityAnalyzer(vk).analyze(
        posts_with_engagement, 
        owner_id=owner_id
    )
    
    total_score = round(post_score * 0.6 + activity_score * 0.4)
    all_reasons = post_reasons + activity_reasons
    
    total_comments = sum(len(p.get('comments', [])) for p in posts_with_engagement)
    unique_commenters = len(set(
        c.get('from_id') 
        for p in posts_with_engagement 
        for c in p.get('comments', []) 
        if c.get('from_id') and c.get('from_id') > 0
    ))
    
    details = {
        "reasons": all_reasons,
        "posts_analyzed": len(posts),
        "engagement_posts": len(posts_with_engagement),
        "total_comments": total_comments,
        "unique_commenters": unique_commenters,
        "findings": activity_findings
    }
    
    logger.info(f"Анализ группы завершён. Скор: {total_score}, комментариев: {total_comments}, нарушений: {len(activity_findings)}")
    
    return {
        "type": "group",
        "group_id": group_id,
        "members_analyzed": 1,
        "average_score": total_score,
        "distribution": {f"{i}-{i+10}": 1 if i <= total_score < i+10 else 0 for i in range(0, 100, 10)},
        "scores": [total_score],
        "reasons": all_reasons,
        "posts_analyzed": len(posts),
        "engagement_posts": len(posts_with_engagement),
        "details": details
    }


__all__ = ['analyze_user', 'analyze_group']