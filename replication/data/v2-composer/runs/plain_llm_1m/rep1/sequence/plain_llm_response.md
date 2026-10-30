Looking at the Composer IR, I'll generate several focused sequence diagrams covering the main runtime flows: the install command flow, dependency resolution, and package download/installation.

```plantuml
@startuml composer-install-flow
skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequence {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
    ParticipantBackgroundColor #F8F9FA
    ParticipantBorderColor #495057
    LifeLineBorderColor #ADB5BD
    ActivationBorderColor #6C757D
    ActivationBackgroundColor #E9ECEF
}

title Composer Install Command — Main Flow

actor "User" as user
participant "Application" as app
participant "InstallCommand" as cmd
participant "BaseCommand" as base
participant "Factory" as factory
participant "Installer" as installer
participant "RepositoryManager" as rm
participant "Locker" as locker
participant "InstallationManager" as im
participant "AutoloadGenerator" as ag

user -> app : run($input, $output)
activate app

app -> app : doRun($input, $output)
app -> cmd : execute($input, $output)
activate cmd

cmd -> base : requireComposer()
activate base
base -> factory : createComposer($io, $localConfig)
activate factory
factory -> factory : loadRootPackage(...)
factory -> rm : setLocalRepository(...)
factory --> base : $composer
deactivate factory
base --> cmd : $composer
deactivate base

cmd -> installer : Installer::create($io, $composer)
activate installer

installer -> locker : isLocked()
activate locker
locker --> installer : true/false
deactivate locker

alt lock file exists (install mode)
    installer -> installer : doInstall($localRepo)
else no lock or update mode
    installer -> installer : doUpdate($localRepo, $doInstall)
end

installer -> installer : run()
activate installer

installer --> cmd : exit code
deactivate installer

cmd -> ag : dump($config, $localRepo,\n  $rootPackage, $installationManager, ...)
activate ag
ag -> ag : buildPackageMap(...)
ag -> ag : parseAutoloads(...)
ag --> cmd : autoload files written
deactivate ag

cmd --> app : exit code
deactivate cmd
app --> user : exit code
deactivate app

@enduml
```

```plantuml
@startuml composer-dependency-resolution
skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequence {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
    ParticipantBackgroundColor #F8F9FA
    ParticipantBorderColor #495057
    LifeLineBorderColor #ADB5BD
    ActivationBorderColor #6C757D
    ActivationBackgroundColor #E9ECEF
}

title Composer Dependency Resolution Flow

participant "Installer" as installer
participant "RepositorySet" as repoSet
participant "PoolBuilder" as pb
participant "PoolOptimizer" as po
participant "SecurityAdvisory\nPoolFilter" as sapf
participant "Request" as request
participant "RuleSetGenerator" as rsg
participant "Solver" as solver
participant "LockTransaction" as lt
participant "Decisions" as dec

installer -> repoSet : createPool($request, $io,\n  $eventDispatcher, $poolOptimizer, ...)
activate repoSet

repoSet -> pb : buildPool($repositories, $request)
activate pb

loop for each package name to load
    pb -> pb : loadPackagesMarkedForLoading\n  ($request, $repositories)
    pb -> pb : loadPackage($request, $repos,\n  $package, $propagateUpdate)
end

pb -> po : optimize($request, $pool)
activate po
po -> po : prepare($request, $pool)
po -> po : optimizeByIdenticalDependencies\n  ($request, $pool)
po -> po : optimizeImpossiblePackagesAway\n  ($request, $pool)
po --> pb : optimized $pool
deactivate po

pb -> sapf : filter($pool, $repositories, $request)
activate sapf
sapf --> pb : $pool (security-filtered)
deactivate sapf

pb --> repoSet : $pool
deactivate pb
repoSet --> installer : $pool
deactivate repoSet

installer -> request : setUpdateAllowList($packages,\n  $allowTransitive)
activate request
request --> installer : done
deactivate request

installer -> solver : new Solver($policy, $pool, $io)
activate solver

installer -> rsg : getRulesFor($request, $platformFilter)
activate rsg
rsg -> rsg : addRulesForRequest(...)
rsg -> rsg : addConflictRules(...)
rsg -> rsg : addRulesForRootAliases(...)
rsg --> installer : $ruleSet
deactivate rsg

installer -> solver : solve($request, $platformFilter)
activate solver

solver -> solver : makeAssertionRuleDecisions()
solver -> solver : runSat()
activate solver

loop propagation loop
    solver -> dec : decide($literal, $level, $why)
    activate dec
    dec --> solver : ok
    deactivate dec
    solver -> solver : propagate($level)
end

solver --> solver : decisions complete
deactivate solver

solver -> lt : new LockTransaction($pool, $presentMap,\n  $unlockableMap, $decisions)
activate lt
lt -> lt : setResultPackages($pool, $decisions)
lt --> solver : $lockTransaction
deactivate lt

solver --> installer : $lockTransaction
deactivate solver

installer -> lt : getNewLockPackages($devMode, $updateMirrors)
activate lt
lt --> installer : [$packages, $devPackages]
deactivate lt

@enduml
```

