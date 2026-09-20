/**
 * Fires the GitHub Actions daily sync.
 *
 * GitHub's own cron is best-effort and did not run this workflow at all on
 * 2026-09-20 -- five scheduled slots, zero runs, with Actions reporting
 * operational. Apps Script time triggers are reliable and already run this
 * shop's other automation, so the schedule lives here and GitHub only does
 * the work.
 *
 * SETUP
 *
 * 1. Make a GitHub token that may start workflows:
 *      github.com -> your avatar -> Settings -> Developer settings
 *      -> Personal access tokens -> Fine-grained tokens -> Generate new token
 *      Repository access : Only select repositories -> sunset-custom-framing
 *      Permissions       : Repository permissions -> Actions -> Read and write
 *      Expiration        : whatever you are willing to renew. 1 year is fine.
 *    Copy the token. It is shown once.
 *
 * 2. In this Apps Script project: Project Settings -> Script Properties
 *      -> Add script property
 *      Property : GITHUB_TOKEN
 *      Value    : the token you just copied
 *
 * 3. Triggers (the clock icon) -> Add Trigger
 *      Function          : fireDailySync
 *      Event source      : Time-driven
 *      Type              : Day timer
 *      Time of day       : 10am to 11am
 *
 *    Apps Script fires somewhere inside that hour, in the calendar's own
 *    timezone, and handles daylight saving itself.
 *
 * 4. Run fireDailySync once by hand to check it works. A run should appear at
 *    github.com/Oxalisviolacea/sunset-custom-framing/actions within seconds.
 */

const GITHUB_OWNER = 'Oxalisviolacea';
const GITHUB_REPO = 'sunset-custom-framing';
const WORKFLOW_FILE = 'daily.yml';
const ALERT_TO = 'DIGEST_TO_ADDRESS';

function fireDailySync() {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) {
    notifyFailure('No GITHUB_TOKEN script property is set, so nothing could be started.');
    return;
  }

  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}` +
              `/actions/workflows/${WORKFLOW_FILE}/dispatches`;

  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true,
  });

  const code = response.getResponseCode();

  // GitHub answers 204 with an empty body when the run has been queued.
  if (code === 204) {
    console.log('Daily sync started on GitHub.');
    return;
  }

  notifyFailure(
    `GitHub refused to start the daily sync.\n\n` +
    `HTTP ${code}\n${response.getContentText()}\n\n` +
    `A 401 or 403 usually means the token expired or lost its Actions ` +
    `permission. A 404 means the token cannot see the repository.`
  );
}

/**
 * The sync sends its own failure email, but it cannot do that if it never
 * started. This covers that gap.
 */
function notifyFailure(details) {
  console.error(details);
  MailApp.sendEmail({
    to: ALERT_TO,
    subject: 'Production Digest DID NOT START',
    body:
      'The daily pickup sync could not be started this morning.\n\n' +
      details + '\n\n' +
      'The calendar has not been updated today and no digest was sent.\n\n' +
      'To run it by hand on the shop computer:\n\n' +
      '  cd ~/Desktop/vf_sync/sunset-sync-project\n' +
      '  ./run_daily.sh\n',
  });
}
