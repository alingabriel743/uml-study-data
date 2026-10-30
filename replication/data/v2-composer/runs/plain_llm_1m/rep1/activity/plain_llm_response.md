Looking at the Composer IR, I'll generate three focused activity diagrams covering the most important workflows: the main install/update process, the SAT dependency resolver, and the security audit flow.

```plantuml
@startuml composer_install_workflow

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam ActivityBackgroundColor #F8F9FA
skinparam ActivityBorderColor #495057
skinparam ActivityArrowColor #495057
skinparam ActivityFontColor #212529
skinparam ActivityDiamondBackgroundColor #E9ECEF
skinparam PartitionBackgroundColor #E9ECEF
skinparam PartitionBorderColor #6C757D
skinparam SwimlaneBorderColor #495057
skinparam SwimlaneBackgroundColor #F8F9FA

title Composer Install / Update Workflow

|User|
start
:Run composer install or composer update;

|Console Application|
:Parse CLI arguments and options;
:Create ConsoleIO;
:Dispatch PreCommandRunEvent;
:Route to matching command;

|BaseCommand|
:Initialize Composer instance via Factory;
:Merge global and project configuration;
:Initialize RepositoryManager;
:Load and activate plugins;

|InstallCommand / UpdateCommand|
:Create Installer with all dependencies;
:Apply CLI flags to Installer\n(prefer-source, no-dev, dry-run, etc.);
:Call Installer::run();

|Installer|
:Load local InstalledFilesystemRepository;
:Load root RootPackage;

if (Perform update or no lock file?) then (yes)
  :Load all configured repositories;
  :Build dependency request from\nroot package requirements;

  |Solver|
  :Build Pool via PoolBuilder;
  :Generate SAT RuleSet;
  :Run DPLL algorithm;

  if (Unsolvable conflicts found?) then (yes)
    :Format dependency problem report;

    |User|
    :Display solver error and exit;
    stop
  else (no)
    |Installer|
    :Create LockTransaction from decisions;
    :Separate dev / non-dev packages;
    :Write updated composer.lock;
  endif
else (no)
  :Load packages from existing composer.lock;
  :Check lock file freshness;
endif

:Compute diff against local repository;
:Generate ordered operation list\n(install / update / uninstall);

|InstallationManager|
:Batch download packages in parallel;
:Prepare installation directories;
:Execute install operations in order;
:Execute update operations;
:Execute uninstall operations;
:Install binary stubs to vendor/bin;
:Notify package install URLs;

|AutoloadGenerator|
if (Dump autoloader?) then (yes)
  :Build package map with install paths;
  :Parse PSR-0 / PSR-4 / classmap / files;
  :Generate class map via ClassMapGenerator;
  :Write vendor/autoload.php and helpers;
endif

|EventDispatcher|
if (Run scripts?) then (yes)
  :Dispatch post-install-cmd or post-update-cmd;
  :Execute PHP scripts and shell commands;
endif

|Auditor|
if (Audit enabled on install?) then (yes)
  :Query repositories for security advisories;
  :Match advisories to installed packages;
  if (Vulnerabilities found?) then (yes)
    if (Error on audit mode?) then (yes)
      |User|
      :Display vulnerabilities and exit non-zero;
      stop
    else (no)
      |User|
      :Display vulnerability warnings;
    endif
  endif
endif

|User|
:Display completion summary and exit 0;
stop

@enduml
```

```plantuml
@startuml composer_dependency_resolution

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam ActivityBackgroundColor #F8F9FA
skinparam ActivityBorderColor #495057
skinparam ActivityArrowColor #495057
skinparam ActivityFontColor #212529
skinparam ActivityDiamondBackgroundColor #E9ECEF
skinparam PartitionBackgroundColor #E9ECEF
skinparam PartitionBorderColor #6C757D

title Composer Dependency Resolution — SAT Solver Flow

start

partition "Pool Building (PoolBuilder)" {
  :Receive root requirements and locked packages;
  :Mark root requirement names for loading;

  repeat
    :Load package versions from repositories\n(Composer, VCS, Path, Artifact);
    :Apply stability acceptability filters;
    :Create AliasPackage objects for branch aliases;
    :Mark transitive dependencies for loading;
    :Track already-loaded packages to avoid cycles;
  repeat while (Packages queued for loading?)

  if (Pool optimizer configured?) then (yes)
    :Group packages by name;
    :Compute dependency hash per package version;
    :Remove versions with identical dependency signatures;
    :Remove packages that cannot satisfy any constraint;
  endif

  if (Security advisory pool filter active?) then (yes)
    :Query AdvisoryProviderInterface repositories;
    :Identify affected package versions;
    :Remove vulnerable versions from pool;
    :Record removals in securityRemovedVersions;
  endif

  if (Filter list pool filter active?) then (yes)
    :Build locked package name-version map;
    :Fetch filter entries from FilterListProviders;
    :Remove blocked non-locked versions from pool;
    :Record removals in filterListRemovedVersions;
  endif

  :Dispatch PrePoolCreateEvent (plugins may modify);
  :Finalize Pool with all package arrays;
}

partition "Rule Generation (RuleSetGenerator)" {
  :Add assertion rules for fixed packages;
  :Add require rules for root package dependencies;

  repeat
    :Process next unvisited package in pool;
    :Create Rule2Literals for each require link;
    :Create conflict rules for conflicting packages;
    :Add rules for packages that provide replacements;
  repeat while (Unvisited packages in pool?)

  :Add GenericRule assertions for root aliases;
}

partition "SAT Solving — DPLL (Solver)" {
  :Initialize RuleWatchGraph with all rules;
  :Initialize empty Decisions queue;
  :Process assertion rules via unit propagation;
  :Populate fixedMap from request fixed packages;

  repeat
    :Check for root-required packages with no candidates;
    :Select lowest-ID undecided package literal;
    :Query DefaultPolicy for preferred package version\n(stable first, prefer-lowest, pinned versions);
    :Record decision in Decisions queue;
    :Propagate via RuleWatchGraph;

    if (Watch graph reports conflict?) then (yes)
      :Call analyze() on conflict rule;
      if (Conflict level == 0?) then (yes)
        :analyzeUnsolvable — record Problem;
        :Continue to collect remaining problems;
      else (no)
        :Backjump to learned conflict level;
        :Add learned rule to rule set;
        :Revert Decisions to conflict level;
        :Re-propagate from learned assertion;
      endif
    endif
  repeat while (Undecided literals remain in pool?)
}

if (Problems list non-empty?) then (yes)
  :Collect all Problem::getPrettyString() messages;
  :Include missing package hints;
  :Include platform requirement details;
  :Include version availability context;
  :Throw SolverProblemsException;
  stop
endif

partition "Transaction Generation (LockTransaction)" {
  :Iterate decided install literals;
  :Map literals to PackageInterface objects;
  :Classify: present / new / updated / removed;
  :Create InstallOperation for new packages;
  :Create UpdateOperation for version changes;
  :Create UninstallOperation for removed packages;
  :Create MarkAliasInstalled for alias packages;
  :movePluginsToFront — plugins install first;
  :moveUninstallsToFront — remove before install;
}

:Return ordered Transaction operation list;
stop

@enduml
```

