Looking at the Mindustry IR, I can identify the key actors and use cases from:
- **`Control.java`** — `playMap()`, `playSector()`, `createPlayer()`
- **`InputHandler.java`** — building, commanding units, mining, schematics
- **`UI.java`** — all dialog types reveal system capabilities
- **`NetServer.java`** / **`NetClient.java`** — multiplayer hosting/joining, admin functions
- **`ServerControl.java`** — headless server operations

I'll produce two focused use case diagrams: one for core gameplay and one for multiplayer/administration.

---

```plantuml
@startuml Mindustry_CoreGameplay
skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam usecase {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam actor {
    BackgroundColor #DDEEFF
    BorderColor #336699
    FontColor #003366
}
skinparam package {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
}

left to right direction

title Mindustry — Player Gameplay Use Cases

actor "Player" as player

rectangle "Mindustry Game" {

    package "Game Entry" {
        usecase "Play Campaign" as UC_Campaign
        usecase "Play Custom Game" as UC_Custom
        usecase "Edit Map\nin Map Editor" as UC_MapEdit
        usecase "Save / Load\nGame State" as UC_SaveLoad
    }

    package "Campaign Flow" {
        usecase "Navigate\nPlanet Map" as UC_Planet
        usecase "Land on Sector\n& Start Mission" as UC_Land
        usecase "Survive\nEnemy Waves" as UC_Waves
        usecase "Complete\nMap Objectives" as UC_Objectives
        usecase "Launch to\nAnother Sector" as UC_LaunchSector
        usecase "Research\nContent" as UC_Research
    }

    package "Base Building" {
        usecase "Place Structure" as UC_Place
        usecase "Demolish Structure" as UC_Break
        usecase "Configure Block\nSettings" as UC_Config
        usecase "Apply Blueprint\n(Schematic)" as UC_Schematic
        usecase "Create Blueprint" as UC_CreateSchem
    }

    package "Unit Commanding" {
        usecase "Directly Pilot\na Unit" as UC_Pilot
        usecase "Select &\nCommand Units" as UC_Command
        usecase "Order Move\nto Position" as UC_Move
        usecase "Order Attack\nTarget" as UC_Attack
        usecase "Assign Build /\nRepair Task" as UC_BuildTask
        usecase "Assign\nMining Task" as UC_MineTask
    }

    package "Content & Settings" {
        usecase "Browse\nContent Database" as UC_Database
        usecase "Manage Mods" as UC_Mods
        usecase "Adjust Settings\n& Keybindings" as UC_Settings
    }
}

' Game entry
player --> UC_Campaign
player --> UC_Custom
player --> UC_MapEdit
player --> UC_SaveLoad
player --> UC_Research
player --> UC_Database
player --> UC_Mods
player --> UC_Settings

' Campaign use case relationships
UC_Campaign ..> UC_Planet : <<include>>
UC_Planet ..> UC_Land : <<extend>>
UC_Land ..> UC_Waves : <<include>>
UC_Land ..> UC_Objectives : <<include>>
UC_Objectives ..> UC_LaunchSector : <<extend>>

' Building use cases
player --> UC_Place
player --> UC_Break
player --> UC_Config
player --> UC_Schematic
UC_Schematic ..> UC_CreateSchem : <<extend>>

' Unit use cases
player --> UC_Pilot
player --> UC_Command
UC_Command ..> UC_Move : <<include>>
UC_Command ..> UC_Attack : <<extend>>
UC_Command ..> UC_BuildTask : <<extend>>
UC_Command ..> UC_MineTask : <<extend>>

@enduml
```

---

```plantuml
@startuml Mindustry_MultiplayerAdmin
skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam usecase {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam actor {
    BackgroundColor #DDEEFF
    BorderColor #336699
    FontColor #003366
}
skinparam package {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
}

left to right direction

title Mindustry — Multiplayer & Server Administration Use Cases

actor "Player" as player
actor "Server Admin" as admin
actor "Headless\nServer" as server

rectangle "Mindustry Multiplayer & Server" {

    package "Session Connection" {
        usecase "Host Multiplayer\nGame" as UC_Host
        usecase "Join Server\nby IP / Browser" as UC_Join
        usecase "Discover\nLocal Servers" as UC_Discover
        usecase "Browse Online\nServer List" as UC_Browse
    }

    package "In-Game Social" {
        usecase "Send Chat\nMessage" as UC_Chat
        usecase "Ping Location\non Minimap" as UC_Ping
        usecase "View Connected\nPlayers" as UC_PlayerList
        usecase "Vote to Kick\nDisruptive Player" as UC_VoteKick
    }

    package "Player Moderation" {
        usecase "Kick Player" as UC_Kick
        usecase "Ban Player\nby UUID" as UC_BanUUID
        usecase "Ban Player\nby IP" as UC_BanIP
        usecase "Unban Player" as UC_Unban
        usecase "Trace Player\nNetwork Info" as UC_Trace
        usecase "Grant Admin\nPrivileges" as UC_Grant
        usecase "Manage\nWhitelist" as UC_Whitelist
    }

    package "Game Control" {
        usecase "Configure\nGame Rules" as UC_Rules
        usecase "Load / Change\nMap" as UC_ChangeMap
        usecase "Skip Current\nWave" as UC_Skip
        usecase "Pause / Resume\nGame" as UC_Pause
        usecase "Set Gamemode\n(Survival / PvP / etc.)" as UC_Gamemode
    }

    package "Server Operations\n(Headless)" {
        usecase "Execute Console\nCommands" as UC_Console
        usecase "Configure Server\nProperties" as UC_ServerConfig
        usecase "Auto-save Game\nState" as UC_Autosave
        usecase "Log Game\nActivity" as UC_Log
    }
}

' Player connections
player --> UC_Join
player --> UC_Discover
player --> UC_Browse
player --> UC_Chat
player --> UC_Ping
player --> UC_PlayerList
player --> UC_VoteKick

' Admin connections
admin --> UC_Host
admin --> UC_Kick
admin --> UC_BanUUID
admin --> UC_BanIP
admin --> UC_Unban
admin --> UC_Trace
admin --> UC_Grant
admin --> UC_Whitelist
admin --> UC_Rules
admin --> UC_ChangeMap
admin --> UC_Skip
admin --> UC_Pause
admin --> UC_Gamemode
admin --> UC_Chat
admin --> UC_Join

' Headless server connections
server --> UC_Host
server --> UC_Console
server --> UC_ServerConfig
server --> UC_Autosave
server --> UC_Log
server --> UC_Rules
server --> UC_ChangeMap
server --> UC_Skip
server --> UC_Kick
server --> UC_BanUUID

' Relationships between use cases
UC_Host ..> UC_Rules : <<include>>
UC_VoteKick ..> UC_Kick : <<extend>>
UC_Join ..> UC_Discover : <<extend>>
UC_BanUUID ..> UC_Trace : <<include>>
UC_Host ..> UC_ChangeMap : <<extend>>

@enduml
```
