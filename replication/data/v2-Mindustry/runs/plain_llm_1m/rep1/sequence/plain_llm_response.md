Looking at the Mindustry IR, I'll generate three focused sequence diagrams covering the most important runtime flows: game initialization, multiplayer connection, and the wave spawning + unit AI update cycle.

```plantuml
@startuml GameInitialization
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam participant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #495057
    BoxBackgroundColor #E9ECEF
    BoxBorderColor #6C757D
}

title Mindustry – Game Initialization Sequence

actor "OS / JVM" as os
participant "ClientLauncher\n(ApplicationCore)" as launcher
participant "Vars" as vars
participant "ContentLoader" as content
participant "Control" as ctrl
participant "Logic" as logic
participant "Renderer" as renderer
participant "UI" as ui
participant "NetClient\n/ NetServer" as net

os -> launcher: init()
activate launcher
launcher -> launcher: setup()

launcher -> vars: init()
activate vars
vars -> vars: loadSettings()
vars -> vars: loadFileLogger()
vars -> vars: init file directories
vars --> launcher : settings ready
deactivate vars

launcher -> content: new ContentLoader()
activate content
content -> content: createBaseContent()\n(Blocks, Items, Bullets, Units…)
launcher -> content: createModContent()
content --> launcher: content registered
deactivate content

launcher -> content: init()
activate content
content -> content: initialize() all Content objects
content -> content: postInit() all Content objects
content --> launcher: content initialised
deactivate content

launcher -> ctrl: new Control()
activate ctrl
ctrl -> ctrl: new Saves()
ctrl -> ctrl: new SoundControl()
ctrl -> ctrl: new InputHandler (Desktop/Mobile)
ctrl --> launcher
deactivate ctrl

launcher -> logic: new Logic()
activate logic
logic -> logic: register event listeners\n(TileChange, BlockBuild…)
logic --> launcher
deactivate logic

launcher -> renderer: new Renderer()
activate renderer
renderer -> renderer: new BlockRenderer / FloorRenderer
renderer -> renderer: new MinimapRenderer
renderer -> renderer: new PlanetRenderer
renderer --> launcher
deactivate renderer

launcher -> ui: new UI()
activate ui
ui -> ui: loadAsync() – pack fonts & textures
ui -> ui: loadSync() – build dialogs\n(PlanetDialog, ResearchDialog…)
ui --> launcher
deactivate ui

launcher -> net: new NetClient()\nnew NetServer()
activate net
net --> launcher
deactivate net

launcher -> launcher: add(Control, Logic, Renderer, UI…)
launcher --> os: application ready / show main menu
deactivate launcher

@enduml
```

```plantuml
@startuml MultiplayerConnection
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam participant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #495057
    BoxBackgroundColor #E9ECEF
    BoxBorderColor #6C757D
}

title Mindustry – Multiplayer Client-Server Connection Sequence

actor "Player\n(client side)" as player
box "Client" #E9ECEF
  participant "NetClient" as nc
  participant "ArcNetProvider\n(Client)" as arcC
end box
box "Network" #F8F9FA
  participant "TCP/IP" as tcp
end box
box "Server" #E9ECEF
  participant "ArcNetProvider\n(Server)" as arcS
  participant "NetServer" as ns
  participant "Administration" as admin
  participant "World" as world
end box

== Initiate Connection ==

player -> nc: connect(ip, port)
activate nc
nc -> arcC: connectClient(ip, port, onSuccess)
activate arcC
arcC -> tcp: TCP handshake
tcp --> arcC: connected
arcC --> nc: success callback
deactivate arcC
nc -> nc: beginConnecting()

nc -> arcC: send(ConnectPacket {version, uuid, mods, name})
arcC -> tcp: transmit
deactivate nc

== Server Validates Handshake ==

tcp -> arcS: received ConnectPacket
arcS -> ns: handleServerReceived(connection, ConnectPacket)
activate ns
ns -> admin: updatePlayerJoined(uuid, ip, name)
activate admin
admin -> admin: check bans / whitelist / version
admin --> ns: PlayerInfo
deactivate admin

alt Player rejected
  ns -> arcS: kick(KickReason)
  arcS -> tcp: KickPacket
else Player accepted
  ns -> ns: assignTeam(player, players)
  ns -> ns: connectConfirm(player)
  ns -> ns: sendWorldData(player)
  activate ns
    ns -> world: writeWorldData(player, outputStream)
    activate world
    world -> world: writeEntities, buildingSnapshots\nteamBlocks, customChunks
    world --> ns: worldStream (StreamBegin + StreamChunks)
    deactivate world
    ns -> arcS: send(WorldStream chunks)
  deactivate ns
end
deactivate ns

== Client Receives & Loads World ==

arcC -> nc: received(StreamBegin)
activate nc
nc -> nc: worldDataBegin() – set quiet mode
loop stream chunks
  arcC -> nc: received(StreamChunk)
  nc -> nc: buffer chunk bytes
end

nc -> nc: all chunks received
nc -> world: loadWorld(inputStream)
activate world
world -> world: readMap(tiles, floors, walls)
world -> world: readEntities(units, buildings)
world -> world: readTeamBlocks()
world --> nc: world loaded
deactivate world

nc -> nc: finishConnecting()
nc -> nc: setClientLoaded(true)
nc --> player: connected — game starts
deactivate nc

@enduml
```

