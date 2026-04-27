class AnalysisResult:
    def __init__(self, user_id):
        self.user_id = user_id
        self.total_score = 0
        self.max_score = 100
        self.risk_level = 'UNKNOWN'
        self.reasons = []
        self.anomalies = []
        self.profile_data = None

    def add_score(self, points, reason, priority='medium'):
        self.total_score = min(self.total_score + points, self.max_score)
        self.reasons.append({
            'reason': reason,
            'priority': priority,
            'points': points
        })

    def add_anomaly(self, description, category):
        self.anomalies.append({
            'description': description,
            'category': category
        })

    def calculate_risk(self):
        if self.total_score >= 70:
            self.risk_level = 'HIGH'
        elif self.total_score >= 40:
            self.risk_level = 'MEDIUM'
        elif self.total_score >= 15:
            self.risk_level = 'LOW'
        else:
            self.risk_level = 'NORMAL'
        return self.risk_level

    def to_dict(self):
        return {
            'user_id': self.user_id,
            'score': self.total_score,
            'max_score': self.max_score,
            'risk_level': self.risk_level,
            'reasons': self.reasons,
            'anomalies': self.anomalies
        }