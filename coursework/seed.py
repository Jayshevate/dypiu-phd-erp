# Core (SIS7001-7007) = 14 credits; electives (3 cr each) make up the rest:
# B.Tech 14 + 3x3 = 23, M.Tech 14 + 1x3 = 17, PG 14 + 2x3 = 20.
# 3 mandatory taught courses + 3 mandatory activity components (Step 5A source).
COURSES = [
    ("SIS7001", "Research Methodology & Ethics", 4, "MANDATORY", True, ""),
    ("SIS7002", "Statistics", 2, "MANDATORY", False, ""),
    ("SIS7003", "Computational Thinking", 3, "MANDATORY", False, ""),
    ("SIS7005", "Industrial Training / Field Work", 2, "ACTIVITY", False, "INDUSTRIAL_TRAINING"),
    ("SIS7006", "Conference / Workshop", 1, "ACTIVITY", False, "CONFERENCE_WORKSHOP"),
    ("SIS7007", "Research Seminar Presentation", 2, "ACTIVITY", False, "RESEARCH_SEMINAR"),
]


def seed_courses() -> int:
    from .models import Course

    created = 0
    for code, title, credits, category, ethics, activity in COURSES:
        _, is_new = Course.objects.get_or_create(code=code, defaults=dict(
            title=title, credits=credits, category=category, has_ethics_submodule=ethics, required_for_all=True,
            activity_type=activity, evaluation_body="SDRC" if category == "ACTIVITY" else "COURSE_COORDINATOR"))
        created += is_new
    return created
