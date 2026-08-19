from __future__ import annotations

JOBICY_RESPONSE = {
    "apiVersion": "2.2.15",
    "jobCount": 2,
    "jobs": [
        {
            "id": 1001,
            "url": "https://jobicy.com/jobs/1001-atlassian-consultant",
            "jobSlug": "1001-atlassian-consultant",
            "jobTitle": "Atlassian Consultant",
            "companyName": "Acme Tools",
            "jobIndustry": ["Project Management"],
            "jobType": ["Full-Time"],
            "jobGeo": "Anywhere",
            "jobLevel": "Senior",
            "jobExcerpt": "Remote consulting for Jira and Confluence workflows.",
            "jobDescription": (
                "<p>Own Jira administration, workflows, permissions, integrations and Confluence spaces.</p>"
            ),
            "annualSalaryMin": 70000,
            "annualSalaryMax": 90000,
            "salaryCurrency": "USD",
            "pubDate": "2026-07-30T19:45:05+00:00",
        },
        {
            "id": 1002,
            "url": "https://jobicy.com/jobs/1002-business-analyst",
            "jobSlug": "1002-business-analyst",
            "jobTitle": "Business Analyst",
            "companyName": "Beta",
            "jobIndustry": ["Business"],
            "jobType": ["Contract"],
            "jobGeo": "EMEA",
            "jobLevel": "Midweight",
            "jobDescription": "<p>Requirements gathering and user stories.</p>",
            "pubDate": "2026-07-29T10:00:00+00:00",
        },
    ],
    "success": True,
}

ATLASSIAN_CONSULTANT = JOBICY_RESPONSE["jobs"][0]
BUSINESS_ANALYST = JOBICY_RESPONSE["jobs"][1]
