# Data Intel User Guide

Welcome to Center Deep Data Intel - your email verification and data intelligence service.

## What is Data Intel?

Data Intel helps you:
- **Verify email addresses** before sending campaigns
- **Catalogue company and contact data** from all your apps
- **Build a unified database** of verified leads

## Getting Started

### Access via Ops-Center

1. Log in to Ops-Center
2. Click "Apps" in the sidebar
3. Click "Data Intel" to launch

### Access Directly

Visit: https://verify.centerdeep.online

## Features

### Email Verification

Verify any email address to check:

| Check | Description |
|-------|-------------|
| **Syntax** | Is the email format valid? |
| **MX Records** | Does the domain have mail servers? |
| **SMTP** | Does the mailbox exist? |
| **Catch-All** | Does the domain accept all addresses? |
| **Disposable** | Is this a temporary email service? |
| **Role-Based** | Is this a generic address (info@, admin@)? |

#### Single Email Verification

1. Go to the **Verify** page
2. Enter an email address
3. Click **Verify**
4. View the results with risk score

#### Batch Verification

1. Go to the **Upload** page
2. Drag & drop a CSV file with an "email" column
3. Click **Verify All**
4. Download results when complete

### Data Catalogue

The catalogue stores:
- **Companies** - Name, domain, industry, location
- **Contacts** - Name, email, title, phone
- **Relationships** - Which contacts belong to which companies

#### Searching

1. Go to the **Search** page
2. Enter search terms (company name, domain, email)
3. Filter by industry, state, or source app
4. Click on results to see details

### Dashboard

The dashboard shows:
- Total companies and contacts in your database
- Email verification statistics
- Recent activity
- Quick verify widget

## Understanding Results

### Risk Scores

| Score | Level | Meaning |
|-------|-------|---------|
| 0-20 | Low | Safe to send |
| 21-50 | Medium | May have issues |
| 51-80 | High | Likely to bounce |
| 81-100 | Critical | Do not send |

### Status Labels

| Status | Description |
|--------|-------------|
| **Valid** | Email verified and deliverable |
| **Invalid** | Email does not exist |
| **Disposable** | Temporary email service |
| **Catch-All** | Domain accepts all addresses |
| **Role-Based** | Generic address (info@, support@) |
| **Unknown** | Could not verify (try again later) |

## Best Practices

### Before Email Campaigns

1. Upload your list to Data Intel
2. Remove emails with score > 50
3. Be cautious with catch-all domains
4. Avoid role-based addresses for personalized outreach

### Building Your Database

1. Connect your apps to Data Intel
2. All enriched data flows into the catalogue
3. Search across all sources from one place
4. Track which app provided each data point

## Tips

- **Verify before you send** - Bounced emails hurt your sender reputation
- **Check disposable emails** - These are often fake signups
- **Role-based caution** - info@ and admin@ often go to shared inboxes
- **Catch-all domains** - The email might not exist, but was accepted anyway

## Need Help?

- **API Documentation**: https://verify.centerdeep.online/docs
- **Admin Settings**: Ops-Center → Settings → Data Intel
- **Support**: Contact your Center Deep administrator
