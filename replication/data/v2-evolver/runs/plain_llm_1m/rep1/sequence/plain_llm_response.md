Looking at the IR for the **evolver** project, I can identify several key interaction flows: the evolution pipeline loop, the A2A proxy/heartbeat cycle, the ATP task lifecycle, the mailbox message routing, and the WebUI observer data flow. I'll generate focused sequence diagrams for each.

```plantuml
@startuml evolver_main_loop
skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceParticipant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #6C757D
    GroupBackgroundColor #E9ECEF
    GroupBorderColor #6C757D
}

title Evolver — Main Evolution Loop

participant "index.js\n(main)" as Main
participant "pipeline/\ncollect.js" as Collect
participant "pipeline/\nselect.js" as Select
participant "pipeline/\nenrich.js" as Enrich
participant "pipeline/\nhub.js" as HubPipe
participant "pipeline/\ndispatch.js" as Dispatch
participant "gep/\nsolidify.js" as Solidify
participant "gep/\nmemoryGraph.js" as Memory
participant "gep/\nassetStore.js" as Store

Main -> Main: acquireLock()
activate Main

loop Each Evolution Cycle
  Main -> Collect: collectSignals\n(transcript, log, memory)
  activate Collect
  Collect -> Memory: readAllEvents()
  activate Memory
  Memory --> Collect: recentEvents[]
  deactivate Memory
  Collect -> Collect: extractSignals()
  Collect --> Main: signals[]
  deactivate Collect

  Main -> Select: selectGene(signals, genes)
  activate Select
  Select -> Store: loadGenes()
  activate Store
  Store --> Select: genes[]
  deactivate Store
  Select --> Main: selectedGene
  deactivate Select

  Main -> Enrich: enrichContext\n(gene, signals, events)
  activate Enrich
  Enrich --> Main: enrichedContext
  deactivate Enrich

  Main -> HubPipe: checkHubCandidates(signals)
  activate HubPipe
  HubPipe --> Main: externalCandidates[]
  deactivate HubPipe

  Main -> Dispatch: dispatchMutation\n(gene, context)
  activate Dispatch
  Dispatch --> Main: mutationResult
  deactivate Dispatch

  alt Mutation Succeeded
    Main -> Solidify: solidify(mutationResult)
    activate Solidify
    Solidify -> Store: appendCapsule(capsule)
    Solidify --> Main: solidified
    deactivate Solidify
    Main -> Store: appendEventJsonl(EvolutionEvent)
  else Mutation Failed
    Main -> Store: appendFailedCapsule(capsule)
    Main -> Store: appendEventJsonl(EvolutionEvent\nstatus=failed)
  end
end

Main -> Main: releaseLock()
deactivate Main

@enduml
```

```plantuml
@startuml evolver_proxy_startup_heartbeat
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceParticipant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #6C757D
    GroupBackgroundColor #E9ECEF
    GroupBorderColor #6C757D
}

title EvoMapProxy — Startup, Registration & Heartbeat

participant "evolver\n(main)" as Evolver
participant "EvoMapProxy\n(proxy/index.js)" as Proxy
participant "LifecycleManager\n(lifecycle/manager.js)" as LC
participant "SyncEngine\n(sync/engine.js)" as Sync
participant "InboundSync\n(sync/inbound.js)" as IB
participant "OutboundSync\n(sync/outbound.js)" as OB
participant "ProxyHttpServer\n(server/http.js)" as Server
participant "TaskMonitor\n(task/monitor.js)" as TM
participant "A2A Hub" as Hub

Evolver -> Proxy: start()
activate Proxy

Proxy -> LC: hello()
activate LC
LC -> LC: _buildHeaders()\n[X-Request-Id, Auth]
LC -> Hub: POST /a2a/hello\n{node_id, version,\n env_fingerprint}
activate Hub
Hub --> LC: {node_secret, hub_version,\n capabilities}
deactivate Hub
LC -> LC: store.setState(node_secret)
LC --> Proxy: helloResult {ok, limited?}
deactivate LC

Proxy -> Server: new ProxyHttpServer(routes)
activate Server
Server -> Server: tryListen(port)
Server --> Proxy: {url, token}
deactivate Server

Proxy -> TM: new TaskMonitor()
activate TM
TM -> TM: _restoreStats()
TM --> Proxy: taskMonitor
deactivate TM

Proxy -> Sync: new SyncEngine()
activate Sync
Sync -> OB: new OutboundSync()
Sync -> IB: new InboundSync()
Sync -> Sync: start()

loop Outbound Flush (periodic)
  Sync -> OB: flush()
  activate OB
  OB -> OB: store.pollOutbound()
  OB -> Hub: POST /a2a/mailbox/send\n{messages[]}
  Hub --> OB: {accepted_ids[]}
  OB -> OB: store.updateStatusBatch()
  deactivate OB
end

loop Inbound Pull (periodic)
  Sync -> IB: pull()
  activate IB
  IB -> Hub: GET /a2a/mailbox/poll\n?cursor=&node_id=
  Hub --> IB: {messages[], next_cursor}
  IB -> IB: store.writeInboundBatch(messages)
  deactivate IB
end
deactivate Sync

loop Heartbeat Loop
  Proxy -> LC: startHeartbeatLoop(intervalMs)
  activate LC
  LC -> TM: getHeartbeatMeta()
  activate TM
  TM --> LC: {pending, metrics}
  deactivate TM
  LC -> Hub: POST /a2a/heartbeat\n{events, task_metrics,\n env_fingerprint}
  Hub --> LC: {pending_messages[],\n force_upgrade?}
  LC -> LC: store.writeInboundBatch()
  deactivate LC
end

Proxy --> Evolver: {url, port, token}
deactivate Proxy

@enduml
```

