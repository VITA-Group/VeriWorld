# Google Form Template — VeriWorld Access Request

> **Internal document.** Copy each section into a Google Form owned by `axisworld.team@gmail.com`. Do NOT commit this file to a public-facing path that implies it is the Form itself — the live Form is at the URL you paste into `docs/ACCESS.md`.

## Form Settings

- **Owner account**: `axisworld.team@gmail.com`
- **Collect email addresses**: Verified (requires Google sign-in)
- **Limit to 1 response**: OFF (users may resubmit after revision)
- **Response receipts**: Always
- **Notifications**: Turn on "Email me for each new response" (Sheet → Tools → Notification rules). Goes to `axisworld.team@gmail.com` inbox.
- **Linked Sheet**: Create a Google Sheet called `VeriWorld Access Requests`. Add a `Status` column (values: `pending` / `approved` / `rejected` / `revoked` / `follow-up`) and a `Granted At` column for the approval timestamp.
- **Confirmation message**: "Thank you. Your request has been received. You will receive a response within 5 business days at the email address you provided. If you do not hear back within 7 business days, email axisworld.team@gmail.com."

---

## Form Title

**VeriWorld — Request Access to the Packaged Unreal Engine Build**

## Form Description (shown at top)

```
Use this form to request access to the access-gated Windows Unreal Engine build required to run the VeriWorld tasks.

Before submitting, please read:
- LICENSE: <link to LICENSE file on GitHub>
- NOTICE: <link to NOTICE.md file on GitHub>

Submitting this form constitutes acceptance of the terms in the LICENSE and NOTICE. Access is personal and non-transferable. Access may be revoked at any time if terms are violated.

For operational questions about this form or the build: axisworld.team@gmail.com
For licensing or legal questions: yan.zheng.mat@gmail.com
```

---

## Section 1 — Applicant Information

### Q1. Full name
- **Type**: Short answer
- **Required**: Yes

### Q2. Google account email (this is where access will be granted)
- **Type**: Short answer
- **Required**: Yes
- **Response validation**: Regex `.+@.+\..+` (basic email)
- **Validation message**: "Please enter a valid email address. Access will be shared with this exact address via Google Drive."

### Q3. Affiliation (institution, lab, or independent)
- **Type**: Short answer
- **Required**: Yes

### Q4. Role
- **Type**: Multiple choice
- **Required**: Yes
- **Options**:
  - Undergraduate student
  - Graduate student (Masters / PhD)
  - Postdoctoral researcher
  - Faculty / Principal investigator
  - Research scientist / engineer (academic)
  - Research scientist / engineer (industry research lab)
  - Independent researcher
  - Other (please specify)

### Q5. If you selected "Industry research lab" or "Other": please specify team / company / description
- **Type**: Short answer
- **Required**: No

### Q6. Country / region of primary residence or institution
- **Type**: Short answer
- **Required**: Yes

---

## Section 2 — Intended Use

### Q7. What do you plan to do with VeriWorld?
- **Type**: Paragraph
- **Required**: Yes
- **Description**: "Briefly describe your research question, which models you plan to evaluate, and your anticipated publication or dissemination venue (e.g., specific conference, course project, internal exploration, etc.). 2–5 sentences."

### Q8. Expected usage duration
- **Type**: Multiple choice
- **Required**: Yes
- **Options**:
  - Less than 1 month
  - 1–3 months
  - 3–6 months
  - 6–12 months
  - More than 12 months

### Q9. How did you hear about VeriWorld?
- **Type**: Multiple choice (Other allowed)
- **Required**: No
- **Options**:
  - Paper / preprint
  - Conference / workshop
  - GitHub
  - Social media
  - Colleague / advisor recommendation
  - Other

---

## Section 3 — Terms Acknowledgment

### Acknowledgment block (Form description for Section 3)

```
Please review the LICENSE and NOTICE files in the VeriWorld repository before continuing. The checkboxes below correspond to specific terms. You must check all of them to submit. Each checkbox represents a separate acknowledgment.

LICENSE: <link>
NOTICE: <link>
```