```plantuml
@startuml WaveSpawningAndUnitAI
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam participant {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
}
skinparam sequence {
    ArrowColor #495057
    LifeLineBorderColor #495057
    BoxBackgroundColor #E9ECEF
    BoxBorderColor #6C757D
}

title Mindustry – Enemy Wave Spawning & Unit AI Update Sequence

participant "Logic\n(game loop)" as logic
participant "WaveSpawner" as spawner
participant "SpawnGroup" as sg
participant "UnitType" as ut
participant "UnitComp\n(enemy unit)" as unit
participant "FlyingAI /\nGroundAI" as ai
participant "Units\n(query)" as units
participant "Pathfinder\n(flow fields)" as path
participant "WeaponsComp" as weapons
participant "Bullet" as bullet

== Wave Trigger (Logic.runWave) ==

logic -> logic: runWave()
activate logic
logic -> spawner: spawnEnemies()
activate spawner

loop for each SpawnGroup in rules.spawns
  spawner -> sg: getSpawned(currentWave)
  activate sg
  sg --> spawner: unitCount
  deactivate sg

  loop unitCount times
    spawner -> sg: createUnit(team, x, y, rotation, wave, cons)
    activate sg
    sg -> ut: spawn(team, x, y)
    activate ut
    ut -> ut: create(team) — instantiate Unit entity
    ut -> unit: new Unit via constructor
    activate unit
    unit -> unit: setType(type)
    unit -> unit: setupWeapons(type)
    unit -> unit: controller = aiController.get()\n(FlyingAI / GroundAI)
    unit -> unit: add() — register in Groups
    deactivate unit
    ut --> sg: unit
    deactivate ut
    sg --> spawner: unit ready
    deactivate sg

    spawner -> spawner: spawnEffect(unit)
  end
end

spawner --> logic
deactivate spawner
deactivate logic

== AI Update Loop (per game tick) ==

logic -> unit: update()
activate unit
unit -> unit: check drownFloor(), updateBoosting()

unit -> ai: updateUnit()
activate ai

group Targeting
  ai -> ai: retarget() — timer expired?
  ai -> units: closestTarget(team, x, y, range,\n  unitPred, tilePred)
  activate units
  units -> units: query Groups.unit quadtree
  units -> units: query BlockIndexer flagged buildings
  units --> ai: target (Unit or Building)
  deactivate units
  ai -> ai: target = result
end

group Movement
  alt Flying unit (FlyingAI)
    ai -> ai: moveToward(target)\nusing circle/direct approach
  else Ground unit (GroundAI)
    ai -> path: getTargetTile(tile, flowfield)
    activate path
    path -> path: look up pre-computed\nflow field weights
    path --> ai: nextTile
    deactivate path
    ai -> unit: move toward nextTile
  end
end

group Weapons
  ai -> ai: shouldShoot() — in range & cone?
  alt can fire
    ai -> weapons: controlWeapons(rotate=true, shoot=true)
    activate weapons
    weapons -> weapons: aim(targetPos)
    weapons -> weapons: update mounts — check reload
    weapons -> bullet: BulletType.create(owner, team, x, y, angle)
    activate bullet
    bullet -> bullet: init velocity, trail, lifetime
    bullet --> weapons: bullet active
    deactivate bullet
    weapons --> ai
    deactivate weapons
  end
end

ai --> unit
deactivate ai
unit --> logic
deactivate unit

@enduml
```
