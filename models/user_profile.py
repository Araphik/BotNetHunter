class UserProfile:
    def __init__(self, raw_data):
        self.id = raw_data.get('id')
        self.screen_name = raw_data.get('screen_name', '')
        self.first_name = raw_data.get('first_name', '')
        self.last_name = raw_data.get('last_name', '')
        self.city = self._extract_city(raw_data)
        self.country = self._extract_country(raw_data)
        self.sex = raw_data.get('sex', 0)
        self.bdate = raw_data.get('bdate', '')
        self.has_photo = bool(
            raw_data.get('photo_max_orig') or
            raw_data.get('photo_200') or
            raw_data.get('photo_max') or
            raw_data.get('photo_100')
        )
        self._photo_url = (
            raw_data.get('photo_max_orig') or
            raw_data.get('photo_200') or
            raw_data.get('photo_max') or
            raw_data.get('photo_100')
        )
        self.has_about = bool(raw_data.get('about'))
        self.has_interests = bool(raw_data.get('interests'))
        self.last_seen = raw_data.get('last_seen', {})
        self.counters = raw_data.get('counters', {})
        self.universities = raw_data.get('universities', [])
        self.career = raw_data.get('career', [])
        self.home_town = raw_data.get('home_town')
        self.relation = raw_data.get('relation')
        self.site = raw_data.get('site')
        self.verified = bool(raw_data.get('verified'))
        self.deactivated = raw_data.get('deactivated')
        self.friends_count = self.counters.get('friends', 0)
        self.followers_count = self.counters.get('followers', 0)

    def _extract_city(self, data):
        city = data.get('city', {})
        return city.get('title') if isinstance(city, dict) else None

    def _extract_country(self, data):
        country = data.get('country', {})
        return country.get('title') if isinstance(country, dict) else None

    def get_photo_url(self):
        return self._photo_url

    def get_friends_count(self):
        return self.friends_count

    def get_followers_count(self):
        return self.followers_count