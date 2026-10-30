Looking at the Mindustry IR, I'll generate three focused activity diagrams covering the most important workflows: the game update loop, the wave spawning process, and the unit AI decision flow.

---

```plantuml
@startuml MindustryGameUpdateLoop
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
    DiamondBackgroundColor #E9ECEF
    DiamondBorderColor #6C757D
}
skinparam swimlaneBorderColor #6C757D
skinparam swimlaneTitleBackgroundColor #E9ECEF

title Mindustry Game Update Loop (Per Frame)

|Control|
start
:Application frame begins;
:Poll keyboard / mouse / touch input;
:Update camera pan and zoom;
:Process build plan queue;
:Handle unit command input;

|Logic|
if (Game state == playing?) then (yes)
    :Update all entity groups\n(Units, Bullets, Buildings, Effects);
    :Update active weather states;
    :Update fog-of-war dynamic thread;
    :Update map objectives;
    if (Wave timer elapsed?) then (yes)
        :Increment wave counter;
        :Fire WaveEvent;
        :WaveSpawner.spawnEnemies();
    else (no)
    endif
    :Update state.enemies count;
    if (Win or lose condition met?) then (yes)
        :gameOver(winner) → updateGameOver();
        :Fire GameOverEvent;
    else (no)
    endif
else (no / menu)
    :Universe.updateGlobal()\n(sector simulation);
endif

|AsyncCore|
fork
    :PhysicsProcess.process()\nResolve unit body collisions;
fork again
    :AvoidanceProcess.process()\nRebuild unit avoidance map;
end fork
:Wait for futures (complete());

|NetServer / NetClient|
if (Multiplayer session active?) then (yes)
    :Serialize entity snapshots;
    :Send snapshot packets to clients;
    :Receive and apply client packets\n(snapshot, plans, config);
    :Sync block states;
else (no / singleplayer)
endif

|Renderer|
:Draw floor tiles and terrain edges;
:Draw block shadows and cracks;
:Draw building layers (base → turret);
:Draw ground units;
:Draw power lines;
:Draw bullets and effects (bloom);
:Draw flying units;
:Draw fog of war overlay;
:Draw weather layer;
:Draw light pass (LightRenderer);
:Draw HUD, minimap, chat, UI;

|Control|
:Frame complete;
stop
@enduml
```

---

```plantuml
@startuml MindustryWaveSpawning
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
    DiamondBackgroundColor #E9ECEF
    DiamondBorderColor #6C757D
}

title Wave Spawning Process

start
:Logic.runWave() triggered\n(wave timer elapsed or /skipwave);
:Increment state.wave counter;
:Fire WaveEvent to all listeners;
:Fire TurnEvent every turnDuration ticks;
:Notify map objectives of new wave;

:WaveSpawner.spawnEnemies();

:Collect ground spawn tiles\n(countGroundSpawns);
:Collect flyer spawn positions\n(countFlyerSpawns);

if (Any spawn points exist?) then (yes)
else (no)
    :No spawns — skip wave;
    stop
endif

:Iterate rules.spawns (SpawnGroup list);

fork
    note left: Ground Spawns
    :eachGroundSpawn(filterPos, cons);
    :For each SpawnGroup at ground spawns;
    if (group.canSpawn(wave)?) then (yes)
        :count = group.getSpawned(wave);
        :shield = group.getShield(wave);
        :loop count times;
        :spawnUnit(group, x, y);
        :Apply group.effect (StatusEffect);
        :Apply shield multiplier;
        :Assign AI controller;
        :spawnEffect(unit);
    else (no)
    endif
fork again
    note right: Flyer Spawns
    :eachFlyerSpawn(filterPos, cons);
    :For each SpawnGroup (flyers);
    if (group.canSpawn(wave)?) then (yes)
        :Spawn above map bounds;
        :Set flying AI controller;
        :spawnEffect(x, y, rotation, type);
    else (no)
    endif
end fork

if (First spawn position found?) then (yes)
    :doShockwave(x, y)\n— screen shake + wave effect;
else (no)
endif

:Update state.enemies count;
:Logic.checkGameState() post-wave;

if (Win wave reached AND enemies == 0?) then (yes)
    :Sector captured\nsectorCapture();
else (no)
endif

stop
@enduml
```

---

