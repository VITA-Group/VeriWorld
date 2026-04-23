/**
 * VeriWorld Access Request Form — one-shot generator (Google Apps Script).
 *
 * How to run (takes < 1 minute):
 *   1. Sign into the Google account that should OWN the form
 *      (per FORM_TEMPLATE.md that is axisworld.team@gmail.com).
 *   2. Go to https://script.google.com/home → "New project".
 *   3. Replace the default Code.gs contents with this file.
 *   4. Click ▶ Run, pick function `createForm`, grant permissions when asked.
 *   5. View → Logs. Copy the "Public" URL and paste into docs/ACCESS.md
 *      (replace the string FORM_LINK_PLACEHOLDER).
 *
 * Mirrors Q1–Q12 from docs/FORM_TEMPLATE.md with the lightweight single-
 * checkbox Q10. To swap to the split-per-term version, replace the
 * `addCheckboxItem` block in Section 3 with seven `addMultipleChoiceItem`
 * Yes/No questions, each required.
 *
 * Running this twice creates TWO separate forms — it does not update an
 * existing one. If you re-run, delete the previous form from Drive first.
 */

var GITHUB_BASE = 'https://github.com/yanyanzheng96/VeriWorld/blob/main';
var LICENSE_URL = GITHUB_BASE + '/LICENSE';
var NOTICE_URL  = GITHUB_BASE + '/NOTICE.md';

