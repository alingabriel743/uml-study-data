Looking at the IR for the Composer project, I can identify:

**Actors:**
- Developer (primary user via CLI)
- CI/CD System (automated pipelines)
- Package Repository (Packagist/VCS — secondary/supporting)
- Composer Plugin (extension system)

**Use cases** derived from the 35+ command classes, services, and sub-systems.

```plantuml
@startuml
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
skinparam actor {
    BackgroundColor #DDEEFF
    BorderColor #5577AA
    FontColor #212529
}
skinparam rectangle {
    BackgroundColor #F0F4F8
    BorderColor #6C757D
}
skinparam package {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
    FontColor #212529
}

title Composer - PHP Package Manager\nUse Case Diagram

left to right direction

actor "Developer" as dev
actor "CI/CD\nSystem" as cicd
actor "Package\nRepository" as repo
actor "Composer\nPlugin" as plugin

rectangle "Composer Package Manager" {

    package "Dependency Management" {
        usecase "Install Packages" as UC_INSTALL
        usecase "Update Packages" as UC_UPDATE
        usecase "Require Package" as UC_REQUIRE
        usecase "Remove Package" as UC_REMOVE
        usecase "Reinstall Packages" as UC_REINSTALL
        usecase "Create Project from Template" as UC_CREATE
    }

    package "Project Configuration" {
        usecase "Initialize New Project" as UC_INIT
        usecase "Configure Composer Settings" as UC_CONFIG
        usecase "Manage Repositories" as UC_REPOS
        usecase "Validate composer.json" as UC_VALIDATE
    }

    package "Package Discovery" {
        usecase "Search Packages" as UC_SEARCH
        usecase "Show Package Details" as UC_SHOW
        usecase "List Outdated Packages" as UC_OUTDATED
        usecase "Analyze Dependency Tree" as UC_DEPENDS
        usecase "Show Package Licenses" as UC_LICENSES
        usecase "Browse Package Homepage" as UC_HOME
        usecase "Show Funding Information" as UC_FUND
    }

    package "Security & Compliance" {
        usecase "Audit Security Vulnerabilities" as UC_AUDIT
        usecase "Manage Security Policy" as UC_POLICY
        usecase "Check Platform Requirements" as UC_PLATFORM
        usecase "Bump Version Constraints" as UC_BUMP
    }

    package "Build & Execution" {
        usecase "Generate Autoloader" as UC_AUTOLOAD
        usecase "Run Composer Scripts" as UC_SCRIPTS
        usecase "Execute Vendor Binaries" as UC_EXEC
        usecase "Archive Package" as UC_ARCHIVE
        usecase "Check Local Package Status" as UC_STATUS
    }

    package "Maintenance" {
        usecase "Clear Cache" as UC_CLEAR
        usecase "Diagnose Environment" as UC_DIAGNOSE
        usecase "Self-Update Composer" as UC_SELFUPDATE
        usecase "Run Global Command" as UC_GLOBAL
    }
}

' Developer – primary actor uses all features
dev --> UC_INSTALL
dev --> UC_UPDATE
dev --> UC_REQUIRE
dev --> UC_REMOVE
dev --> UC_REINSTALL
dev --> UC_CREATE
dev --> UC_INIT
dev --> UC_CONFIG
dev --> UC_REPOS
dev --> UC_VALIDATE
dev --> UC_SEARCH
dev --> UC_SHOW
dev --> UC_OUTDATED
dev --> UC_DEPENDS
dev --> UC_LICENSES
dev --> UC_HOME
dev --> UC_FUND
dev --> UC_AUDIT
dev --> UC_POLICY
dev --> UC_PLATFORM
dev --> UC_BUMP
dev --> UC_AUTOLOAD
dev --> UC_SCRIPTS
dev --> UC_EXEC
dev --> UC_ARCHIVE
dev --> UC_STATUS
dev --> UC_CLEAR
dev --> UC_DIAGNOSE
dev --> UC_SELFUPDATE
dev --> UC_GLOBAL

' CI/CD system uses automation-relevant features
cicd --> UC_INSTALL
cicd --> UC_UPDATE
cicd --> UC_VALIDATE
cicd --> UC_AUDIT
cicd --> UC_AUTOLOAD
cicd --> UC_SCRIPTS
cicd --> UC_PLATFORM

' Package Repository – secondary/supporting actor
UC_INSTALL --> repo
UC_UPDATE --> repo
UC_SEARCH --> repo
UC_REQUIRE --> repo
UC_CREATE --> repo
UC_AUDIT --> repo
UC_SHOW --> repo

' Plugin – extends Composer lifecycle hooks
plugin --> UC_INSTALL
plugin --> UC_UPDATE
plugin --> UC_SCRIPTS
plugin --> UC_AUTOLOAD

' Include relationships (sub-flows always executed)
UC_INSTALL ..> UC_AUTOLOAD : <<include>>
UC_UPDATE ..> UC_AUTOLOAD : <<include>>
UC_REINSTALL ..> UC_INSTALL : <<include>>
UC_AUDIT ..> UC_POLICY : <<include>>
UC_GLOBAL ..> UC_INSTALL : <<include>>
UC_GLOBAL ..> UC_UPDATE : <<include>>

' Extend relationships (optional/conditional behaviour)
UC_OUTDATED ..> UC_SHOW : <<extend>>
UC_DEPENDS ..> UC_SHOW : <<extend>>

@enduml
```
