DEFAULT_MODULE_WEIGHTS = {
    "profile_analyzer": {
        "label": "Базовый анализ профиля",
        "global_weight": 1.0,
        "description": "Влияние проверки имени, аватарки, заполненности профиля",
        "parameters": {
            "empty_profile_penalty": {"value": 25, "label": "Штраф за пустой профиль (0/4)", "min": 5, "max": 50},
            "no_avatar_penalty": {"value": 18, "label": "Штраф за отсутствие аватарки", "min": 5, "max": 30},
            "new_account_penalty": {"value": 20, "label": "Штраф за очень новый аккаунт", "min": 5, "max": 30},
            "bot_keyword_penalty": {"value": 15, "label": "Штраф за бот-ключ в имени", "min": 5, "max": 25},
        }
    },
    "social_graph_analyzer": {
        "label": "Анализ социального графа",
        "global_weight": 1.2,
        "description": "Влияние географии друзей, концентрации в городах, близких ID",
        "parameters": {
            "geo_anomaly_penalty": {"value": 20, "label": "Штраф за гео-аномалию (<10% друзей из города)", "min": 5, "max": 40},
            "city_concentration_penalty": {"value": 15, "label": "Штраф за концентрацию в чужом городе", "min": 5, "max": 30},
            "close_ids_penalty": {"value": 18, "label": "Штраф за близкие ID друзей", "min": 5, "max": 30},
        }
    },
    "behavior_analyzer": {
        "label": "Поведенческий анализ",
        "global_weight": 0.9,
        "description": "Влияние паттернов постов, активности, регулярности публикаций",
        "parameters": {
            "repost_penalty": {"value": 15, "label": "Штраф за повторяющиеся посты", "min": 5, "max": 30},
            "link_spam_penalty": {"value": 10, "label": "Штраф за посты со ссылками", "min": 2, "max": 20},
            "regularity_penalty": {"value": 20, "label": "Штраф за слишком регулярные посты", "min": 5, "max": 30},
        }
    },
    "cross_check_analyzer": {
        "label": "Кросс-проверка данных",
        "global_weight": 1.1,
        "description": "Влияние логических противоречий между полями профиля",
        "parameters": {
            "completeness_mismatch_penalty": {"value": 15, "label": "Штраф за дисбаланс заполненности", "min": 5, "max": 30},
            "ratio_anomaly_penalty": {"value": 10, "label": "Штраф за аномальное соотношение подписчики/друзья", "min": 2, "max": 20},
            "age_mismatch_penalty": {"value": 12, "label": "Штраф за несоответствие возраста аккаунта", "min": 5, "max": 25},
        }
    },
}

# Глобальный лимит запросов в сутки для обычных пользователей
DEFAULT_REQUESTS_LIMIT = 100

# Админ-учётка