```plantuml
@startuml composer-package-download-install
skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequence {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
    ParticipantBackgroundColor #F8F9FA
    ParticipantBorderColor #495057
    LifeLineBorderColor #ADB5BD
    ActivationBorderColor #6C757D
    ActivationBackgroundColor #E9ECEF
}

title Package Download and Installation Flow

participant "Installer" as installer
participant "InstallationManager" as im
participant "EventDispatcher" as ed
participant "LibraryInstaller" as li
participant "DownloadManager" as dm
participant "FileDownloader" as fd
participant "HttpDownloader" as hd
participant "Cache" as cache
participant "ArchiveDownloader" as ad
participant "BinaryInstaller" as bi

installer -> im : execute($repo, $operations,\n  $devMode, $runScripts)
activate im

loop for each operation batch
    im -> im : downloadAndExecuteBatch\n  ($repo, $operations, ...)

    loop parallel downloads
        im -> li : download($package, $prevPackage)
        activate li
        li -> dm : download($package, $targetDir, $prevPackage)
        activate dm

        dm -> dm : resolvePackageInstallPreference($package)
        dm -> dm : getDownloaderForPackage($package)
        dm -> fd : download($package, $path, $prevPackage)
        activate fd

        fd -> cache : read($cacheKey)
        activate cache
        cache --> fd : cached content or null
        deactivate cache

        alt not cached
            fd -> hd : get($url, $options)
            activate hd
            hd --> fd : Response
            deactivate hd
            fd -> cache : write($cacheKey, $contents)
        end

        fd --> dm : Promise resolved
        deactivate fd
        dm --> li : Promise
        deactivate dm
        li --> im : Promise
        deactivate li
    end

    im -> im : executeBatch($repo, $operations, ...)

    loop for each install operation
        im -> ed : dispatchPackageEvent(\n  PRE_PACKAGE_INSTALL, ...)
        activate ed
        ed --> im : ok
        deactivate ed

        im -> li : install($repo, $package)
        activate li

        li -> dm : install($package, $targetDir)
        activate dm
        dm -> ad : install($package, $path)
        activate ad

        ad -> ad : extract($package, $file, $path)
        ad --> dm : done
        deactivate ad

        dm --> li : done
        deactivate dm

        li -> bi : installBinaries($package, $installPath)
        activate bi
        bi --> li : done
        deactivate bi

        li -> li : updateInstalledFile(...)
        li --> im : done
        deactivate li

        im -> ed : dispatchPackageEvent(\n  POST_PACKAGE_INSTALL, ...)
        activate ed
        ed --> im : ok
        deactivate ed
    end
end

im --> installer : done
deactivate im

@enduml
```

