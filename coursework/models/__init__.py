"""Academic bounded context models (app label `coursework`)."""
from .activities import ActivitySubmission
from .assessment import Assessment, CourseResult, MarkEntry, MarkEntryHistory, ResultEvent
from .attendance import AttendanceCorrection, AttendanceRecord, AttendanceSession, OrientationAttendance, OrientationSeminar
from .catalogue import AssessmentComponent, ComponentScore, Course, CourseAttempt, ExamAttempt
from .config import AcademicRuleParameter, ParameterChangeRequest, RuleStatus
from .electives import ElectiveProposal, ElectiveProposalEvent
from .electives_lists import ElectiveList, ElectiveListDecision, ElectiveListItem
from .exam_admin import (HallTicket, QuestionPaperSetterAppointment, RevaluationCase, RevaluationMark,
                         TranscriptIssue)
from .examination import Exam, ExamCycle, ExamEligibility, ExamRegistration, ThirdAttemptCase, ThirdAttemptCaseEvent
from .structure import AcademicYear, FeeClearance, Semester, SemesterRegistration
from .teaching import CourseOffering, FacultySubjectAssignment, ScholarCourseEnrollment, Section

__all__ = [
    "ElectiveList", "ElectiveListDecision", "ElectiveListItem", "FeeClearance", "HallTicket",
    "ParameterChangeRequest", "QuestionPaperSetterAppointment", "RevaluationCase", "RevaluationMark",
    "SemesterRegistration", "TranscriptIssue",
    "AcademicRuleParameter", "AcademicYear", "ActivitySubmission", "Assessment", "AssessmentComponent",
    "AttendanceCorrection", "AttendanceRecord", "AttendanceSession", "ComponentScore", "Course", "CourseAttempt",
    "CourseOffering", "CourseResult", "ElectiveProposal", "ElectiveProposalEvent", "Exam", "ExamAttempt",
    "ExamCycle", "ExamEligibility", "ExamRegistration", "FacultySubjectAssignment", "MarkEntry",
    "MarkEntryHistory", "OrientationAttendance", "OrientationSeminar", "ResultEvent", "RuleStatus",
    "ScholarCourseEnrollment", "Section", "Semester", "ThirdAttemptCase", "ThirdAttemptCaseEvent",
]
