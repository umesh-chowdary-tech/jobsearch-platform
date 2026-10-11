"""The inbox rules, pinned to the shapes a real job-search inbox contains.

Each fixture is modelled on a kind of email found in a read-only survey of one
inbox (two months, 880 messages), with every company, person and number made
up. Each one is a trap the rules fell into, or a shape they must keep reading
right.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jobsearch import mail_rules as R

ROOT = Path(__file__).resolve().parents[1]
ME = "kavya.example@example.com"

VANDELAY_FOOTER = (" This is a system generated mail, please do not reply. If you are approached by any "
                   "entity or individuals who demand money or any other form of compensation in return "
                   "for a job offer at Vandelay please send the details to our business ethics helpline.")


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    """The shared senders list, plus one employer domain to read a company from."""
    d = tmp_path_factory.mktemp("data")
    doc = yaml.safe_load((ROOT / "data" / "mail-senders.yaml").read_text(encoding="utf-8")) or {}
    doc["employers"] = {"globex.example": "Globex"}
    (d / "mail-senders.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(d)


# ── what happened ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sender, subject, body, kind", [
    # "other applicants", not "other candidates".
    ("talent@globex.example", "Globex Careers: Thank you for your time and interest",
     "Thank you for applying for the position of R-123456 Software Engineer - AI Engineer. Although we were "
     "impressed with your skills, we decided to move forward with other applicants who demonstrated a "
     "stronger alignment.", "rejected"),
    # Via Oracle's hiring platform.
    ("no-reply@workflow.mail.us2.cloud.oracle.com", "Your job application status (Job number: 210000001)",
     "Thank you for your interest in the Software Engineer II position at Initech. We received quite a "
     "few applications, and at this time we're sorry to let you know we're moving forward with other "
     "candidates.", "rejected"),
    # A rejection titled "Thank You for Applying" - the body decides.
    ("no-reply@ashbyhq.com", "Thank You for Applying to the Staff Software Engineer role at Hooli",
     "Thank you for taking the time to apply. After careful consideration, we've decided not to move forward "
     "with your application at this time.", "rejected"),
    ("umbrella@myworkday.com", "Update on your Umbrella Job Application for Full Stack Developer",
     "Dear KAVYA, After reviewing your information, we regret to inform you that your application for Full "
     "Stack Developer was not selected for further consideration.", "rejected"),
    # The role was cancelled - not a rejection of the person.
    ("vandelay@myworkday.com", "News on your Vandelay Interview",
     "Thanks for your interest in the role of AI / ML Engineer at Vandelay. However, we regret to inform "
     "that due to certain recent developments, we are no longer recruiting for this role." + VANDELAY_FOOTER,
     "cancelled"),
    ("noreply@hooli.example", "You're through to the next round - Hooli assignment inside",
     "Congratulations! You're through to the next round for Platform Engineer.", "interview"),
    ("hr@initech.example", "L1 Discussion - Platform Engineer - Initech", "Please share your availability.",
     "interview"),
    ("noreply@talent.globex.example", "Your Application with Globex for STAFF012345 - Staff AI & Machine Learning Engineer",
     "We have received your application.", "received"),
])
def test_real_outcomes_are_read_right(sender, subject, body, kind):
    assert R.classify(sender, subject, body).kind == kind


@pytest.mark.parametrize("subject, body, kind", [
    # An agency applied for the person. "next steps" alone made this an interview.
    ("Thank you for your interest in Vandelay",
     "Good news! EXAMPLE STAFFING our partner recruitment agency just submitted an application on your "
     "behalf to Vandelay. We'll be in touch with next steps." + VANDELAY_FOOTER, "received"),
    ("Next Steps for your Vandelay application",
     "Congratulations on reaching a new milestone (Online Assessment) in your recruitment journey with us! "
     "Please click here to begin your assessment." + VANDELAY_FOOTER, "interview"),
    ("Vandelay: Urgent Action Required - Assessment Acknowledgement",
     "Dear Candidate, Thank you for your interest with us. We'd like to move you to the next stage - please "
     "review the process below.", "interview"),
])
def test_an_agencys_steps_are_read_with_confidence(subject, body, kind):
    v = R.classify("vandelay@myworkday.com", subject, body)
    assert (v.kind, v.confidence) == (kind, "high")


def test_a_fraud_warning_footer_is_not_an_offer():
    """Unread, every one of 13 emails from one employer came out as an offer."""
    v = R.classify("vandelay@myworkday.com", "Time to schedule your interview with Vandelay!",
                   "Please schedule your interview for the AI / ML Engineer role." + VANDELAY_FOOTER)
    assert v.kind == "interview"


@pytest.mark.parametrize("sender, subject, body", [
    ("careers@email.careers.globex.example", "Thank you for your application!",
     "We've received your application. If you see the job moved to an inactive state, that means the "
     "position is no longer open, you withdrew, or you were not selected for the role."),
    ("initech@myworkday.com", "Thanks for your Application",
     "We will review your application in the next five to ten business days. If you're not selected to "
     "move forward, you will receive an email letting you know your application\nis no longer being "
     "considered for this position."),          # wrapped mid-sentence, as plain-text mail is
    ("no-reply@hooli.example", "Thank you for applying to Hooli",
     "We will review your application. If you are not selected for this position, please stay tuned to our "
     "careers page."),
])
def test_if_you_are_not_selected_is_a_receipt_not_a_rejection(sender, subject, body):
    assert R.classify(sender, subject, body).kind == "received"


def test_a_weak_word_never_outranks_a_strong_one():
    """"assessment" in a receipt's small print made eight receipts interviews."""
    v = R.classify("no-reply@hooli.example", "Application Received for Platform Engineer at Hooli",
                   "Thank you for applying. Some roles include an online assessment later in the process.")
    assert (v.kind, v.confidence) == ("received", "high")


