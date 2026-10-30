# UML Diagram Study — Generated Diagrams

This folder contains all PlantUML diagrams generated as part of the empirical evaluation for the research paper:

> **Code2UML: Agentic LLMs with context engineering for scalable software visualization**

## Contents

- **917 PlantUML source files** (`.puml`) — the generated UML diagrams
- **864 rendered PNG images** (`.png`) — visual renders of the diagrams

## Structure

```
diagrams_uml_study/
├── Java/
│   ├── Mindustry/          (72 diagrams)
│   ├── debezium/           (96 diagrams)
│   └── shopping-cart/      (24 diagrams)
├── JavaScript/
│   ├── evolver/            (81 diagrams)
│   ├── impeccable/         (64 diagrams)
│   └── puter/             (100 diagrams)
├── PHP/
│   ├── PHPMailer/          (97 diagrams)
│   ├── composer/           (71 diagrams)
│   └── laravel-ai/         (66 diagrams)
└── Python/
    ├── GenericAgent/        (45 diagrams)
    ├── lyra/               (92 diagrams)
    └── openai-agents/     (109 diagrams)
```

Each project folder contains subfolders by diagram type:
`activity/`, `class/`, `component/`, `deployment/`, `sequence/`, `system_context/`, `use_case/`

## Source Repositories

### Java
| Project | Repository | Diagrams |
|---------|-----------|----------|
| shopping-cart | https://github.com/shashirajraja/shopping-cart | 24 |
| Mindustry | https://github.com/Anuken/Mindustry | 72 |
| debezium | https://github.com/debezium/debezium | 96 |

### JavaScript
| Project | Repository | Diagrams |
|---------|-----------|----------|
| evolver | https://github.com/EvoMap/evolver | 81 |
| impeccable | https://github.com/pbakaus/impeccable | 64 |
| puter | https://github.com/HeyPuter/puter | 100 |

### PHP
| Project | Repository | Diagrams |
|---------|-----------|----------|
| laravel-ai | https://github.com/laravel/ai | 66 |
| PHPMailer | https://github.com/PHPMailer/PHPMailer | 97 |
| composer | https://github.com/composer/composer | 71 |

### Python
| Project | Repository | Diagrams |
|---------|-----------|----------|
| GenericAgent | https://github.com/lsdefine/GenericAgent | 45 |
| openai-agents | https://github.com/openai/openai-agents-python | 109 |
| lyra | https://github.com/nv-tlabs/lyra | 92 |

## Evaluation Summary

| Language | Projects | Total Diagrams |
|----------|----------|----------------|
| Java | 3 | 192 |
| JavaScript | 3 | 245 |
| PHP | 3 | 234 |
| Python | 3 | 246 |
| **Total** | **12** | **917** |

## Diagram Types

| Type | Description | Pipeline Path |
|------|-------------|---------------|
| Class | Class hierarchies, attributes, methods, relationships | DEEP |
| Sequence | Method call flows between participants | DEEP |
| Activity | Business process flows with swim lanes | DEEP |
| Use Case | Actor-system interactions | DEEP |
| Component | Package-level architecture | SINGLE |
| Deployment | Infrastructure and deployment topology | SINGLE |
| System Context | C4-style external system integrations | SINGLE |
