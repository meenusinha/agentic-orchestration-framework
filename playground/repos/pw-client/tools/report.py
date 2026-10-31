"""Nightly report: share of weak passwords among active users."""
from ipc import connect


def weak_ratio(users):
    svc = connect("AuthService")
    scores = [svc.checkStrength(u.password_sample) for u in users]
    return sum(s.level == 0 for s in scores) / max(len(scores), 1)
