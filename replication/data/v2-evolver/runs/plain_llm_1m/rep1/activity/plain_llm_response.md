Looking at the IR for the "evolver" project, I can identify three key workflows to diagram:

1. **Main Process Loop** — top-level orchestration (`index.js`)
2. **Gene Evolution Pipeline** — the core GEP cycle (signal detection → gene selection → mutation → validation → solidification)
3. **ATP Task Lifecycle** — the Agent Task Protocol buying/selling flow

```plantuml
@startuml evolver_main_loop

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam activity {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}

title Evolver — Main Process Loop

start

:Parse CLI Arguments\n(command, --loop, --verbose,\n--force, --scope, --token);

if (Command is "atp"?) then (yes)
    :Route to ATP Subcommand Handler\n(buy / orders / verify / status);
    stop
endif

if (Command is "hooks" / "install"?) then (yes)
    :Setup AI Platform Hooks\n(Claude Code, Codex, Cursor,\nKiro, OpenCode);
    stop
endif

if (Command is "status" / "stop" / "restart"?) then (yes)
    :Handle Lifecycle Command\n(lifecycle module);
    stop
endif

if (Acquire Process Lock?) then (no)
    :Exit — Another instance running\n(evolver.pid exists, PID alive);
    stop
endif

:Register Shutdown Handlers\n(SIGINT / SIGTERM →\nreleaseLock + stopEventStream);

fork
    :Initialize ATP AutoBuyer\n(daily budget cap, dedup TTL);
fork again
    :Initialize ATP AutoDeliver\n(poll interval ms);
fork again
    :Start Validator Daemon\n(if EVOLVER_VALIDATOR_ENABLED);
end fork

repeat

    :Read Evolution State\n(last_run, last_solidify,\nprevious signals);

    if (Force update requested?) then (yes)
        :Execute Force Update\n(copy new evolver files to disk);
        :Spawn Replacement Process;
        :Release Process Lock;
        stop
    endif

    if (Pending solidification exists?) then (yes)
        :Reject Pending Run\n(mark run_id as rejected);
    endif

    :Run Evolution Cycle\n(signal detection → gene selection\n→ mutation → validation → solidify);

    :Write Cycle Progress Atomically\n(gep_cycle_progress.json);

    if (ATP task pickup enabled?) then (yes)
        :Check for Available Hub Tasks\n(atpTaskPickup.pickOne);
    endif

    if (AutoBuy signal conditions met?) then (yes)
        :Place ATP Order\n(capabilities + budget from signals);
    endif

    :Calculate Next Sleep Duration\n(frequency × idle intensity\nfrom idleScheduler);

    :Sleep Until Next Cycle;

repeat while (Loop mode active?) is (yes)

:Release Process Lock;
stop

@enduml
```

```plantuml
@startuml evolver_gene_pipeline

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam activity {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam swimlane {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
}

title Evolver — Gene Evolution Pipeline (GEP)

|Signal Detection|
start

:Build Input Corpus\n(session transcript + today log\n+ memory snippet + user input);

:Extract Regex Signals\n(error/exception, timeout,\nloop patterns, API usage);

:Score Keyword Signals\n(per-signal profile matching\nwith occurrence counts);

if (LLM signal interval reached?) then (yes)
    :Extract LLM-Assisted Signals\n(POST to Hub LLM service\nwith truncated corpus);
endif

:Analyze Recent History\n(failure streaks, consecutive\nrepair count, recent intents);

:Merge and Filter Signals\n(dedup + apply history-based\nsuppressions);

|Gene Selection|
:Load Gene Library\n(genes.json + genes.jsonl\nfrom GEP assets directory);

if (Genes available?) then (yes)
    :Apply Policy Checks\n(blast radius limits, ethics\nconstraints, preconditions,\nbanned gene filter);
    :Score Genes by Signal Match\n(signal overlap × confidence\n× epigenetic modifiers);
    :Select Best Candidate Gene;
else (no)
    :Use Built-in Default Gene\n(gene_gep_repair_from_errors\nor gene_explore);
endif

|Mutation Generation|
:Capture Local State Awareness\n(node identity, env config,\nevolution dir, skills dir,\nrecent events summary);

:Write Prompt Artifact to Disk\n(gep_prompt_<cycleId>.json\ncontaining gene strategy\n+ context + principles);

:Spawn AI Agent Session\n(sessions_spawn call with\ntask description + answer\nfile path + capabilities);

:AI Agent Applies Code Changes\n(reads codebase files, edits,\nwrites answer to answer file);

:Capture Execution Trace\n(tools used, files changed,\nblast radius, error signatures);

|Validation|
:Build Validation Commands\n(from gene.validation list\n+ execution trace commands);

fork
    :Run Commands in Sandbox\n(isolated env, time-limited,\nAllowed executables only);
fork again
    :Verify Blast Radius Limits\n(file count + line count\nvs A2A_MAX_FILES / MAX_LINES);
end fork

:Build Validation Report\n(pass/fail per command\n+ failure classification\n+ env fingerprint);

if (All validations passed?) then (yes)

    |Solidification|
    :Commit Changes to Git\n(git commit with message);
    :Assemble Capsule Object\n(outcome, trace, signals,\nblast radius, success streak);
    :Upsert Gene\n(update learning_history\n+ success/fail counts);
    :Persist Capsule to Store\n(capsules.jsonl append);
    :Append Evolution Event\n(events.jsonl with run_id);

    if (Capsule broadcast eligible?) then (yes)
        fork
            :Publish Skill Markdown to Hub\n(geneToSkillMd →\nskillPublisher.publishSkillToHub);
        fork again
            :Publish A2A Bundle to Hub\n(gene + capsule bundle\nwith HMAC-SHA256 signature);
        end fork
    endif

    if (Self-PR conditions met?) then (yes)
        :Create GitHub Pull Request\n(selfPR.maybeCreatePR\nwith diff + capsule context);
    endif

    :Record Outcome: Success;

else (no)

    |Solidification|
    :Rollback Git Changes\n(stash or hard reset\nper EVOLVER_ROLLBACK_MODE);
    :Persist Failed Capsule\n(failed_capsules.json);
    :Append Failed Evolution Event;
    :Increment Failure Signals\n(consecutive_failure_streak_N\nban_gene if repeat offender);

    if (Recurring error loop detected?) then (yes)
        :Create GitHub Issue\n(issueReporter.maybeReportIssue\nwith error signature + events);
    endif

    :Record Outcome: Failed;

endif

|Memory & Learning|
:Update Personality State\n(adjust trait scores based\non outcome + signals);
:Record to Memory Graph\n(memory_graph.jsonl);
:Update Narrative Memory\n(evolution_narrative.md);
:Append Reflection Log Entry\n(reflection_log.jsonl);

if (Issue reporting conditions met?) then (yes)
    :Trigger Issue Reporter;
endif

stop

@enduml
```