### Q10. Terms acceptance (checkboxes, all required)
- **Type**: Checkboxes
- **Required**: Yes (and mark "Response validation" → "Require at least 8 selections" so all must be checked; alternatively split into 8 separate required yes/no questions, which is more legally robust — see note below)
- **Options**:
  - I have read the LICENSE (PolyForm Noncommercial License 1.0.0) in full and agree to be bound by its terms.
  - I have read the NOTICE in full and agree to be bound by its terms, including specifically Sections 2.1, 3, and 4.
  - I understand that access granted to me is personal and non-transferable, and that I may not share, redistribute, mirror, re-host, or transfer the packaged build to any third party.
  - I understand that I may not reverse engineer, decompile, disassemble, or extract assets from the packaged build, and that I may not circumvent any access or protection measure.
  - I understand that I may not use the software or any output derived from it as training data, fine-tuning data, distillation target, reinforcement signal, or any other input to the training or improvement of any commercial model, product, or service.
  - I understand that I may not incorporate the software into any commercial product, paid service, or proprietary offering, and that I may not republish it as another benchmark, dataset, or evaluation suite.
  - I acknowledge that any academic publication, preprint, or public-facing work that uses, extends, or is substantially informed by VeriWorld must cite **both** VeriWorld (per `CITATION.cff`) **and** VoxelCodeBench (Zheng & Bordes, 2026 — `arXiv:2604.02580`), and must explicitly acknowledge VeriWorld in the body of the work. Citation in the reference list alone is not sufficient.
  - I acknowledge that submitting this form constitutes acceptance of the LICENSE and NOTICE, regardless of whether I have reviewed them in detail, and that the licensor may pursue legal action for violations under applicable copyright, trade-secret, and anti-circumvention law.

**Note on legal robustness**: Google Forms' checkbox-question "require all selected" is a soft validation. For stronger click-wrap evidence, convert each of the eight items above into its own Yes/No multiple-choice question where "Yes" is required and the question is marked required. This produces an individually timestamped acknowledgment per term. Trade-off: longer form. For most academic-benchmark use cases the single-checkbox-question form is sufficient; for higher-stakes deployments prefer the split form.

---

## Section 4 — Optional

### Q11. Preferred build platform
- **Type**: Multiple choice
- **Required**: No
- **Options**:
  - Windows 64-bit (available)
  - Linux (not currently distributed — noted for demand tracking)
  - macOS (not currently distributed — noted for demand tracking)

### Q12. Anything else you'd like us to know
- **Type**: Paragraph
- **Required**: No

---

## Post-Submission Workflow (operational)

1. Form submission lands in the linked Google Sheet. Notification email to `axisworld.team@gmail.com`.
2. Reviewer opens Sheet, skims Q7 (intended use) and Q4 (role). Looks for:
   - Obvious commercial-product intent (reject or request clarification)
   - Spam / nonsense (reject)
   - Clear academic / research intent (approve)
3. Set `Status` column to `approved` / `rejected` / `follow-up`.
4. For approvals: share the GDrive folder with the email from Q2 (Viewer access, do not allow reshare). Set `Granted At` timestamp.
5. Send approval email from `axisworld.team@gmail.com` with:
   - GDrive link
   - Reminder of key terms (no redistribution, no reverse engineering)
   - Link to repository README for harness setup
6. For rejections: send a brief polite email explaining — typically "request appears commercial; licensing inquiries to yan.zheng.mat@gmail.com" or "request lacks sufficient detail, please resubmit with [X]".

## Monthly Maintenance

- Export the Sheet to CSV, archive locally + cloud backup. This preserves the click-wrap evidence independently of Google's availability.
- Review `approved` list against any abuse reports. Revoke access as needed.

## Revocation

- In Sheet, set `Status` to `revoked`.
- In GDrive, remove the revoked email from the folder's sharing list.
- Optionally send a notification email documenting the reason and the effective date.
