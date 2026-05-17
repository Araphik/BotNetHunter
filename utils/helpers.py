import re
from urllib.parse import urlparse


def parse_user_input(user_input):
    user_input = (user_input or "").strip()
    user_input = user_input.replace("\u200b", "").replace("\ufeff", "")
    user_input = user_input.lstrip("@")

    parsed = urlparse(user_input if "://" in user_input else f"https://{user_input}")
    host = parsed.netloc.lower()
    if host in {
        "vk.com",
        "www.vk.com",
        "m.vk.com",
        "mobile.vk.com",
        "vk.ru",
        "www.vk.ru",
        "m.vk.ru",
        "mobile.vk.ru",
    }:
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            user_input = path_parts[0]

    user_input = user_input.strip().strip("/").lstrip("@")
    if user_input.lower().startswith('id') and user_input[2:].isdigit():
        user_input = user_input[2:]
    return user_input


def format_score(score, max_score=100):
    if score >= 70:
        return f'{score}/{max_score} — ВЫСОКИЙ риск'
    elif score >= 40:
        return f'{score}/{max_score} — СРЕДНИЙ риск'
    elif score >= 15:
        return f'{score}/{max_score} — НИЗКИЙ риск'
    else:
        return f'{score}/{max_score} — НОРМА'