```plantuml
@startuml composer_security_audit

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam ActivityBackgroundColor #F8F9FA
skinparam ActivityBorderColor #495057
skinparam ActivityArrowColor #495057
skinparam ActivityFontColor #212529
skinparam ActivityDiamondBackgroundColor #E9ECEF
skinparam PartitionBackgroundColor #E9ECEF
skinparam PartitionBorderColor #6C757D
skinparam SwimlaneBorderColor #495057
skinparam SwimlaneBackgroundColor #F8F9FA

title Composer Security Audit Workflow

|AuditCommand|
start
:Parse --format, --no-dev, --locked options;
:Load Composer instance and configuration;
:Load PolicyConfig from composer.json;

if (--locked flag?) then (yes)
  :Load packages from composer.lock;
else (no)
  :Load packages from installed repository;
endif

if (No-dev mode?) then (yes)
  :Exclude dev-only packages from list;
endif

:Pass packages to Auditor::audit();

|Auditor|
partition "Advisory Collection" {
  :Build packageName -> VersionConstraint map;

  fork
    :Call RepositorySet::getMatchingSecurityAdvisories();
    :Query each AdvisoryProviderInterface repo;
    :Receive PartialSecurityAdvisory list;
  fork again
    :Call FilterListProviderSet::getMatchingFilterLists();
    :Query each FilterListProviderInterface repo;
    :Receive FilterListEntry list;
  end fork

  :Merge advisory collections by package name;
}

partition "Advisory Processing" {
  :Group all advisories by package name;

  if (needsCompleteAdvisoryLoad?) then (yes)
    :Identify advisories missing full details;
    :Fetch complete SecurityAdvisory data;
  endif

  :Match each advisory against installed version;

  if (Ignore ID list configured in PolicyConfig?) then (yes)
    :Check each advisory ID against ignore rules;
    :Convert matching to IgnoredSecurityAdvisory;
    :Attach ignore reason to advisory;
  endif

  if (Ignore severity list configured?) then (yes)
    :Check advisory severity against threshold;
    :Convert low-severity to IgnoredSecurityAdvisory;
  endif
}

if (AbandonedPolicyConfig active?) then (yes)
  partition "Abandoned Package Check" {
    :Iterate installed CompletePackage objects;
    :Check isAbandoned() flag on each;
    :Apply IgnorePackageRule filters;
    :Separate abandoned from ignored-abandoned;
  }
endif

if (MalwarePolicyConfig active?) then (yes)
  partition "Malware / Filter List Check" {
    :Collect filter list entries for packages;
    :Apply ignore rules from PolicyConfig;
    :Classify entries as block or audit;
    :Build filtered package result map;
  }
endif

partition "Output Formatting" {
  if (Format == table?) then (yes)
    :Render Symfony Console Table\nwith Package, CVE, Severity, URL columns;
  else if (Format == plain?) then (yes)
    :Write text lines per advisory;
  else if (Format == json?) then (yes)
    :JSON-serialize all advisory objects\nvia jsonSerialize();
  else (summary)
    :Count advisories by severity level;
    :Render count summary;
  endif

  if (Abandoned packages found?) then (yes)
    :Output abandoned package list;
  endif

  if (Filtered packages found?) then (yes)
    :Output filter list match warnings;
  endif
}

:Compute result bitmask\n(bit0=vulnerable, bit1=abandoned, bit2=filtered);

|AuditCommand|
if (Warning-only mode?) then (yes)
  :Output as warnings only;
  :Return exit code 0;
else (no)
  if (Result bitmask non-zero?) then (yes)
    :Return non-zero exit code;
  else (no)
    :Output no vulnerabilities found;
    :Return exit code 0;
  endif
endif

|User|
:Display audit results;
stop

@enduml
```
