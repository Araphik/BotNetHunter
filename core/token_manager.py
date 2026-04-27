import os
from config.settings import VK_TOKENS


class TokenManager:
    def __init__(self, tokens=None):
        self.tokens = tokens if tokens is not None else VK_TOKENS
        self.current_index = 0

    def get_current_token(self):
        if not self.tokens or self.current_index >= len(self.tokens):
            return None
        return self.tokens[self.current_index]

    def switch_token(self):
        if self.current_index < len(self.tokens) - 1:
            self.current_index += 1
            return True
        return False

    def is_exhausted(self):
        return not self.tokens or self.current_index >= len(self.tokens)

    def get_tokens_count(self):
        return len(self.tokens)
