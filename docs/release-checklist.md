# Release checklist

**Checklist version: 5**

Version 5 (2026-08-09) makes this document self-contained after the completed
MVP planning document was retired. It also records the hosted Windows
isolation used by the passing release-readiness workflow: outbound firewall
rules cover Python, the `econpapers` launcher, and the managed runtime while
the Actions runner itself remains connected.

Version 4 (2026-08-09) adds the manually triggered
`.github/workflows/release-readiness.yml` workflow. It runs the real CLI
scenarios on hosted Linux, macOS, and Windows runners: artifacts and a
synthetic library are prepared while online, the default network route is
isolated, the commands run offline, and networking is restored before the job
finishes. Its uploaded per-OS artifacts are release evidence; the local
procedure below remains the fallback when the workflow cannot be used.

Version 3 (2026-08-09) retires version 2's dated observation: the current CI
configuration has now executed, all eight jobs green, at
`8126fba`; see
[GitHub Actions run 31295765312](https://github.com/ledwindra/econ-paper-cli/actions/runs/31295765312).
Limitation 3 keeps only its durable half. Limitation 2 narrows to the
exact-floor gap. No procedural rule changed.

Version 2 (2026-08-08) recorded that, as of that date, no job of the current
CI configuration had completed a step; added limitation 3; required
unexecuted CI rows to read "not run" rather than blank; and fixed the release
tag to the candidate SHA. Version 1 assumed a green CI matrix was available,
and a run record produced under it would have overstated its evidence. The
dated observation has now been superseded by a successful run; the rule it
rests on has not — see limitation 3.

This checklist is the source of truth for release-readiness verification.
Working through it produces a *run record*, a filled-in copy of the "Run
record" section at the bottom. A blank checklist, a green default test suite,
or a workflow run against another commit does not satisfy the release gate.

---

## 1. Which commit is being released

Committing a run record necessarily moves `HEAD` past the commit the run
tested, so "tested at `HEAD`" is never true and must not be written.

1. Choose a **release-candidate commit** and check it out by SHA, detached:

   ```bash
   git checkout --detach <release-candidate-sha>
   ```

2. Confirm the working tree is clean. Every command below runs against that
   exact commit, and the run record names it.
3. The commit that adds the run record is **results-only**: it may touch this
   file and the run record and nothing else. Any source, test, or
   configuration change invalidates the run — pick a new candidate SHA and
   start over.
4. **Tag the candidate SHA**, never the results-only commit on top of it.
   This convention was settled on 2026-08-08. The tag must name the commit
   whose behavior was tested, and the results-only commit is by definition one
   the run did not test. The run record restates the convention so a reader of
   the record alone can see it, but it is not a per-release choice.

## 2. Environment prerequisites

- A supported Python (see § 5 for what CI covers and what actually ran).
- A clean virtual environment; the install step below is run inside it.
- For § 6 and § 7 only: the private acceptance corpus and real local
  artifacts. Neither is required for §§ 3–4.

```bash
python -m pip install -e ".[dev]"
```

Expected: exits 0.

## 3. Default suite

Every command's expected outcome is exit code 0.

| Command | Expected |
| --- | --- |
| `econpapers --help` | 0, usage text listing `setup`, `status`, `chat`, `update`, `analyze` |
| `ruff check .` | 0, "All checks passed!" |
| `ruff format --check .` | 0, "N files already formatted" |
| `pytest` | 0, all tests pass, **zero** items collected from `integration_tests/` |

The last point is a real check, not a formality — it is what makes the opt-in
tier opt-in. Confirm it explicitly:

```bash
pytest --collect-only -q | grep -c integration_tests
```

Expected: `0`.

## 4. Tier 1 release-readiness scenarios

These run inside the default `pytest` above; this section exists so the run
record can state which scenarios were exercised and what they do *not* cover.

| Scenario | Where | Establishes |
| --- | --- | --- |
| No-upload guard, four service paths + negative control | `tests/services/test_release_readiness.py` | No outbound network from `chat`/shell/`analyze`/`status` **service** functions |
| Concurrent writer vs. open shell session | same | Snapshot immutability against an *idle* reader; not lock contention |
| Cached-ready executable loss | same | Current `INTERNAL_FAILURE` mapping (see § 8) |
| Interrupted model repair, three outcomes | `tests/services/test_model_provisioning.py` | Valid → zero downloads; corrupt + failed staging → prior bytes intact; corrupt + success → new bytes |
| Interrupted runtime repair | `tests/services/test_runtime_provisioning.py` | Valid install untouched; corrupt-install repair recovers on a later run |

**What tier 1 is not.** The public CLI entry points in
`services/commands.py` expose no injection seams, so none of the above is
`econpapers <command>` coverage. Section 7 is.

## 5. Cross-platform coverage

CI (`.github/workflows/ci.yml`) is *configured* to cover exactly:

- `{ubuntu-latest, macos-latest, windows-latest}` × `{3.11, 3.14}`
- `{ubuntu-latest, macos-15-intel}` × `3.10.12` (the exact `pyproject.toml`
  floor)

State it in those terms. Do not write "cross-platform support" without
qualification.

**Configured is not the same as executed — check before you claim either.**
As of 2026-08-09 all eight jobs of the current configuration have executed
and passed at one SHA (`8126fba`), and a green run of a superseded workflow
never counts for the current one — see limitation 3 in § 8. The date is what
expires here, not the rule: a matrix change resets coverage to nothing again,
and a pass at one SHA is not a pass at the candidate. Re-establish the status
with the commands below rather than trusting this paragraph. Before filling
the run record, establish which jobs
actually ran at the candidate SHA. Ask for `databaseId`, because the second
command needs it:

```bash
gh run list --limit 20 \
  --json databaseId,headSha,workflowName,status,conclusion,createdAt
```

```bash
gh api repos/{owner}/{repo}/actions/runs/<run-id>/jobs \
  --jq '.jobs[] | "\(.conclusion) steps=\(.steps|length) \(.name)"'
```

A job reporting **zero steps** and a two- to ten-second duration did not fail
its tests — no step ran at all. That alone does **not** tell you why: runner
allocation, cancellation, a billing or minutes limit, or another service
fault all look identical here. If the cause matters for the release notes,
get it from the repository's Actions billing or settings page and record what
you saw; otherwise write "cause not established".

Record those rows as **"not run"**. Never leave them blank, and never carry a
pass forward from an earlier SHA — or from an earlier *workflow*: a green run
of a superseded matrix says nothing about the current one.

### Floor-version availability probe (required, per release)

The floor matrix depends on which builds `actions/python-versions` publishes,
which changes over time. Re-probe rather than trusting this file:

```bash
curl -sS https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json \
  | python -c "import json,sys; [print(e['version'], sorted({(f['platform'], f['arch']) for f in e['files']})) for e in json.load(sys.stdin) if e['version'].startswith('3.10.') and int(e['version'].split('.')[2].split('-')[0]) >= 12]"
```

Result as of **2026-08-08**: every 3.10.12–3.10.20 entry publishes
`linux/x64`, `linux/arm64`, and `darwin/x64` — and **no `win32` build at all**.
3.10.11 was the last 3.10 with Windows installers, and it is below the floor.
`darwin/arm64` also stops at 3.10.11, which is why macOS floor coverage runs
on the x64 `macos-15-intel` label rather than `macos-latest`.

- If the probe still shows no `win32` build: record the Windows limitation in
  § 8 and continue.
- If a `win32` build has appeared: add it to the `floor-check` matrix and drop
  that limitation.

Record the probe date and result in the run record.

## 6. Private acceptance corpus

The corpus is **not** in this repository and its location is **not** recorded
here — `AGENTS.md`'s git discipline forbids committing personal paths, and the
exact filenames, sizes, and checksums live in the gitignored
`papers/ACCEPTANCE_CORPUS_RECORD.md`. What this file records is the contract
and the results.

```bash
ECONPAPERS_TEST_ACCEPTANCE_DIR="<repo root>" \
ECONPAPERS_ACCEPTANCE_PAPER_DIR="<private corpus dir>" \
python -m pytest tests/evaluation/test_pdf_acceptance_harness.py -m real_pdf -v
```

Expected: `test_pdf_acceptance_harness_opt_in` passes, all six cases
(`case_a`–`case_f`). Record pass/fail per case in the run record — case labels
only, never filenames or paths.

## 7. Opt-in integration tier

Located in `integration_tests/` (outside `testpaths`), marked `model`, and
gated on its own environment variables so it also skips when pytest is pointed
at the directory directly. Both mechanisms are required; the marker alone
deselects nothing.

```bash
ECONPAPERS_LLAMA_CPP="<runtime executable>" \
ECONPAPERS_MODEL_MANIFEST="<manifest path>" \
ECONPAPERS_MODEL_BASE="<model dir>" \
python -m pytest integration_tests -m model -v
```

Expected: tests run rather than skip. A skip here means the gate variables are
unset — that is a not-run, not a pass, and must be recorded as such.

**Current contents, stated so this section is not read as broader than it
is.** `integration_tests/` currently holds one test:
`test_llama_cpp_model.py`, a generation-contract smoke test against a manually
installed runtime and model. Automated tier 2 coverage of the *CLI* scenarios
— real `econpapers chat`/shell/`analyze`/`status` under network isolation — is
implemented by the manually triggered release-readiness workflow on all three
hosted operating systems. The workflow's network isolation is platform
specific and restores the runner's route in cleanup; its uploaded artifacts
record the three probe results and command output. If the workflow is not
available, the manual isolation procedure below remains valid, and the
no-upload gate still rests on tier 1's guarded service tests (§ 4).

### Network isolation and its control

Tier 2's claim is that the real commands complete with the machine offline.
A command succeeding proves nothing if the isolation step silently failed, so
the control is three probes, not two, and the **preflight must succeed**:

| Step | Probe | Required result |
| --- | --- | --- |
| 1. Before isolation | raw-IP TCP connect | **succeeds** |
| 2. After isolation, before commands | same | fails |
| 3. After commands | same | fails |

```bash
python -c "import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)"
```

A literal address is deliberate: a DNS-only failure is not isolation. If
`1.1.1.1:443` is unreachable from this host even before isolation, substitute
a reachable endpoint and record which.

Isolation, by platform — the workflow records its exact command; for a manual
run, record the command used and whether it needed elevation:

| Platform | Command |
| --- | --- |
| Linux | run the commands inside `unshare -rn` |
| macOS | `networksetup -setairportpower <device> off`, plus any wired service set to off |
| Windows (manual) | `Disable-NetAdapter -Name <name>` (elevated) |
| Windows (hosted) | outbound firewall rules for `python.exe`, `econpapers.exe`, and the configured `llama-completion.exe` |

The automated workflow uses `sudo unshare --net` on Linux, temporarily brings
the default route down with `sudo ifconfig` on macOS, and installs
program-scoped outbound firewall rules with PowerShell on Windows. Windows
cannot disable the runner's adapter without also interrupting the job, so its
hosted result proves that the tested process tree is offline rather than that
the whole runner is offline. The workflow removes all temporary rules in
cleanup before uploading its artifact.

Then run, against an already-provisioned library, and expect each to complete:
`econpapers status`, `econpapers chat "<question>"`, bare `econpapers`
(ask one question, then `/exit`), and `econpapers analyze <pdf>`.

**Downgrade rule.** If the preflight does not succeed, if either
post-isolation probe does not fail, or if the control is skipped, this
section's result is recorded as an *observational manual check* and **may not
be cited as evidence for the no-upload gate**. The gate then rests on the tier
1 guarded service tests alone. Say so explicitly in the run record.

### Real-network artifact download

One manual run, network permitted, against a clean config/library directory:

```bash
ECONPAPERS_CONFIG_DIR=<temp> ECONPAPERS_LIBRARY_DIR=<temp> econpapers setup
```

Expected: the pinned runtime and default model download, verify against § 9,
and durable config is written; `econpapers status` then reads it back.

This is the only step in this document where the **application** reaches the
network, and it does so only to download the pinned artifacts named in § 9.
It is not the only network use in the checklist overall — § 5's `curl` probe
of the `actions/python-versions` manifest is an operator command, not
application traffic, and § 7's connectivity probes are deliberately network
operations. None of those bear on the no-upload claim, which is about what
`chat`/shell/`analyze`/`status` initiate.

## 8. Known limitations (carry into release notes)

1. **Runtime repair is not atomic.** Repairing a *corrupt* managed runtime
   removes the install directory before restaging, because a directory cannot
   be replaced onto a non-empty destination the way `os.replace` handles a
   single file. An interrupt in that window destroys the corrupt install; no
   partial tree is ever promoted, and a later run converges. A valid install
   is never touched. The provisioning implementation and its regression tests
   are authoritative for this recovery contract.
2. **Floor-version coverage excludes Windows.** No `win32` build exists at or
   above the 3.10.12 floor (§ 5), so on Windows the lowest interpreter this
   project can test is 3.11. Windows 3.11 and 3.14 have now been *tested*
   under the current configuration (2026-08-09, `8126fba`), not merely
   configured; what remains open is only that nothing verifies Windows at the
   declared floor itself. The *absence of a build* is a fact about
   `actions/python-versions`, not about this project: it cannot be cleared
   here, only by upstream publishing one.
3. **A superseded workflow's passes never count.** This is the durable rule
   and it does not expire: a green run validates the workflow *that ran*, so
   when the matrix, interpreter list, or job set changes, prior passes stop
   being evidence and coverage resets to nothing until the new configuration
   runs. Re-check with the commands in § 5 rather than trusting any statement
   of status, including this one.

   Version 2 of this file paired that rule with a dated observation — that as
   of 2026-08-08 no job of the current configuration had ever completed a
   step, every run since `945341e` having failed at zero steps for reasons
   never established. **That observation is retired.** On 2026-08-09 the
   repository was made public and the very next push executed normally: all
   eight jobs ran their steps at `8126fba`, and all eight passed. The
   coincidence with the visibility change is suggestive of a private-repo
   Actions-minutes limit, but that was never confirmed against the billing
   page and is not asserted here. What is now on record is only that the
   configuration executes.

   Two consequences follow. The Intel-macOS floor gap is closed —
   `macos-15-intel` at exactly 3.10.12 passed. And the earlier claim that
   every commit from `945341e` onward was CI-unvalidated no longer describes
   the present: the documentation truth pass, tier-1 release suite, and
   artifact-metadata alignment are all included in the green run at
   `8126fba`. The first Windows execution under this configuration also found
   a real Windows-only test defect (a POSIX path literal asserted verbatim),
   fixed at that same SHA.

   For history: the last green run before this, `977c7e26` (2026-08-02),
   executed a *different* workflow against a *different* floor —
   `{ubuntu, macos, windows} × {3.10, 3.14}` with a floating `3.10` and no
   `floor-check` job. `requires-python` was `>=3.10` then, which floating
   `3.10` satisfies everywhere, so those passes were valid for that workflow;
   `945341e` raised the floor to `>=3.10.12` and reshaped the matrix
   together, superseding them. Superseded, not wrong — which is the rule
   above, stated as a worked example.
4. **A ready generator that loses its executable reports
   `INTERNAL_FAILURE`, not a typed failure.** `LlamaCppProcessError` falls in
   the shell's internal-failure group. Only the not-yet-ready case is typed,
   because it fails at `check_readiness()`. Pinned by
   `test_a_ready_generator_losing_its_executable_reports_internal_failure`.
   Reclassifying it is a behavior change with exit-code and documentation
   consequences and remains a **candidate follow-up issue**.
5. **The no-upload guarantee covers application-initiated traffic.** It does
   not and cannot constrain what a user-supplied `llama-completion` binary
   does in its own process, and it excludes `setup`/`update` artifact
   downloads by design (those are downloads of pinned, checksum-verified
   artifacts, not uploads).

## 9. Artifact identities

Cross-reference: [`artifact-licensing.md`](artifact-licensing.md) carries the
seven required licensing facts for each of these.

### Models (`domain/model_manifest.py`)

Default: `qwen2.5-1.5b-instruct-q4-k-m`.

| Model id | Size (bytes) | SHA-256 | License |
| --- | --- | --- | --- |
| `qwen2.5-1.5b-instruct-q4-k-m` | 1,117,320,736 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` | Apache-2.0 |
| `qwen2.5-7b-instruct-q4-k-m` | 4,683,074,240 | `65b8fcd92af6b4fefa935c625d1ac27ea29dcb6ee14589c55a8f115ceaaa1423` | Apache-2.0 |

### Runtimes (`domain/runtime_manifest_data.py`, schema 1)

| Runtime id | Version marker | Platform/arch | Size (bytes) | SHA-256 | License |
| --- | --- | --- | --- | --- | --- |
| `llama.cpp-b10199` | `10199` | macos/arm64 | 10,939,809 | `a7bc124584fbed7e848f7d95987a6c537399a7398682f45fa32b66852269ae6c` | MIT |
| `llama.cpp-b10199` | `10199` | macos/x86_64 | 11,216,652 | `df24f71388941f030cf4f0f716584f0c5fdeb4465ff67a036d37575d809b4799` | MIT |
| `llama.cpp-b10199` | `10199` | linux/x86_64 | 16,434,223 | `16d63bfb5c7e1c1656d940de398456ed2972af16ab5a0961f88c5929bc4fe58a` | MIT |
| `llama.cpp-b10199` | `10199` | windows/x86_64 | 18,350,490 | `b10b8cbcc0fef99771daf13cfea426d1dde4baf36618a9b4c4c30a6f79115650` | MIT |

Regenerate rather than transcribing, and paste the output into the run record:

```bash
python -c "from econ_paper_cli.domain.model_manifest import MANAGED_MODEL_CATALOG as C; from econ_paper_cli.domain.runtime_manifest_data import MANAGED_RUNTIME_MANIFEST as M; [print(a.model_id, a.size_bytes, a.sha256) for a in C.artifacts]; [print(a.runtime_id, a.platform.value, a.architecture.value, a.archive_size_bytes, a.archive_sha256) for a in M.artifacts]"
```

---

## Run record template

Copy this section, fill every field, and commit it as a results-only change.

```text
Release candidate SHA:
Checklist version:       2
Tag points at:           candidate SHA  (fixed convention — see § 1.4)
Date:
Operator:

Interpreter versions actually resolved (not matrix strings):
  ubuntu-latest:   3.11.x = ____  3.14.x = ____  3.10.12 = ____
  macos-latest:    3.11.x = ____  3.14.x = ____
  macos-15-intel:  3.10.12 = ____
  windows-latest:  3.11.x = ____  3.14.x = ____

§3 Default suite:        econpapers --help ___  ruff check ___  ruff format ___  pytest ___
    integration_tests collected by bare pytest (must be 0): ___

OS x Python x scenario results
------------------------------
Automated (CI). One row per job; PASS/FAIL per scenario group, from that
job's `pytest` run at the candidate SHA. Three non-result entries, and they
are not interchangeable:

  n/a       the job does not exist in the matrix
  not run   the job exists but did not execute at this SHA (§ 5) —
            including a run that "failed" with zero steps
  (blank)   never acceptable; a blank is an unanswered question

A pass observed at any other SHA is not a pass here.

  OS               Python    lint  format  suite  no-upload  concurrency  interruption
  ubuntu-latest    3.11      ____  ____    ____   ____       ____         ____
  ubuntu-latest    3.14      ____  ____    ____   ____       ____         ____
  ubuntu-latest    3.10.12   n/a   n/a     ____   ____       ____         ____
  macos-latest     3.11      ____  ____    ____   ____       ____         ____
  macos-latest     3.14      ____  ____    ____   ____       ____         ____
  macos-15-intel   3.10.12   n/a   n/a     ____   ____       ____         ____
  windows-latest   3.11      ____  ____    ____   ____       ____         ____
  windows-latest   3.14      ____  ____    ____   ____       ____         ____
  windows-latest   3.10.12   ----- no build published; see limitation 2 -----

  CI ran at the candidate SHA at all?  yes | no (see limitation 3)
  If no, every row above reads "not run" and the release evidence is the
  local run below, which is single-platform.

Local (the operator's machine). This is what carries the release when CI has
not run, so it is recorded, not assumed:

  OS / arch: ____________   Python: ______   Commit: ______________
  ruff check ___  ruff format ___  pytest ___ (passed: ____ skipped: ____)

Manual tier (§7), per command, per OS actually exercised. "Integration tier:
ran" is not an attestation; each cell is.

  OS / Python              status  chat  shell  analyze
  ______ / ______          ____    ____  ____   ____
  ______ / ______          ____    ____  ____   ____
  ______ / ______          ____    ____  ____   ____

  Platforms NOT manually exercised this release (state them; silence is not
  coverage):

§5 Floor probe date/result:
§6 Private corpus:       case_a ___ case_b ___ case_c ___ case_d ___ case_e ___ case_f ___
                         (labels only — no paths, filenames, or checksums here)
§7 Integration tier:     ran | skipped (skipped counts as NOT RUN)
    Per-command outcomes are the manual-tier table above, not this line.
    Isolation command:                       elevation required: yes | no
    Probe endpoint:
    Preflight (must succeed):                ___
    Post-isolation probe (must fail):        ___
    Post-command probe (must fail):          ___
    Downgrade rule triggered:                yes | no
    If yes: tier 2 is observational only and is NOT evidence for no-upload.
§7 Real-network setup run:
§9 Artifact identities regenerated and matched: yes | no

Known limitations reviewed (§8, items 1-5):   yes
Deviations from this checklist:
```
