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
from utils.helpers import parse_user_input
from utils.logger import logger

# Лок для безопасного обновления распределения в многопоточном режиме
distribution_lock = threading.Lock()


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
    """Комплексный анализ группы с параллельной обработкой"""
    vk = VKClient(token_manager)
    logger.info(f"Начало анализа группы {group_id}. Лимит: {max_members}")
    
    scores = []
    distribution = {f"{i}-{i+10}": 0 for i in range(0, 100, 10)}
    analyzed_count = 0
    
    # 1. Получаем ID участников
    all_member_ids = []
    offset = 0
    while len(all_member_ids) < max_members:
        members_data, status = vk.get_group_members(group_id, count=100, offset=offset)
        if status != 'ok' or not members_data or 'response' not in members_data:
            break
        member_ids = members_data['response']['items']
        if not member_ids:
            break
        all_member_ids.extend(member_ids)
        offset += len(member_ids)
        if len(member_ids) < 100:
            break
    
    if not all_member_ids:
        logger.warning(f"Не удалось получить участников группы {group_id}")
        return None
    
    all_member_ids = all_member_ids[:max_members]
    logger.info(f"Получено {len(all_member_ids)} участников для анализа")
    
    # 2. Загружаем профили пачками
    fields = 'screen_name,photo_200,photo_max,city,country,sex,bdate,last_seen,counters,about,interests'
    profiles = vk.get_users_batch(all_member_ids, fields)
    
    if not profiles:
        logger.error(f"Не удалось загрузить профили участников")
        return None
    
    logger.info(f"Загружено {len(profiles)} профилей, начинаем анализ...")
    
    # 3. Анализируем профили параллельно
    def analyze_worker(profile):
        try:
            return _analyze_single_profile_fast(profile, vk)
        except Exception as e:
            logger.warning(f"Ошибка анализа профиля {profile.id}: {e}")
            return None
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(analyze_worker, profile) for profile in profiles]
        for future in as_completed(futures):
            score = future.result()
            if score is not None:
                scores.append(score)
                with distribution_lock:
                    bin_idx = min(score // 10, 9)
                    distribution[f"{bin_idx*10}-{bin_idx*10+10}"] += 1
                analyzed_count += 1
                if analyzed_count % 20 == 0:
                    logger.info(f"Проанализировано {analyzed_count}/{len(profiles)}")
    
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    logger.info(f"Анализ группы завершён. Средний балл: {avg_score}, участников: {analyzed_count}")
    
    return {
        "type": "group",
        "group_id": group_id,
        "members_analyzed": analyzed_count,
        "average_score": avg_score,
        "distribution": distribution,
        "scores": scores
    }


__all__ = ['analyze_user', 'analyze_group']