def test_a_weak_signal_alone_is_low_confidence():
    v = R.classify("x@myworkday.com", "Next Steps for your Vandelay application",
                   "Please complete the online assessment by Friday.")
    assert (v.kind, v.confidence) == ("interview", "low")


@pytest.mark.parametrize("link, kind", [
    ("https://www.linkedin.com/help/?lipi=urn%3Ali%3Apage%3Aemail_email_jobs_application_rejected_01", "rejected"),
    ("https://www.linkedin.com/?trk=eml-email_jobs_application_sent_01-footer", "received"),
])
def test_linkedin_is_read_from_its_links(link, kind):
    """The body said only "Your update from <company>"; the link named the event."""
    v = R.classify("jobs-noreply@linkedin.com", "Your application to Backend Engineer at Globex",
                   f"Your update from Globex\nLearn why we included this: {link}")
    assert v.kind == kind


# ── Naukri: counted, never ledgered ──────────────────────────────────────────

NAUKRI_APPLIED = ("Your applications were sent, Good luck!\nApplied on September 23, 2026\nFullstack Developer\n"
                  "Globex\nTrack applications\nSimilar jobs for you\nSoftware Developer\nInitech\n3.4\n"
                  "Software Engineer\nHooli\nBack End Engineer\nUmbrella")


def test_a_naukri_digest_is_a_count_and_one_example():
    """"You applied for 19 jobs" lists one of them; what follows is
    recommendations, and must never become applications."""
    v = R.classify("info@naukri.com", "You applied for 19 jobs on 23 Sep", NAUKRI_APPLIED)
    assert v.kind == "naukri_applied"
    assert v.extra == {"count": 19, "day": "23 Sep", "example": "Fullstack Developer · Globex"}
    assert "Initech" not in str(v.extra) and "Hooli" not in str(v.extra)


def test_neo_counts_are_read_not_assumed():
    """Neo's "applied on your behalf" was 0 in every email surveyed."""
    body = ("Jobs auto-applied | Neo applied on your behalf | 0 | Jobs applied with assistance | "
            "You applied with Neo's help | 19 | Recruiter activity | Recruiters acted on your applications | 3")
    v = R.classify("info@naukri.com", "Your daily update: Jobs found & applied by Neo", body)
    assert v.extra == {"auto_applied": 0, "assisted": 19, "recruiter_actions": 3}


def test_naukri_status_change_says_nothing_more():
    v = R.classify("info@naukri.com", "Status of your job application has changed",
                   "The status of your job application on Naukri.com has been updated")
    assert v.kind == "naukri_status"


