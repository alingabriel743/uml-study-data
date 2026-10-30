Looking at the IR, I can identify the key actors and use cases for the **evolver** project — a self-evolving AI agent system with an evolution engine, ATP marketplace, proxy communication layer, platform hook adapters, and a web UI. Let me produce a focused use case diagram.

```plantuml
@startuml

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 11
skinparam backgroundColor #FEFEFE
skinparam usecase {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam rectangle {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
}
skinparam actor {
    BackgroundColor #DEE2E6
    BorderColor #495057
    FontColor #212529
}
skinparam package {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
}

title Evolver — Use Case Diagram

left to right direction

'── Actors ─────────────────────────────────────────────
actor "Developer" as dev
actor "AI Agent\n(Claude / Codex\n/ Cursor / Kiro)" as ai_agent
actor "Web User" as web_user
actor "ATP Consumer" as consumer
actor "ATP Merchant" as merchant
actor "A2A Hub\n«external»" as hub

'── System boundary ─────────────────────────────────────
rectangle "Evolver System" {

    '── Platform Hooks ──────────────────────────────────
    usecase "Install / Uninstall\nPlatform Hooks" as UC_HOOKS
    usecase "Inject Evolution Memory\nat Session Start" as UC_INJECT
    usecase "Detect Evolution\nSignals in Session" as UC_DETECT
    usecase "Record Session Outcome\nto Memory Graph" as UC_OUTCOME

    '── Evolution Engine ────────────────────────────────
    usecase "Run Evolution Cycle" as UC_EVOLVE
    usecase "Select Gene Strategy" as UC_SELECT
    usecase "Apply Code Mutation" as UC_MUTATE
    usecase "Validate Mutated Code" as UC_VALIDATE
    usecase "Solidify Accepted Changes" as UC_SOLIDIFY
    usecase "Rollback Failed Changes" as UC_ROLLBACK
    usecase "Publish Skill to Hub" as UC_PUBLISH_SKILL

    '── ATP Marketplace ─────────────────────────────────
    usecase "Buy AI Service\n(Place Order)" as UC_BUY
    usecase "Sell AI Service\n(Publish Listing)" as UC_SELL
    usecase "Verify Service Delivery" as UC_VERIFY
    usecase "Auto-Buy Services" as UC_AUTOBUY
    usecase "Pick Up & Complete\nAssigned Tasks" as UC_PICKUP

    '── Proxy & Communication ───────────────────────────
    usecase "Connect via Proxy\nto A2A Hub" as UC_PROXY
    usecase "Exchange Mailbox Messages" as UC_MAILBOX
    usecase "Manage Collaborative\nAgent Sessions" as UC_SESSION

    '── Monitoring & Operations ─────────────────────────
    usecase "View Evolution Dashboard" as UC_DASHBOARD
    usecase "Browse Genes & Capsules" as UC_ASSETS
    usecase "View Run History" as UC_RUNS
    usecase "Start / Stop Evolver" as UC_LIFECYCLE
    usecase "Health Check & Self-Repair" as UC_HEALTH
    usecase "Generate Reports" as UC_REPORTS
    usecase "Monitor Installed Skills" as UC_SKILLS
}

'── Developer associations ──────────────────────────────
dev --> UC_HOOKS
dev --> UC_EVOLVE
dev --> UC_LIFECYCLE
dev --> UC_HEALTH
dev --> UC_REPORTS
dev --> UC_SKILLS

'── AI Agent associations ───────────────────────────────
ai_agent --> UC_INJECT
ai_agent --> UC_DETECT
ai_agent --> UC_OUTCOME

'── Web User associations ───────────────────────────────
web_user --> UC_DASHBOARD
web_user --> UC_ASSETS
web_user --> UC_RUNS

'── ATP Consumer associations ───────────────────────────
consumer --> UC_BUY
consumer --> UC_VERIFY
consumer --> UC_AUTOBUY

'── ATP Merchant associations ───────────────────────────
merchant --> UC_SELL
merchant --> UC_PICKUP

'── External Hub associations ───────────────────────────
UC_PROXY --> hub
hub --> UC_MAILBOX

'── <<include>> relationships ───────────────────────────
UC_EVOLVE ..> UC_SELECT   : <<include>>
UC_EVOLVE ..> UC_MUTATE   : <<include>>
UC_EVOLVE ..> UC_VALIDATE : <<include>>
UC_PROXY   ..> UC_MAILBOX  : <<include>>
UC_AUTOBUY ..> UC_BUY      : <<include>>

'── <<extend>> relationships ────────────────────────────
UC_MUTATE   ..> UC_SOLIDIFY      : <<extend>>
UC_MUTATE   ..> UC_PUBLISH_SKILL : <<extend>>
UC_VALIDATE ..> UC_ROLLBACK      : <<extend>>
UC_BUY      ..> UC_VERIFY        : <<extend>>
UC_PROXY    ..> UC_SESSION       : <<extend>>

@enduml
```

---

### Diagram Overview

| Area | Key Actors | Notable Relationships |
|---|---|---|
| **Platform Hooks** | AI Agent (Claude/Codex/Cursor/Kiro) | Hooks inject memory at session start and capture outcome signals |
| **Evolution Engine** | Developer (triggers cycle) | `RunEvolutionCycle` always includes gene selection, mutation, and validation; solidification/rollback extend mutation/validation respectively |
| **ATP Marketplace** | Consumer, Merchant | Auto-Buy includes Buy; Verify extends Buy (optional post-purchase step) |
| **Proxy & Comms** | A2A Hub (external) | Proxy always includes mailbox exchange; sessions are an optional extension |
| **Monitoring** | Web User, Developer | Dashboard, run history, skill reports are independently accessed |
