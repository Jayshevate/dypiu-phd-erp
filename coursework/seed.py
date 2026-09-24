# Core (SIS7001-7007) = 14 credits; electives (3 cr each) make up the rest:
# B.Tech 14 + 3x3 = 23, M.Tech 14 + 1x3 = 17, PG 14 + 2x3 = 20.
COURSES = [
    ("SIS7001", "Research Methodology & Ethics", 4, "MANDATORY", True),
    ("SIS7002", "Statistics", 2, "MANDATORY", False),
    ("SIS7003", "Computational Thinking", 3, "MANDATORY", False),
    ("SIS7005", "Industrial Training", 2, "ACTIVITY", False),
    ("SIS7006", "Conference / Workshop", 1, "ACTIVITY", False),
    ("SIS7007", "Research Seminar", 2, "ACTIVITY", False),
]


def seed_courses() -> int:
    from .models import Course

    created = 0
    for code, title, credits, category, ethics in COURSES:
        _, is_new = Course.objects.get_or_create(code=code, defaults=dict(
            title=title, credits=credits, category=category, has_ethics_submodule=ethics, required_for_all=True))
        created += is_new
    return created