# ── what is worth reading at all ─────────────────────────────────────────────

@pytest.mark.parametrize("sender, subject, keep", [
    ("alerts@hirist.tech", "Senior Python roles for you", False),              # a feed
    ("no-reply@workflow.mail.us2.cloud.oracle.com", "Take action to confirm your identity", False),
    ("x@turbohire.co", "6-digit Code for your application to Globex", False),
    ("careers@initech.example", "New job opportunities at Initech", False),
    ("jobs-noreply@linkedin.com", "Software Engineer role at Hooli", False),    # a LinkedIn alert
    ("jobs-noreply@linkedin.com", "Kavya Example, your application was sent to Umbrella", True),
    ("info@naukri.com", "You applied for 4 jobs on 21 Sep", True),
    ("info@naukri.com", "Kavya, Recruiters are noticing your profile", False),
    ("talent@globex.example", "Globex Careers: Thank you for applying", True),
    # Not submitted without a profile on the marketplace; no employer named.
    ("sam@connect.hackajob.com", "Your Fullstack Engineer application is almost there, Kavya", False),
    # A marketplace's matches - it read as an interview on "next steps".
    ("alex@hackajob.com", "Congratulations on your first matches: 53 roles awaiting your response", False),
    ("x@hackajob.com", "You have 12 new roles matching your profile", False),
    (ME, "Re: L1 Discussion - Platform Engineer - Initech", False),            # the person's own sent mail
])
def test_which_emails_are_read(sender, subject, keep, data):
    assert R.is_candidate(sender, subject, data, me=ME) is keep


# ── which application ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, rid", [
    ("Thank you for applying for the position of R-123456 Software Engineer - AI Engineer.", "R-123456"),
    ("Your job application status (Job number: 210000001)", "210000001"),
    ("in the position of Software Engineer - II, 200012345 at Globex.", "200012345"),
    ("Your Application with Globex for STAFF012345 - Staff AI & Machine Learning Engineer", "STAFF012345"),
    ("Thank you for your interest in Initech || Senior Backend Engineer", ""),
])
def test_requisition_ids(text, rid):
    assert R.req_id(text) == rid


@pytest.mark.parametrize("sender, name, subject, company, source", [
    ("talent@globex.example", "Globex Talent", "Globex Careers: Thank you for applying", "Globex", "sender"),
    # Oracle's platform, not Oracle: the most specific listing wins.
    ("no-reply@workflow.mail.us2.cloud.oracle.com", "Initech Corp. Human Resources",
     "We received your job application (Job Number: 210000002)", "Initech Corp.", "org"),
    ("hooli@successfactors.eu", "Hooli Recruiting Team",
     "Thank you for your application to AI Engineer at Hooli", "Hooli", "subject"),
    ("no-reply@us.greenhouse-mail.io", "", "Thank you for applying to Vandelay Labs", "Vandelay Labs", "subject"),
    ("umbrella@myworkday.com", "Umbrella People Services", "Thank you for your application!", "Umbrella", "org"),
    # A bare name has no organisation words: a person, or a guess - flagged.
    ("sam@connect.hackajob.com", "Sam", "Your Fullstack Engineer application is almost there", "Sam", "display"),
    # The platform is not the employer.
    ("noreply@berribot.com", "Berribot", "AI Interview with InterviewBot(On Behalf of Globex)", "Globex", "subject"),
])
def test_company_names(sender, name, subject, company, source, data):
    assert R.company(sender, name, subject, data) == (company, source)