```plantuml
@startuml composer-audit-flow
skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequence {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
    ParticipantBackgroundColor #F8F9FA
    ParticipantBorderColor #495057
    LifeLineBorderColor #ADB5BD
    ActivationBorderColor #6C757D
    ActivationBackgroundColor #E9ECEF
}

title Security Audit Flow (composer audit)

actor "User" as user
participant "AuditCommand" as cmd
participant "Auditor" as auditor
participant "RepositorySet" as repoSet
participant "ComposerRepository" as repo
participant "PolicyConfig" as policy
participant "Locker" as locker
participant "IOInterface" as io

user -> cmd : execute($input, $output)
activate cmd

cmd -> cmd : getPackages($composer, $input)
activate cmd

cmd -> locker : getLockedRepository($withDevReqs)
activate locker
locker --> cmd : $lockedRepository
deactivate locker

deactivate cmd

cmd -> auditor : audit($io, $repositorySet,\n  $policyConfig, $packages,\n  $format, $warningOnly, ...)
activate auditor

auditor -> repoSet : getMatchingSecurityAdvisories\n  ($packages, $allowPartial, ...)
activate repoSet

loop for each advisory-provider repository
    repoSet -> repo : getSecurityAdvisories\n  ($packageConstraintMap, $allowPartial)
    activate repo
    repo --> repoSet : advisories map
    deactivate repo
end

repoSet --> auditor : $allAdvisories
deactivate repoSet

auditor -> auditor : needsCompleteAdvisoryLoad\n  ($advisories, $ignoreList)

auditor -> auditor : processAdvisories\n  ($allAdvisories, $ignoreList,\n  $ignoredSeverities)

auditor -> policy : getActiveAuditFilterLists()
activate policy
policy --> auditor : $activeLists
deactivate policy

auditor -> auditor : filterAbandonedPackages\n  ($packages, $ignoreAbandoned)

alt has vulnerabilities
    auditor -> io : write(vulnerability details)
    activate io
    io --> auditor : ok
    deactivate io
    auditor -> auditor : outputAdvisories($io, $advisories, $format)
end

alt has abandoned packages
    auditor -> auditor : outputAbandonedPackages\n  ($io, $packages, $format)
end

auditor -> auditor : calculateBitmask\n  ($hasVulnerable, $hasAbandoned, $hasFiltered)

auditor --> cmd : exit bitmask
deactivate auditor

cmd --> user : exit code
deactivate cmd

@enduml
```

```plantuml
@startuml composer-autoload-generation
skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequence {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
    ParticipantBackgroundColor #F8F9FA
    ParticipantBorderColor #495057
    LifeLineBorderColor #ADB5BD
    ActivationBorderColor #6C757D
    ActivationBackgroundColor #E9ECEF
}

title Autoload Generation Flow

participant "DumpAutoloadCommand" as cmd
participant "AutoloadGenerator" as ag
participant "InstallationManager" as im
participant "ClassMapGenerator" as cmg
participant "EventDispatcher" as ed
participant "Filesystem" as fs

cmd -> ag : dump($config, $localRepo,\n  $rootPackage, $installationManager,\n  $targetDir, $scanPsr, $suffix, $locker)
activate ag

ag -> ed : dispatchScript(PRE_AUTOLOAD_DUMP, ...)
activate ed
ed --> ag : ok
deactivate ed

ag -> ag : buildPackageMap($installationManager,\n  $rootPackage, $packages)
activate ag

loop for each package
    ag -> im : getInstallPath($package)
    activate im
    im --> ag : $installPath
    deactivate im
end
deactivate ag

ag -> ag : parseAutoloads($packageMap,\n  $rootPackage, $filteredDevPackages)
activate ag

loop for each autoload type (psr-4, psr-0, classmap, files)
    ag -> ag : parseAutoloadsType($packageMap,\n  $type, $rootPackage)
end
deactivate ag

opt classmap enabled
    ag -> cmg : createMap($path, $excluded, $io,\n  $namespace, $autoloadType, ...)
    activate cmg
    cmg --> ag : $classMap
    deactivate cmg
end

ag -> ag : getAutoloadFile($vendorPathCode, $suffix)
ag -> ag : getAutoloadRealFile($useClassMap,\n  $useIncludePath, ...)

ag -> fs : filePutContentsIfModified\n  (autoload.php, $content)
activate fs
fs --> ag : ok
deactivate fs

ag -> fs : filePutContentsIfModified\n  (autoload_classmap.php, $content)
activate fs
fs --> ag : ok
deactivate fs

ag -> fs : filePutContentsIfModified\n  (autoload_namespaces.php, $content)
activate fs
fs --> ag : ok
deactivate fs

ag -> fs : filePutContentsIfModified\n  (autoload_psr4.php, $content)
activate fs
fs --> ag : ok
deactivate fs

ag -> ed : dispatchScript(POST_AUTOLOAD_DUMP, ...)
activate ed
ed --> ag : ok
deactivate ed

ag --> cmd : done
deactivate ag

@enduml
```
