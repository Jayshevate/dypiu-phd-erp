"""Academic REST API: /api/academic/..."""
from django.urls import path

from imports import api as import_api

from . import approvals, offices, scholar, setup, teaching

app_name = "academic_api"

urlpatterns = [
    # reference data
    path("catalogue/", offices.CatalogueView.as_view()),
    path("rules/", offices.RulesView.as_view()),
    path("rules/changes/", offices.ProposeChangeView.as_view()),
    path("rules/changes/<int:pk>/decide/", offices.DecideChangeView.as_view()),

    # scholar workspace (own record only: no scholar id in the URL)
    path("me/dashboard/", scholar.DashboardView.as_view()),
    path("me/semester-registration/", scholar.SemesterRegistrationView.as_view()),
    path("me/courses/", scholar.CoursesView.as_view()),
    path("me/offerings/", offices.AvailableOfferingsView.as_view()),
    path("me/enrollments/", offices.EnrollView.as_view()),
    path("me/attendance/", scholar.AttendanceView.as_view()),
    path("me/results/", scholar.ResultsView.as_view()),
    path("me/exams/", scholar.ExamsView.as_view()),
    path("me/exam-registrations/", scholar.ExamRegistrationView.as_view()),
    path("me/electives/", scholar.ElectivesView.as_view()),
    path("me/activities/", scholar.ActivitiesView.as_view()),
    path("me/third-attempt/", scholar.ThirdAttemptView.as_view()),
    path("me/transcript/", scholar.TranscriptView.as_view()),
    path("me/revaluations/", scholar.RevaluationView.as_view()),
    path("hall-tickets/<int:pk>/", scholar.HallTicketView.as_view()),

    # faculty / course coordinator
    path("teaching/assignments/", teaching.AssignmentsView.as_view()),
    path("offerings/<int:pk>/", teaching.OfferingView.as_view()),
    path("offerings/<int:pk>/roster/", teaching.RosterView.as_view()),
    path("offerings/<int:pk>/marks/", teaching.MarksView.as_view()),
    path("offerings/<int:pk>/results/", teaching.OfferingResultsView.as_view()),
    path("sections/<int:pk>/sessions/", teaching.SessionsView.as_view()),
    path("sessions/<int:pk>/records/", teaching.SessionRecordsView.as_view()),
    path("marks/", teaching.MarkEntryView.as_view()),
    path("attempts/<int:pk>/prepare-result/", teaching.PrepareResultView.as_view()),

    # SDRC
    path("sdrc/activities/", teaching.SdrcActivitiesView.as_view()),
    path("activity-submissions/<int:pk>/review/", teaching.ReviewSubmissionView.as_view()),
    path("activity-submissions/<int:pk>/document/", teaching.SubmissionDocumentView.as_view()),
    path("enrollments/<int:pk>/open-evaluation/", teaching.OpenEvaluationView.as_view()),

    # result chain (R&D Cell verify, COE ratify)
    path("results/", offices.ResultQueueView.as_view()),
    path("results/<int:pk>/", offices.ResultDetailView.as_view()),
    path("results/<int:pk>/verify/", offices.ResultTransitionView.as_view(transition="verify")),
    path("results/<int:pk>/return/", offices.ResultTransitionView.as_view(transition="return")),
    path("results/<int:pk>/ratify/", offices.ResultTransitionView.as_view(transition="ratify")),

    # scholars (staff views, authorized per scholar)
    path("scholars/", offices.ScholarListView.as_view()),
    path("scholars/<int:pk>/record/", offices.ScholarRecordView.as_view()),
    path("scholars/<int:pk>/fee-clearance/", offices.FeeClearanceView.as_view()),
    path("scholars/<int:pk>/semester-registration/", offices.ManageSemesterRegistrationView.as_view()),
    path("scholars/<int:pk>/enrollments/", offices.EnrollView.as_view()),
    path("scholars/<int:pk>/transcript/", offices.ScholarTranscriptView.as_view()),
    path("scholars/<int:pk>/transcript/issue/", offices.IssueTranscriptView.as_view()),
    path("transcripts/verify/<str:code>/", offices.VerifyTranscriptView.as_view()),

    # examinations (COE)
    path("exam-cycles/", offices.ExamCyclesView.as_view()),
    path("exams/", offices.ExamsCreateView.as_view()),
    path("exams/<int:pk>/registrations/", offices.ExamRegistrationsView.as_view()),
    path("exam-registrations/<int:pk>/hall-ticket/", offices.IssueHallTicketView.as_view()),
    path("hall-tickets/<int:pk>/revoke/", offices.RevokeHallTicketView.as_view()),

    # structure (Academic Admin / CISR / department & school admins for assignments)
    path("governance/offerings/", offices.GovernanceOfferingsView.as_view()),
    path("governance/offerings/<int:pk>/sections/", offices.SectionCreateView.as_view()),
    path("governance/offerings/<int:pk>/assessments/", offices.DefineAssessmentsView.as_view()),
    path("governance/offerings/<int:pk>/assignments/", offices.AssignFacultyView.as_view()),
    path("governance/assignments/<int:pk>/revoke/", offices.RevokeAssignmentView.as_view()),
    path("governance/faculty/", offices.FacultyDirectoryView.as_view()),

    # institutional setup and provisioning (Step A2)
    path("setup/status/", setup.StatusView.as_view()),
    path("setup/universities/", setup.UniversitiesView.as_view()),
    path("setup/schools/", setup.SchoolsView.as_view()),
    path("setup/departments/", setup.DepartmentsView.as_view()),
    path("setup/academic-years/", setup.AcademicYearsView.as_view()),
    path("setup/semesters/", setup.SemestersView.as_view()),
    path("setup/courses/", setup.CoursesView.as_view()),
    path("setup/faculty/", setup.FacultyRecordsView.as_view()),
    path("setup/scholars/", setup.ScholarRecordsView.as_view()),
    path("setup/people/provision/", setup.ProvisionView.as_view()),
    path("setup/people/<int:pk>/activation/", setup.ActivationView.as_view()),

    # institutional data import (Step A3)
    path("imports/", import_api.BatchListView.as_view()),
    path("imports/types/", import_api.TypesView.as_view()),
    path("imports/templates/<str:key>/", import_api.TemplateView.as_view()),
    path("imports/analyze/", import_api.AnalyzeView.as_view()),
    path("imports/preview/", import_api.PreviewView.as_view()),
    path("imports/<int:pk>/", import_api.BatchView.as_view()),
    path("imports/<int:pk>/commit/", import_api.CommitView.as_view()),
    path("imports/<int:pk>/activation-links/", import_api.ActivationLinksView.as_view()),

    # approvals
    path("third-attempt-cases/", approvals.ThirdAttemptCasesView.as_view()),
    path("third-attempt-cases/<int:pk>/review/", approvals.ThirdAttemptReviewView.as_view()),
    path("third-attempt-cases/<int:pk>/decide/", approvals.ThirdAttemptDecideView.as_view()),
    path("elective-proposals/", approvals.ElectiveProposalsView.as_view()),
    path("elective-proposals/<int:pk>/recommend/", approvals.ElectiveRecommendView.as_view()),
    path("elective-proposals/<int:pk>/decide/", approvals.ElectiveDecideView.as_view()),
    path("elective-lists/", approvals.ElectiveListsView.as_view()),
    path("elective-lists/<int:pk>/submit/", approvals.ElectiveListSubmitView.as_view()),
    path("elective-lists/<int:pk>/decide/", approvals.ElectiveListDecideView.as_view()),
    path("question-papers/", approvals.QuestionPapersView.as_view()),
    path("exams/<int:pk>/setters/", approvals.AppointSetterView.as_view()),
    path("setter-appointments/<int:pk>/received/", approvals.ReceivePaperView.as_view()),
]
