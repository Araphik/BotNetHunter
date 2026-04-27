from app.models import VKToken


class TokenManager:
    _instance = None
    _db_session = None
    
    def __new__(cls, db_session=None):
        if cls._instance is None:
            cls._instance = super(TokenManager, cls).__new__(cls)
            cls._instance._db_session = db_session
        return cls._instance
    
    def __init__(self, db_session=None):
        if db_session is not None:
            self._db_session = db_session
        self.current_index = 0
    
    def _get_active_tokens(self):
        """Получить список активных токенов из БД"""
        if not self._db_session:
            from app.database import SessionLocal
            self._db_session = SessionLocal()
        
        tokens = self._db_session.query(VKToken).filter(
            VKToken.is_active == True
        ).all()
        return [t.token for t in tokens]
    
    def get_current_token(self):
        """Получить текущий активный токен"""
        tokens = self._get_active_tokens()
        if not tokens or self.current_index >= len(tokens):
            return None
        return tokens[self.current_index]
    
    def switch_token(self):
        """Переключиться на следующий токен"""
        tokens = self._get_active_tokens()
        if self.current_index < len(tokens) - 1:
            self.current_index += 1
            return True
        return False
    
    def is_exhausted(self):
        """Проверить, исчерпаны ли все токены"""
        tokens = self._get_active_tokens()
        return not tokens or self.current_index >= len(tokens)
    
    def get_tokens_count(self):
        """Получить количество активных токенов"""
        return len(self._get_active_tokens())
    
    def reset(self):
        """Сбросить индекс токена на начало"""
        self.current_index = 0