```plantuml
@startuml evolver_atp_task_lifecycle
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceParticipant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #6C757D
    GroupBackgroundColor #E9ECEF
    GroupBorderColor #6C757D
}

title ATP (Agent Task Protocol) — Full Task Lifecycle

actor "Evolution\nLoop" as Evo
participant "autoBuyer.js" as AB
participant "consumerAgent.js" as CA
participant "hubClient.js" as HC
participant "A2A Hub" as Hub
participant "atpTaskPickup.js" as Pickup
participant "merchantAgent.js" as MA
participant "atpExecute.js" as Execute
participant "assetStore.js" as Store

== Auto-Buy: Place Order ==
Evo -> AB: considerOrder({capabilities, signals})
activate AB
AB -> AB: _readLedger() / _rotateIfNewDay()
AB -> AB: _questionHash(opts) [dedup check]
AB -> CA: orderAndWait({capabilities,\n budget, question})
activate CA
CA -> HC: placeOrder({capabilities, budget,\n routing_mode})
activate HC
HC -> Hub: POST /a2a/atp/order
Hub --> HC: {order_id, status: "placed"}
HC --> CA: orderResult
deactivate HC
CA --> AB: {ok, order_id}
deactivate CA
AB -> AB: _writeLedger() [track spend]
AB --> Evo: {ok, order_id}
deactivate AB

== Merchant: Pick Up Task ==
MA -> Pickup: pickOne({limit})
activate Pickup
Pickup -> HC: listMyTasks(limit)
activate HC
HC -> Hub: GET /a2a/task/my?node_id=
Hub --> HC: {tasks[]}
HC --> Pickup: tasks[]
deactivate HC
Pickup -> Pickup: _isEligible(task)
Pickup -> Pickup: _buildSpawnTask(task)
Pickup --> MA: spawnTask {task_id, question, tools}
deactivate Pickup

MA -> MA: processTask(spawnTask)
note right: Agent executes work,\nwrites answer to file

== Merchant: Complete Task ==
MA -> Execute: completeAtpTask({taskId,\n orderId, answerFile})
activate Execute
Execute -> Execute: _readAnswer(answerFile)
Execute -> Execute: _ensureNodeSecret()
Execute -> Execute: _buildGene(capabilities, signals)
Execute -> Execute: _buildCapsule()
Execute -> Execute: _publishBundle(gene, capsule)
Execute -> Hub: POST /a2a/publish\n{gene, capsule, signature}
Hub --> Execute: {asset_id, published}
Execute -> Execute: _completeTaskOnHub(taskId, assetId)
Execute -> Hub: POST /a2a/task/complete\n{task_id, asset_id}
Hub --> Execute: {ok: true, credits_held}
Execute -> Execute: submitDelivery(orderId, proofPayload)
Execute -> Hub: POST /a2a/atp/deliver\n{order_id, proof}
Hub --> Execute: {ok: true}
Execute --> MA: {ok: true, stage: "delivered"}
deactivate Execute

MA -> Store: appendCapsule(capsule)

== Consumer: Verify Delivery ==
Evo -> CA: confirmDelivery(orderId)
activate CA
CA -> HC: verifyDelivery(orderId, "confirm")
activate HC
HC -> Hub: POST /a2a/atp/verify\n{order_id, action: "confirm"}
Hub --> HC: {verified, credits_released}
HC --> CA: verifyResult
deactivate HC
CA --> Evo: {ok: true}
deactivate CA

@enduml
```