function createForm() {
  var form = FormApp.create(
    'VeriWorld — Request Access to the Packaged Unreal Engine Build'
  );

  form.setDescription(
    'Use this form to request access to the access-gated Windows Unreal Engine ' +
    'build required to run the VeriWorld tasks.\n\n' +
    'Before submitting, please read:\n' +
    '- LICENSE: ' + LICENSE_URL + '\n' +
    '- NOTICE: '  + NOTICE_URL  + '\n\n' +
    'Submitting this form constitutes acceptance of the terms in the LICENSE ' +
    'and NOTICE. Access is personal and non-transferable. Access may be revoked ' +
    'at any time if terms are violated.\n\n' +
    'For operational questions about this form or the build: axisworld.team@gmail.com\n' +
    'For licensing or legal questions: yan.zheng.mat@gmail.com'
  );

  form.setCollectEmail(true);
  form.setLimitOneResponsePerUser(false);
  form.setConfirmationMessage(
    'Thank you. Your request has been received. You will receive a response ' +
    'within 5 business days at the email address you provided. If you do not ' +
    'hear back within 7 business days, email axisworld.team@gmail.com.'
  );

  // ---------- Section 1 — Applicant Information ----------
  form.addPageBreakItem().setTitle('Section 1 — Applicant Information');

  form.addTextItem()
    .setTitle('Full name')
    .setRequired(true);

  var emailItem = form.addTextItem()
    .setTitle('Google account email (this is where access will be granted)')
    .setRequired(true);
  emailItem.setValidation(
    FormApp.createTextValidation()
      .setHelpText('Please enter a valid email address. Access will be shared with this exact address via Google Drive.')
      .requireTextMatchesPattern('.+@.+\\..+')
      .build()
  );

  form.addTextItem()
    .setTitle('Affiliation (institution, lab, or independent)')
    .setRequired(true);

  form.addMultipleChoiceItem()
    .setTitle('Role')
    .setChoiceValues([
      'Undergraduate student',
      'Graduate student (Masters / PhD)',
      'Postdoctoral researcher',
      'Faculty / Principal investigator',
      'Research scientist / engineer (academic)',
      'Research scientist / engineer (industry research lab)',
      'Independent researcher',
      'Other (please specify)'
    ])
    .setRequired(true);

  form.addTextItem()
    .setTitle('If you selected "Industry research lab" or "Other": please specify team / company / description')
    .setRequired(false);

  form.addTextItem()
    .setTitle('Country / region of primary residence or institution')
    .setRequired(true);

  // ---------- Section 2 — Intended Use ----------
  form.addPageBreakItem().setTitle('Section 2 — Intended Use');

  form.addParagraphTextItem()
    .setTitle('What do you plan to do with VeriWorld?')
    .setHelpText(
      'Briefly describe your research question, which models you plan to ' +
      'evaluate, and your anticipated publication or dissemination venue ' +
      '(e.g., specific conference, course project, internal exploration, ' +
      'etc.). 2–5 sentences.'
    )
    .setRequired(true);

  form.addMultipleChoiceItem()
    .setTitle('Expected usage duration')
    .setChoiceValues([
      'Less than 1 month',
      '1–3 months',
      '3–6 months',
      '6–12 months',
      'More than 12 months'
    ])
    .setRequired(true);

  form.addMultipleChoiceItem()
    .setTitle('How did you hear about VeriWorld?')
    .setChoiceValues([
      'Paper / preprint',
      'Conference / workshop',
      'GitHub',
      'Social media',
      'Colleague / advisor recommendation'
    ])
    .showOtherOption(true)
    .setRequired(false);

  // ---------- Section 3 — Terms Acknowledgment ----------
  form.addPageBreakItem()
    .setTitle('Section 3 — Terms Acknowledgment')
    .setHelpText(
      'Please review the LICENSE and NOTICE files in the VeriWorld repository ' +
      'before continuing. The checkboxes below correspond to specific terms. ' +
      'You must check all of them to submit.\n\n' +
      'LICENSE: ' + LICENSE_URL + '\n' +
      'NOTICE: '  + NOTICE_URL
    );

  var termsOptions = [
    'I have read the LICENSE (PolyForm Noncommercial License 1.0.0) in full and agree to be bound by its terms.',
    'I have read the NOTICE in full and agree to be bound by its terms, including specifically Sections 2.1, 3, and 4.',
    'I understand that access granted to me is personal and non-transferable, and that I may not share, redistribute, mirror, re-host, or transfer the packaged build to any third party.',
    'I understand that I may not reverse engineer, decompile, disassemble, or extract assets from the packaged build, and that I may not circumvent any access or protection measure.',
    'I understand that I may not use the software or any output derived from it as training data, fine-tuning data, distillation target, reinforcement signal, or any other input to the training or improvement of any commercial model, product, or service.',
    'I understand that I may not incorporate the software into any commercial product, paid service, or proprietary offering, and that I may not republish it as another benchmark, dataset, or evaluation suite.',
    'I acknowledge that submitting this form constitutes acceptance of the LICENSE and NOTICE, regardless of whether I have reviewed them in detail, and that the licensor may pursue legal action for violations under applicable copyright, trade-secret, and anti-circumvention law.'
  ];

  var termsItem = form.addCheckboxItem()
    .setTitle('Terms acceptance — you must check all seven boxes to submit.')
    .setChoiceValues(termsOptions)
    .setRequired(true);
  termsItem.setValidation(
    FormApp.createCheckboxValidation()
      .setHelpText('All seven acknowledgments are required to proceed.')
      .requireSelectAtLeast(7)
      .build()
  );

  // ---------- Section 4 — Optional ----------
  form.addPageBreakItem().setTitle('Section 4 — Optional');

  form.addMultipleChoiceItem()
    .setTitle('Preferred build platform')
    .setChoiceValues([
      'Windows 64-bit (available)',
      'Linux (not currently distributed — noted for demand tracking)',
      'macOS (not currently distributed — noted for demand tracking)'
    ])
    .setRequired(false);

  form.addParagraphTextItem()
    .setTitle('Anything else you\'d like us to know')
    .setRequired(false);

  // ---------- Output ----------
  var publishedUrl = form.getPublishedUrl();
  var editUrl      = form.getEditUrl();

  Logger.log('=== VeriWorld Access Form Created ===');
  Logger.log('Public (share this) : ' + publishedUrl);
  Logger.log('Edit (admin only)   : ' + editUrl);
  Logger.log('');
  Logger.log('Next steps:');
  Logger.log('  1. Paste the Public URL into docs/ACCESS.md (replace FORM_LINK_PLACEHOLDER).');
  Logger.log('  2. Open the Edit URL → Responses tab → green Sheets icon to create a linked response Sheet.');
  Logger.log('  3. In that Sheet, add two extra columns at the end: "Status" and "Granted At".');
  Logger.log('  4. In the Sheet: Tools → Notification settings → email on new submission.');
}
