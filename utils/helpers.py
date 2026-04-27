import re


def parse_user_input(user_input):
    user_input = user_input.strip()
    if 'vk.com/' in user_input:
        user_input = user_input.split('vk.com/')[-1].split('?')[0].split('/')[0]
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