@pytest.mark.parametrize("sender, name, subject, body, company, source", [
    # A Workday tenant; only the body says who.
    ("gbx@myworkday.com", "", "Thanks for your Application",
     "Dear Kavya, Thank you for your interest in a career with Globex and taking the time to complete an "
     "application for our Software Engineer opening.", "Globex", "body"),
    ("no-reply@us.greenhouse-mail.io", "", "Kavya, thanks for wanting to become an Initechie!",
     "Thank you for applying to Initech! We are thrilled that you are interested in the Senior Engineer, IAM "
     "position!", "Initech", "body"),
    # A job, not an employer.
    ("no-reply@us.greenhouse-mail.io", "", "Thanks!", "Thank you for applying to Senior Engineer.", "", ""),
    # An organisation's own name comes before the body's legal name...
    ("no-reply@us.greenhouse-mail.io", "Hooli Technologies Hiring Team", "Thanks!",
     "Thank you for applying to Hooli Technologies Pvt. Ltd.", "Hooli Technologies", "org"),
    # ...and a body-only name loses the legal suffix.
    ("no-reply@us.greenhouse-mail.io", "", "Thanks!", "Thank you for applying to Acme Solutions Pvt. Ltd.",
     "Acme Solutions", "body"),
    # The Workday tenant is the company naming itself...
    ("vandelay@myworkday.com", "Vandelay", "Thank you for applying!",
     "Thank you for applying to the Senior Staff Software Engineer (SDET).", "Vandelay", "tenant"),
    # ...which a person at a platform is not.
    ("sam@connect.hackajob.com", "Sam", "Next steps", "Thanks for applying to the Fullstack Engineer role on "
     "Naukri.", "Sam", "display"),
])
def test_company_names_from_the_body_and_the_tenant(sender, name, subject, body, company, source, data):
    assert R.company(sender, name, subject, data, body) == (company, source)


@pytest.mark.parametrize("subject, body, role", [
    ("Update on your Umbrella Job Application for Software Engineer - Full\n Stack Development", "",
     "Software Engineer - Full Stack Development"),                 # a folded header
    ("Thank you for your interest in Initech || Senior Backend Engineer", "", "Senior Backend Engineer"),
    ("Your Interview has been successfully Scheduled.",
     "Your interview for AI / ML Engineer has been successfully scheduled.", "AI / ML Engineer"),
    ("Thank you for your interest in Hooli!",
     "Thank you for applying for the Platform Analyst, Bangalore position at Hooli!",
     "Platform Analyst, Bangalore"),                               # a comma inside the role
    ("Globex Careers: Thank you for your time and interest",
     "Thank you for applying for the position of R-123456 Software Engineer - AI Engineer. We appreciate it.",
     "Software Engineer - AI Engineer"),
    ("Thanks for your Application",
     "Thank you for your interest in a career with Globex and taking the time to complete an application "
     "for our Software Engineer opening.", "Software Engineer"),
    ("Thank you for applying!", "Dear Kavya, Thank you for applying to the Senior Staff Software Engineer "
     "(SDET). We appreciate your interest.", "Senior Staff Software Engineer (SDET)"),
])
def test_role_names(subject, body, role):
    assert R.role(subject, body) == role


# ── mail someone signed up for ──────────────────────────────────────────────

MARKETPLACE = ("Hi Kavya, I'm Alex, VP of Marketplace at hackajob. Congratulations, you have your first "
               "matches. You have 53 roles matching your profile, all with Globex awaiting your response. "
               "Next steps: View your matches here. Apply to the roles. "
               "You're receiving this because you opted in to job updates from hackajob. "
               "Manage your subscription preferences")


def test_a_subscription_footer_is_marketing_not_an_interview(data):
    """Its only signal was the weak "next steps"; the footer says what it is."""
    v = R.classify("alex@hackajob.com", "Your update", MARKETPLACE, data)
    assert v.kind == "marketing" and "opted in to job updates" in v.evidence


def test_a_strong_outcome_beats_a_subscription_footer(data):
    body = "We regret to inform you that we will not be moving forward. Manage your email preferences here."
    assert R.classify("x@example.com", "Your application", body, data).kind == "rejected"
    body = "Thank you for applying to Acme. You are receiving this email because you signed up for alerts."
    assert R.classify("x@example.com", "Acme", body, data).kind == "received"


def test_marketing_is_never_recorded():
    from jobsearch.applications import LEDGER_KINDS
    assert "marketing" not in LEDGER_KINDS


def test_the_shared_list_names_no_ones_employers():
    """Which companies a person applied to is theirs: the shared list starts
    empty, and approved senders live in each profile's mail-learned.yaml."""
    doc = yaml.safe_load((ROOT / "data" / "mail-senders.yaml").read_text(encoding="utf-8"))
    assert doc["employers"] == {}