```plantuml
@startuml evolver_mailbox_routing
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceParticipant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #6C757D
    GroupBackgroundColor #E9ECEF
    GroupBorderColor #6C757D
}

title Proxy Mailbox — Inbound & Outbound Message Routing

participant "A2A Hub\n(heartbeat)" as Hub
participant "LifecycleManager\n(heartbeat)" as LC
participant "MailboxStore\n(mailbox/store.js)" as Store
participant "ProxyHttpServer\n(server/http.js)" as Server
participant "DmHandler /\nSessionHandler" as Ext
participant "evolver\nconsumer" as Evo
participant "OutboundSync\n(sync/outbound.js)" as OB

== Inbound: Hub → Consumer ==
Hub -> LC: pending_messages[]\n(in heartbeat response)
activate LC
LC -> Store: writeInboundBatch(messages)
activate Store
loop each message
  Store -> Store: _appendMessage(msg)\n[assign UUIDv7, timestamp]
  Store -> Store: _inbound.push(id)
end
Store --> LC: accepted_ids[]
deactivate Store
deactivate LC

Evo -> Server: POST /mailbox/poll\n{type?, limit}
activate Server
Server -> Store: poll({limit, type})
activate Store
Store -> Store: walk _inbound queue\n(status: pending)
Store --> Server: messages[]
deactivate Store
Server --> Evo: {messages[]}
deactivate Server

Evo -> Ext: process messages\n(DM, session invites, etc.)
activate Ext
Ext --> Evo: handled
deactivate Ext

Evo -> Server: POST /mailbox/ack\n{messageIds[]}
activate Server
Server -> Store: ack(messageIds)
activate Store
loop each id
  Store -> Store: _appendUpdate(id,\n{status: "acked"})
  Store -> Store: _evictFromIndex(id)
end
Store --> Server: ok
deactivate Store
Server --> Evo: {ok: true, acked: n}
deactivate Server

== Outbound: Consumer → Hub ==
Evo -> Server: POST /mailbox/send\n{type, payload}
activate Server
Server -> Store: send({type, payload})
activate Store
Store -> Store: safeParse(payload)
Store -> Store: _appendMessage(msg)\n[_outbound.push(id)]
Store --> Server: {id}
deactivate Store
Server --> Evo: {id}
deactivate Server

OB -> Store: pollOutbound()
activate Store
Store -> Store: sort by priority/timestamp\n(pending outbound)
Store --> OB: pendingMessages[]
deactivate Store

OB -> Hub: POST /a2a/mailbox/send\n{messages[]}
activate Hub
Hub --> OB: {accepted_ids[], rejected_ids[]}
deactivate Hub

OB -> Store: updateStatusBatch\n([{id, status: "sent"}])
activate Store
Store -> Store: _appendUpdate() per id
Store -> Store: _evictFromIndex() [terminal]
deactivate Store

@enduml
```