```plantuml
@startuml evolver_atp_lifecycle

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam activity {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam swimlane {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
}

title ATP (Agent Task Protocol) — Task Lifecycle

|Consumer (Buyer)|
start

if (AutoBuy triggered by evolution signals?) then (yes)
    :Check Daily Budget Ledger\n(ledger.spent vs dailyCap\nwith cold-start halving);
    if (Budget exhausted?) then (yes)
        :Skip — daily cap reached;
        stop
    endif
    :Compute Question Hash\n(_questionHash: caps+question);
    if (Recently ordered same hash?) then (yes)
        :Skip — duplicate within TTL;
        stop
    endif
    :Compose Question from Signals\n(questionComposer.compose\nwith template + capabilities);
else (manual CLI)
    :Parse CLI Buy Args\n(evolver atp buy <caps>\n--budget --routing --question);
endif

:Register with Hub if needed\n(sendHelloToHub → node_id\n+ node_secret);

:Place Order on Hub\n(POST /a2a/atp/order\ncapabilities + budget\n+ routing + question);

:Record Spend in Ledger\n(dedup entry + spent amount);

|Hub|
:Validate Order\n(policy checks, balance);
:Route to Matching Merchants\n(directory search by capability\n+ tier + routing mode);
:Assign Task with Bounty;
:Return Order ID;

|Merchant (Seller)|
:List My Available Tasks\n(hubClient.listMyTasks);

:Score and Select Best Task\n(selectBestTask:\ncapability match + bounty\n+ difficulty estimate);

if (Eligible task found?) then (yes)
    :Check Spawn Cooldown\n(_recentlySpawned in ledger\nSPAWN_COOLDOWN_MS);
    :Clip Question to Max Length\n(_clipQuestion MAX_ANSWER_PROMPT_CHARS);
    :Build Spawn Task Prompt\n(task title + clipped question\n+ capabilities + answer path);
    :Record Spawn in Ledger\n(task_id → timestamp);
    :Spawn AI Agent Session\n(renderSessionsSpawnCall\nwith task + answer file);

    :AI Agent Processes Task\n(reads question + codebase\ncontext, generates answer);
    :Write Answer to File\n(atp_answer_<safe_taskId>.md);

    :Execute completeAtpTask;

    fork
        :Build Gene from Answer\n(capabilities + signals\n+ strategy steps from answer);
    fork again
        :Build Capsule from Answer\n(confidence=0.9, blast radius\nfrom answer stats);
    end fork

    :Ensure Node Secret Present\n(_ensureNodeSecret\n→ sendHelloToHub if missing);

    :Sign Bundle with HMAC-SHA256\n([gene.asset_id + capsule.asset_id]\nsorted + joined + signed);

    :Publish Bundle to Hub\n(POST /a2a/publish\nwith nonce + timestamp);

    :Complete Task on Hub\n(POST /a2a/task/complete\nwith result_asset_id);

    :Remove Task from Spawn Ledger\n(atpTaskPickup.forget);

else (no task)
    :No eligible task — return null;
    stop
endif

|Hub|
:Verify Delivery Proof\n(validate HMAC + asset exists);
:Record Proof Entry;
:Notify Consumer of Delivery;

|Consumer (Buyer)|
:Receive Delivery Notification\n(inbound message from hub);

if (Verify mode = auto?) then (yes)
    :Auto-confirm Delivery\n(confirmDelivery);
elseif (Verify mode = ai_judge?) then (ai judge)
    :Request AI Judge Review\n(requestAiJudge);
    :AI assesses result quality;
    :Auto-confirm if passed;
else (bilateral)
    :Consumer Reviews Result;
    if (Satisfied with delivery?) then (yes)
        :Confirm Delivery;
    else (no)
        :Dispute Order\n(disputeOrder with reason);
    endif
endif

:Settle Order\n(settleOrder → release payment\nto merchant on Hub);

:Ingest Received Assets via A2A\n(a2a.lowerConfidence factor=0.6\n+ validate + isAllowedA2AAsset);

:Append to External Candidates\n(external_candidates.jsonl);

:Update Memory Graph\n(recordExternalCandidate);

stop

@enduml
```
