# Contributing

This repository runs a live system. It collects from real solar plants every
few hours and emails real customers about them. Most of what follows exists
because something went wrong once, and the reason is given each time — a rule
you understand is one you will still follow in three months.

Read this once before your first pull request. It is short.

---

## Getting set up

```bash
git clone https://github.com/ktronicsdev/IoTDeviceMonitor
cd IoTDeviceMonitor
pip install pytest pyyaml
pytest src/test/java/org/ktronics/scripts/integration/ -q
```

Python 3.11 or newer. CI runs 3.11.

**Two tests will fail on a fresh checkout and it is not your fault.**
`test_credentials_file_exists` and `test_credentials_file_is_valid_json` read
`src/main/java/org/ktronics/config/credentials.json`, which holds live customer
logins and is therefore gitignored. You will not be given it to start with. On
Windows you will also see a line-endings test fail in a fresh clone. Ignore all
three; CI has the real file and checks them properly.

Anything *else* red is yours.

### Reading a failed build

A "❌ Build FAILED" email does not always mean the code is broken. These
workflows have two jobs, and they fail for very different reasons:

- **`tests` failed** — a real problem. Something you changed broke a test, or
  a test that was passing has started failing. Read it.
- **`run` failed** — data collection. Usually an API that timed out, a vendor
  portal having a bad day, or two workflows racing each other to commit
  (see issue #18). It recovers on the next cycle by itself.

So check *which job* went red before assuming it was you. The one case worth
reacting to immediately is `tests` red on `main`, because that means something
reached the live branch broken.

---

## Never push to `main`

`main` is the live branch. Every push to it fires the production workflows,
which collect from 26 plants and send customer email. There is no staging copy
of this system.

Work on a branch, open a pull request, and let it be reviewed:

```bash
git checkout main && git pull
git checkout -b short-description-of-the-change
# ... work ...
git push -u origin short-description-of-the-change
```

If you push to `main` by accident, say so the same hour. It is recoverable.
Silence about it is not.

---

## Anything that can make output go quiet ships behind a flag, off by default

Every outbound channel and every rule that can *suppress* a message has a flag
in [`src/main/java/org/ktronics/config/features.json`](src/main/java/org/ktronics/config/features.json):

```python
from features import is_enabled

if is_enabled('emails.device_alarms_admin'):
    ...
```

Override for a single run without a commit — the env var is `KT_FEATURE_` plus
the dotted path, upper-cased, dots to underscores:

```bash
KT_FEATURE_EMAILS_DEVICE_ALARMS_ADMIN=false pytest ...
```

New suppressing behaviour ships as `false`, the way
`alerts.production_orange_3month` does. It merges inert, gets watched against
live traffic for a cycle, and is switched on deliberately. Pass
`default=False` as well, so the behaviour is off even if the flag is missing
from the file.

**A flag that suppresses something wants three states, not two.** Use
`mode_of()` instead of `is_enabled()`:

```python
from features import mode_of

mode = mode_of('alerts.six_hour_gate', default='off')   # off | dry_run | on
```

`dry_run` is the one that matters: the rule runs and records what it *would*
have done, and acts on nothing. That is how you find out what a filter will
silence before it silences anything — which the six-hour gate did not have
when it went live, so nobody could answer that question in advance.

A legacy `true`/`false` in the file still means `on`/`off`, so promoting a
boolean flag to three states breaks nothing.

**Why:** a channel that wrongly goes loud is irritating and obvious. A channel
that wrongly goes quiet is invisible — the symptom is an inbox that looks
healthy, and you can lose real faults for months before anyone notices. The flag
is how we find out which we have built before a customer does.

Put the check where *every* caller passes through it, not at one call site.
A flag only one of two callers honours is worse than no flag, because it looks
like cover it is not providing.

---

## One issue per pull request

Not four. This is the rule people most often think is bureaucratic, so here is
what it actually costs:

- **Ordering stops meaning anything.** The six-hour gate design says the status
  filter must land *before or with* the gate, never after — because a dwell gate
  on an unfiltered feed turns every stale, already-fixed alarm into a fresh
  call-out. Bundle them and that sequencing is gone.
- **You cannot revert one piece.** When something misbehaves at 2 a.m. the
  question is "what do we take out", and the answer should not be "four
  unrelated changes".
- **It cannot be reviewed.** A reviewer reading 400 lines across seven files
  does a worse job than the same person reading 80.

If a change needs another change first, say so in the PR and let them land in
order.

---

## Done means you ran it

Paste the output of the full test run into the pull request description.

```bash
pytest src/test/java/org/ktronics/scripts/integration/ -q
```

A pull request description that disagrees with the branch costs more than an
honest one that says "two of these still fail". The reviewer plans around what
you tell them, so being told something is finished when it is not is worse than
being told nothing.

"I could not get X working" is a perfectly good line to put in a PR. So is
"I am not sure this is right". Neither has ever caused a problem here.

---

## Reply to every review thread

Each comment gets an answer: `fixed in <sha>`, or "I don't think this applies,
because…". Both are good answers.

Pushing a correct fix in silence is not, because the reviewer cannot tell
agreement from oversight and has to re-read the whole diff to find out. And if
you think a review comment is wrong, say so — reviewers are wrong regularly, and
an argued disagreement is more useful than a change you did not believe in.

Do not mark a thread resolved unless it is. If a comment turns out not to apply,
that is worth a sentence, not a "fixed".

---

## This repository is public

Anyone can read it, and git history is permanent.

Never commit: credentials, API keys, tokens, connection strings, customer names
alongside production figures, customer email addresses, or third-party contact
lists. Check `.gitignore` before adding a data file — several entries are there
for exactly this reason.

If something sensitive does land, say so immediately. Rotating a key is a ten
minute job; finding out six months later is not.

---

## `data/`, `alerts/` and `state/` belong to the bots

Those directories are written by the scheduled workflows every few hours. Do not
hand-edit them: your change will be overwritten, and in the meantime it makes
your diff unreadable.

If a test needs data, build it in the test with a temporary directory. There are
plenty of examples in `src/test/java/org/ktronics/scripts/integration/`.

---

## Tests

They live in `src/test/java/org/ktronics/scripts/integration/`.

- `pytest.ini` sets `pythonpath`, which is what actually makes
  `import generate_device_alarms` work. The `SCRIPTS_DIR` constant some test
  files carry does **not** resolve to a real directory — do not build paths from
  it. Walk up from `__file__` to the repo root instead.
- Name a test for the behaviour it defends, not the function it calls.
  `test_r2_timezone_conversion_offset` tells you what broke; `test_normalize_2`
  does not.
- **Check that a new test fails before it passes.** Break the behaviour on
  purpose, watch the test go red, then fix it. A test that was green from the
  moment it was written has not been shown to guard anything — this has bitten
  this repo twice, in both directions.
- Do not edit a test to make it pass. If you believe a test is wrong, change it
  *and say so in the PR*, with the reason. That is allowed and has been the right
  call before now.

Pull requests run the full suite automatically. A green check is necessary, not
sufficient — it means nothing broke, not that the change is right.

---

## Formatting

Run your formatter over the lines you changed, not across files you are only
touching incidentally. A 200-line reformat with two real edits inside it hides
the change from the reviewer and guarantees a conflict with whoever else is in
that file.

---

## When you think the design is wrong

Say so in the pull request, before building it.

The design documents — [`six-hour-gate/README.md`](six-hour-gate/README.md) for
the alarm gate, [`Readme.md`](Readme.md) for the use-case numbering — are the
record of decisions and the reasoning behind them. They are not sacred, and
several of them have been changed by someone pointing out a problem.

What does not work is quietly implementing something different, or quietly
implementing something you believe is wrong. Both leave the next person reading
a document that no longer describes the system.
