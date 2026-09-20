/**
 * The schedule for the Sunset pickup sync.
 *
 * GitHub runs the work; this decides when. GitHub's own cron never fired for
 * this repository -- a known bug affecting new private repos on Free plans
 * (community discussions 202602, 203822, 205984 and others, all unanswered).
 * Apps Script time triggers are reliable and already ran this shop's previous
 * digest for months, so the clock lives here.
 *
 * Two functions, two triggers:
 *
 *   fireDailySync()      10am  starts the job on GitHub
 *   checkDailySyncRan()  11am  makes sure it actually worked
 *
 * The second one matters. The sync emails you when it fails, but it cannot do
 * that if it never started. Nothing inside GitHub can report that GitHub did
 * not run something, so the check lives out here too.
 *
 * ---------------------------------------------------------------- SETUP ----
 *
 * 1. Make a GitHub token that may start workflows:
 *      github.com -> your avatar -> Settings -> Developer settings
 *      -> Personal access tokens -> Fine-grained tokens -> Generate new token
 *      Repository access : Only select repositories -> sunset-custom-framing
 *      Permissions       : Repository permissions -> Actions -> Read and write
 *      Expiration        : whatever you will remember to renew; 1 year is fine
 *    Copy it. GitHub shows it once.
 *
 * 2. Project Settings -> Script Properties -> Add script property
 *      Property : GITHUB_TOKEN
 *      Value    : the token
 *
 * 3. Triggers (clock icon) -> Add Trigger, twice:
 *      fireDailySync      | Time-driven | Day timer | 10am to 11am
 *      checkDailySyncRan  | Time-driven | Day timer | 11am to noon
 *
 *    Apps Script fires somewhere inside the hour you pick, in this account's
 *    timezone, and handles daylight saving on its own.
 *
 * 4. Run fireDailySync once by hand. A run should appear within seconds at
 *    github.com/Oxalisviolacea/sunset-custom-framing/actions
 */

const GITHUB_OWNER = 'Oxalisviolacea';
const GITHUB_REPO = 'sunset-custom-framing';
const WORKFLOW_FILE = 'daily.yml';
const ALERT_TO = 'DIGEST_TO_ADDRESS';

const RUN_BY_HAND =
  'To run it by hand on the shop computer:\n\n' +
  '  cd ~/Desktop/vf_sync/sunset-sync-project\n' +
  '  ./run_daily.sh\n';


/** 10am: start the job on GitHub. */
function fireDailySync() {
  const token = token_();
  if (!token) {
    alert_('Production Digest DID NOT START',
           'No GITHUB_TOKEN script property is set, so nothing could be started.');
    return;
  }

  const response = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}` +
    `/actions/workflows/${WORKFLOW_FILE}/dispatches`,
    {
      method: 'post',
      contentType: 'application/json',
      headers: githubHeaders_(token),
      payload: JSON.stringify({ ref: 'main' }),
      muteHttpExceptions: true,
    });

  const code = response.getResponseCode();

  // GitHub answers 204 with an empty body once the run is queued.
  if (code === 204) {
    console.log('Daily sync started on GitHub.');
    return;
  }

  alert_('Production Digest DID NOT START',
    `GitHub refused to start the daily sync.\n\nHTTP ${code}\n` +
    `${response.getContentText()}\n\n` +
    'A 401 or 403 usually means the token expired or lost its Actions ' +
    'permission. A 404 means the token cannot see the repository.');
}


/**
 * 11am: did it actually run? The job reports its own failures, but only if it
 * got as far as running. This covers the case where it never did.
 */
function checkDailySyncRan() {
  const token = token_();
  if (!token) {
    alert_('Production Digest — cannot be checked',
           'No GITHUB_TOKEN script property is set, so the run could not be verified.');
    return;
  }

  const response = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}` +
    `/actions/workflows/${WORKFLOW_FILE}/runs?per_page=30`,
    { headers: githubHeaders_(token), muteHttpExceptions: true });

  if (response.getResponseCode() !== 200) {
    alert_('Production Digest — cannot be checked',
      'GitHub could not be asked whether the sync ran.\n\n' +
      `HTTP ${response.getResponseCode()}\n${response.getContentText()}\n\n` +
      'The digest may well have gone out. This is the check failing, not ' +
      'necessarily the sync.');
    return;
  }

  const today = todayHere_();
  const succeeded = (JSON.parse(response.getContentText()).workflow_runs || [])
    .filter(run => run.conclusion === 'success')
    .filter(run => sameDayHere_(run.created_at, today));

  if (succeeded.length > 0) {
    console.log(`${succeeded.length} successful run(s) today. Nothing to report.`);
    return;
  }

  alert_('Production Digest DID NOT RUN',
    'No successful run of the daily sync happened today.\n\n' +
    'The pickup calendar has not been updated and no digest was sent. ' +
    'Nothing is broken on the calendar itself -- it simply was not touched.\n\n' +
    RUN_BY_HAND);
}


// ----------------------------------------------------------------- helpers --

function token_() {
  return PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
}

function githubHeaders_(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };
}

function todayHere_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

function sameDayHere_(isoStamp, day) {
  return Utilities.formatDate(
    new Date(isoStamp), Session.getScriptTimeZone(), 'yyyy-MM-dd') === day;
}

function alert_(subject, details) {
  console.error(`${subject}: ${details}`);
  MailApp.sendEmail({ to: ALERT_TO, subject: subject, body: details });
}
