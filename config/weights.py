DEFAULT_MODULE_WEIGHTS = {
    "profile_analyzer": {
        "label": "Базовый анализ профиля",
        "global_weight": 1.0,
        "description": "Влияние проверки имени, аватарки, заполненности профиля",
        "parameters": {
            "empty_profile_penalty": {"value": 25, "label": "Штраф за пустой профиль (0/4)", "min": 5, "max": 50, "step": 1, "description": ""},
            "no_avatar_penalty": {"value": 18, "label": "Штраф за отсутствие аватарки", "min": 5, "max": 30, "step": 1, "description": ""},
            "new_account_penalty": {"value": 20, "label": "Штраф за очень новый аккаунт", "min": 5, "max": 30, "step": 1, "description": ""},
            "bot_keyword_penalty": {"value": 15, "label": "Штраф за бот-ключ в имени", "min": 5, "max": 25, "step": 1, "description": ""},
        }
    },
    "social_graph_analyzer": {
        "label": "Анализ социального графа",
        "global_weight": 1.2,
        "description": "Влияние географии друзей, концентрации в городах, близких ID",
        "parameters": {
            "geo_anomaly_penalty": {"value": 20, "label": "Штраф за гео-аномалию (<10% друзей из города)", "min": 5, "max": 40, "step": 1, "description": ""},
            "city_concentration_penalty": {"value": 15, "label": "Штраф за концентрацию в чужом городе", "min": 5, "max": 30, "step": 1, "description": ""},
            "close_ids_penalty": {"value": 18, "label": "Штраф за близкие ID друзей", "min": 5, "max": 30, "step": 1, "description": ""},
        }
    },
    "behavior_analyzer": {
        "label": "Поведенческий анализ",
        "global_weight": 0.9,
        "description": "Влияние паттернов постов, активности, регулярности публикаций",
        "parameters": {
            "repost_penalty": {"value": 15, "label": "Штраф за повторяющиеся посты", "min": 5, "max": 30, "step": 1, "description": ""},
            "link_spam_penalty": {"value": 10, "label": "Штраф за посты со ссылками", "min": 2, "max": 20, "step": 1, "description": ""},
            "regularity_penalty": {"value": 20, "label": "Штраф за слишком регулярные посты", "min": 5, "max": 30, "step": 1, "description": ""},
        }
    },
    "cross_check_analyzer": {
        "label": "Кросс-проверка данных",
        "global_weight": 1.1,
        "description": "Влияние логических противоречий между полями профиля",
        "parameters": {
            "completeness_mismatch_penalty": {"value": 15, "label": "Штраф за дисбаланс заполненности", "min": 5, "max": 30, "step": 1, "description": ""},
            "ratio_anomaly_penalty": {"value": 10, "label": "Штраф за аномальное соотношение подписчики/друзья", "min": 2, "max": 20, "step": 1, "description": ""},
            "age_mismatch_penalty": {"value": 12, "label": "Штраф за несоответствие возраста аккаунта", "min": 5, "max": 25, "step": 1, "description": ""},
        }
    },
    "group_post_analyzer": {
        "label": "Анализ постов группы",
        "global_weight": 1.0,
        "description": "Настройки загрузки и анализа контента группы",
        "parameters": {
            "posts_limit": {"value": 100, "label": "Макс. постов для анализа", "min": 10, "max": 200, "step": 10, "description": "Сколько последних постов загружать со стены"},
            "comments_limit": {"value": 10000, "label": "Макс. комментариев на пост", "min": 100, "max": 10000, "step": 100, "description": "Лимит комментариев для одного поста"},
            "no_posts": {"value": 15, "label": "Штраф за отсутствие постов", "min": 5, "max": 30, "step": 1, "description": ""},
            "high_frequency": {"value": 12, "label": "Штраф за высокую частоту постов", "min": 5, "max": 25, "step": 1, "description": ""},
            "repetitive_content": {"value": 18, "label": "Штраф за повторяющийся контент", "min": 5, "max": 30, "step": 1, "description": ""},
            "link_spam": {"value": 10, "label": "Штраф за посты со ссылками", "min": 2, "max": 20, "step": 1, "description": ""},
            "night_posting": {"value": 10, "label": "Штраф за ночные публикации", "min": 2, "max": 20, "step": 1, "description": ""},
            "caps_spam": {"value": 8, "label": "Штраф за CAPS/эмодзи-спам", "min": 2, "max": 15, "step": 1, "description": ""},
        }
    },
    "engagement_analyzer": {
        "label": "Анализ активности под постами",
        "global_weight": 0.9,
        "description": "Влияние шаблонных комментариев, быстрой активности, рекламы",
        "parameters": {
            "generic_comments_high": {"value": 20, "label": "Штраф за >70% шаблонных комментариев", "min": 5, "max": 30, "step": 1, "description": ""},
            "generic_comments_med": {"value": 10, "label": "Штраф за >40% шаблонных комментариев", "min": 2, "max": 20, "step": 1, "description": ""},
            "rapid_comments": {"value": 15, "label": "Штраф за быструю серию комментариев", "min": 5, "max": 25, "step": 1, "description": ""},
            "repetitive_user_comments": {"value": 12, "label": "Штраф за повторяющиеся комментарии пользователя", "min": 3, "max": 20, "step": 1, "description": ""},
            "promo_comments": {"value": 10, "label": "Штраф за рекламные фразы в комментариях", "min": 2, "max": 20, "step": 1, "description": ""},
            "low_engagement": {"value": 8, "label": "Штраф за низкую вовлеченность", "min": 2, "max": 15, "step": 1, "description": ""},
            "new_account_activity": {"value": 10, "label": "...", "min": 2, "max": 20, "step": 1, "description": ""},
            "coordinated_activity": {"value": 15, "label": "...", "min": 5, "max": 30, "step": 1, "description": ""},
        }
    },
}

DEFAULT_REQUESTS_LIMIT = 100