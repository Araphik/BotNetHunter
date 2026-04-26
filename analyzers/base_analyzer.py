class BaseAnalyzer:
    def __init__(self, vk_client):
        self.vk = vk_client

    def analyze(self, profile):
        raise NotImplementedError

    def _safe_get(self, data, *keys, default=None):
        for key in keys:
            if isinstance(data, dict):
                data = data.get(key, default)
            else:
                return default
        return data if data is not None else default