```plantuml
@startuml evolver_webui_observer
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceParticipant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #6C757D
    GroupBackgroundColor #E9ECEF
    GroupBorderColor #6C757D
}

title WebUI — Observer Data Request Flow

actor "Browser" as Browser
participant "WebUiServer\n(webui/server/http.js)" as WS
participant "routes.js" as Routes
participant "observer/\nstatus.js" as Status
participant "observer/\nassets.js" as Assets
participant "observer/\nruns.js" as Runs
participant "observer/\ninteractions.js" as Inter
participant "observer/\njsonl.js" as JSONL
database "File System\n(evolution/*.json\nassets/gep/*.jsonl)" as FS

Browser -> WS: GET /webui/status
activate WS
WS -> WS: _handle(req, res)
WS -> Routes: matchRoute("GET", "/webui/status")
Routes --> WS: handler
WS -> Status: getStatus()
activate Status
Status -> FS: readJsonSafe(cycleProgressPath)
Status -> FS: readJsonSafe(solidifyStatePath)
Status -> Status: inferMode(cycle, solidify, proxy)
Status -> Status: getProxySettings()
Status -> Status: getSafetyState()
Status --> WS: {mode, cycle, proxy,\n safety, filesPresent}
deactivate Status
WS --> Browser: 200 JSON
deactivate WS

Browser -> WS: GET /webui/assets
activate WS
WS -> Assets: getAssetOverview()
activate Assets
Assets -> JSONL: readGenes(paths)
activate JSONL
JSONL -> FS: readFileSync(genes.json)
JSONL -> FS: readFileSync(genes.jsonl)
JSONL --> Assets: genes[]
deactivate JSONL
Assets -> JSONL: readCapsules(paths)
Assets -> JSONL: readJsonl(eventsPath\n[last 200])
Assets -> Assets: dedupeById(),\n countBy(category)
Assets --> WS: {genes, capsules,\n events, assetCalls}
deactivate Assets
WS --> Browser: 200 JSON
deactivate WS

Browser -> WS: GET /webui/runs
activate WS
WS -> Runs: listRuns()
activate Runs
Runs -> Runs: buildRuns()
Runs -> JSONL: readJsonSafe(cycleProgressPath)
Runs -> JSONL: readJsonl(eventsPath)
Runs -> JSONL: readPipelineEvents()
loop each event/pipelineEvent
  Runs -> Runs: summaryFromEvent(ev)\n/ summaryFromPipelineEvent(ev)
  Runs -> Runs: mergeRun(runs, summary)
end
Runs -> Runs: sort by updatedAt DESC
Runs -> JSONL: paginate(runs, query)
Runs --> WS: {items[], total, cursor}
deactivate Runs
WS --> Browser: 200 JSON
deactivate WS

Browser -> WS: GET /webui/interactions
activate WS
WS -> Inter: getInteractions()
activate Inter
Inter -> Inter: getProxySettings()
Inter -> JSONL: readMailboxMessages(query)
activate JSONL
JSONL -> FS: readJsonl(messages.jsonl)
JSONL --> Inter: messages[]
deactivate JSONL
Inter -> Inter: normalizeMessage()\n filterMessages()
Inter --> WS: {proxy, mailbox:\n{items[], total}}
deactivate Inter
WS --> Browser: 200 JSON
deactivate WS

@enduml
```

```plantuml
@startuml evolver_skill_distillation
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceParticipant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #6C757D
    GroupBackgroundColor #E9ECEF
    GroupBorderColor #6C757D
}

title Skill Distillation & Hub Publication Pipeline

participant "skillDistiller.js" as Distiller
participant "skill2gep.js" as S2G
participant "skillPublisher.js" as Publisher
participant "assetStore.js" as Store
participant "a2a.js" as A2A
participant "A2A Hub" as Hub

note over Distiller: Triggered after\nsuccessful solidify

Distiller -> Distiller: readEvolutionEvents()
Distiller -> Distiller: analyzePatterns(events)

loop each promising pattern
  Distiller -> S2G: runOnSkillInvocation\n({skillPath, execution})
  activate S2G
  S2G -> S2G: parseSkillMd(skillMd)
  S2G -> S2G: synthesizeGene(parsed, execution)
  S2G -> S2G: detectForgery(execution)
  S2G -> S2G: assembleCapsule(gene, execution)
  
  S2G -> Store: upsertGene(gene)
  activate Store
  Store -> Store: validateGene(gene)
  Store -> Store: ensureSchemaFields(gene)
  Store -> Store: withFileLock(genesPath, fn)
  Store --> S2G: saved
  deactivate Store
  
  S2G -> Store: appendCapsule(capsule)
  
  S2G -> S2G: publishAssets(gene, capsule)
  S2G -> Publisher: publishSkillToHub(gene)
  activate Publisher
  Publisher -> Publisher: geneToSkillMd(gene)
  Publisher -> Publisher: sanitizeSkillName(rawName)
  Publisher -> Hub: POST /a2a/skill/publish\n{content: skillMd,\n tags: signals_match}
  Hub --> Publisher: {skill_id, published}
  Publisher --> S2G: {ok, skill_id}
  deactivate Publisher
  
  S2G -> A2A: buildPublishBundle(gene, capsule)
  S2G -> A2A: httpTransportSend(message)
  A2A -> Hub: POST /a2a/publish\n{gene, capsule, signature}
  Hub --> A2A: {asset_id}
  
  S2G --> Distiller: {ok, geneId, capsuleId}
  deactivate S2G
end

@enduml
```