```plantuml
@startuml MindustryUnitAIFlow
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
    DiamondBackgroundColor #E9ECEF
    DiamondBorderColor #6C757D
}

title Unit AI Controller Decision Flow

start
:AIController.updateUnit() called per tick;

if (Unit is alive and valid?) then (no)
    stop
else (yes)
endif

:updateTargeting();

if (Current target still valid\n(alive, in range, correct team)?) then (no)
    :Invalidate target;
    if (Timer allows retarget?) then (yes)
        :retarget() → findTarget(x, y, range, air, ground);
        if (Enemy found?) then (yes)
            :Set target;
            :Reset noTargetTime;
        else (no)
            :Increment noTargetTime;
            if (noTargetTime > rotateBackTimer?) then (yes)
                :Face default direction;
            endif
        endif
    endif
else (yes)
endif

:updateMovement() — dispatched by AI subtype;

if (AI type?) then (FlyingAI / SuicideAI)
    if (Has valid target?) then (yes)
        if (circleTarget enabled?) then (yes)
            :circleAttack(circleLength);
        else (no)
            :moveTo(target, circleLength, smooth);
        endif
    else (no)
        :pathfind(Pathfinder.fieldCore);
    endif
else if (AI type?) then (GroundAI)
    :pathfind to target via ControlPathfinder;
    if (Stuck > stuckRange?) then (yes)
        :Force-nudge position;
    endif
else if (AI type?) then (BuilderAI)
    if (alwaysFlee OR near enemy?) then (yes)
        :Flee to safe distance;
    else (no)
        :Find next BlockPlan in queue;
        if (Plan found?) then (yes)
            :Move to plan location;
            :Execute build / deconstruct;
        else (no)
            :Follow assistFollowing unit;
        endif
    endif
else if (AI type?) then (MinerAI)
    if (Unit full of items?) then (yes)
        :Deliver items to nearest core;
    else (no)
        :Move to ore tile;
        :Mine ore;
    endif
else (CommandAI)
    if (Has command queue position?) then (yes)
        :Move to commandPos via getPathPosition;
        if (Arrived?) then (yes)
            :Finish path;
        endif
    else (no)
        :defaultBehavior() — attack nearby enemies;
    endif
endif

:updateWeapons();

if (Has weapons AND canShoot()?) then (yes)
    :For each WeaponMount;
    if (Target in shootCone AND range?) then (yes)
        :Advance reload counter;
        if (Reload complete?) then (yes)
            :Fire bullet (BulletType.create);
            :Reset reload;
            :Apply recoil;
            :Play shootSound;
            :Shoot effect;
        else (no)
            :Continue reloading;
        endif
    else (no)
        :Rotate weapon toward targetPos;
    endif
else (no)
endif

:updateVisuals() — engine particles, trails;

stop
@enduml
```

---

```plantuml
@startuml MindustryBuildingProductionFlow
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
    DiamondBackgroundColor #E9ECEF
    DiamondBorderColor #6C757D
}

title Building Production & Resource Flow (GenericCrafter)

|Building (Consumer side)|
start
:Building.updateTile() called;
:updateConsumption()\nCalculate efficiency from all Consume[];

if (Building enabled?) then (no)
    :Remain idle;
    stop
else (yes)
endif

if (Power satisfied (ConsumePower)?) then (no)
    :status = noInput;
    stop
else (yes)
endif

if (Required items available\n(ConsumeItems check)?) then (no)
    :status = noInput;
    stop
else (yes)
endif

if (Required liquids available\n(ConsumeLiquid check)?) then (no)
    :status = noInput;
    stop
else (yes)
endif

:efficiency = product of all consumer efficiencies;
:potentialEfficiency = efficiency;

|Production Logic|
if (warmup < 1.0?) then (yes)
    :Increment warmup by warmupSpeed * edelta();
else (no)
endif

:progress += getProgressIncrease(craftTime)\n× efficiency × timeScale;

if (progress >= 1.0?) then (yes)
    :craft() — produce outputs;

    if (outputItem set?) then (yes)
        :Add outputItem to items module;
    endif
    if (outputItems[] set?) then (yes)
        :Add each ItemStack to items;
    endif
    if (outputLiquid set?) then (yes)
        :Add liquid to liquids module;
    endif

    :consume() — deduct inputs\nfrom items/liquids/power;
    :Fire craftEffect at building position;
    :Reset progress to 0;
else (no)
    :Continue building progress;
endif

|Output Distribution|
:dumpOutputs();
:For each outputItem/outputItems;
:dump(item) to adjacent buildings;

if (Adjacent building accepts item?) then (yes)
    :handleItem(source, item)\nTransfer item;
    :Fire itemTransfer effect;
else (no)
    :Item remains in buffer;
    :status = noOutput;
endif

if (outputLiquid set?) then (yes)
    :dumpLiquid(liquid) to adjacent;\nFollow liquid graph;
else (no)
endif

:Building update complete;
stop
@enduml
```
