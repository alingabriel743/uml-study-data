Looking at the IR, this is the **Lyra** project — an AI system for video world generation with two major versions (Lyra-1/cosmos_predict1 and Lyra-2/lyra_2). I'll produce two focused use case diagrams: one for end-user generation capabilities and one for the ML engineering/training lifecycle.

---

```plantuml
@startuml lyra_use_case_generation

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam usecase {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
    ArrowColor #495057
}
skinparam actor {
    BorderColor #495057
    FontColor #212529
    BackgroundColor #DEE2E6
}
skinparam package {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
    FontColor #212529
}
skinparam note {
    BackgroundColor #FFF9C4
    BorderColor #F0AD4E
}

title Lyra — World Generation System (User Perspective)

left to right direction

actor "Researcher /\nDeveloper" as researcher
actor "Safety\nOfficer" as safety
actor "External\nCheckpoint\nStore" as checkpoints

rectangle "Lyra World Generation System" {

    package "Text-to-World Generation" {
        usecase "Generate World\nfrom Text Prompt" as UC_T2W
        usecase "Upsample Prompt\n(Prompt Upsampler)" as UC_PU
        usecase "Generate Multi-view\nWorld from Text" as UC_T2W_MV
    }

    package "Video-to-World Generation" {
        usecase "Generate World\nfrom Input Video" as UC_V2W
        usecase "Generate Multi-view\nWorld from Video" as UC_V2W_MV
        usecase "Interpolate Between\nVideo Frames" as UC_INTERP
        usecase "Generate AR World\n(Lyra-2 Pipeline)" as UC_AR
    }

    package "3D-Consistent Generation (Gen3C)" {
        usecase "Generate 3D Video\nfrom Single Image" as UC_GEN3C_IMG
        usecase "Generate 3D Video\nfrom Dynamic Video" as UC_GEN3C_DYN
        usecase "Generate Multi-view\n3D Video" as UC_GEN3C_MV
        usecase "Generate with\nCustom Camera Trajectory" as UC_CAM_TRAJ
    }

    package "Tokenization" {
        usecase "Encode Video\nto Latents" as UC_ENC
        usecase "Decode Latents\nto Video" as UC_DEC
        usecase "Encode Image\nto Latents" as UC_ENC_IMG
    }

    package "Content Safety (Guardrails)" {
        usecase "Check Text\nContent Safety" as UC_GR_TEXT
        usecase "Check Video\nContent Safety" as UC_GR_VID
        usecase "Blur Detected\nFaces in Video" as UC_FACE_BLUR
        usecase "Filter Blocklisted\nContent" as UC_BLOCKLIST
    }

    package "Model Management" {
        usecase "Download Model\nCheckpoints" as UC_DL_CKPT
        usecase "Load Model\nWeights" as UC_LOAD
    }
}

' Researcher interactions
researcher --> UC_T2W
researcher --> UC_T2W_MV
researcher --> UC_V2W
researcher --> UC_V2W_MV
researcher --> UC_INTERP
researcher --> UC_AR
researcher --> UC_GEN3C_IMG
researcher --> UC_GEN3C_DYN
researcher --> UC_GEN3C_MV
researcher --> UC_CAM_TRAJ
researcher --> UC_ENC
researcher --> UC_DEC
researcher --> UC_ENC_IMG
researcher --> UC_DL_CKPT

' Safety Officer interactions
safety --> UC_GR_TEXT
safety --> UC_GR_VID
safety --> UC_FACE_BLUR
safety --> UC_BLOCKLIST

' External system interactions
checkpoints --> UC_LOAD

' Include relationships
UC_T2W ..> UC_PU : <<include>>
UC_T2W ..> UC_GR_TEXT : <<include>>
UC_T2W ..> UC_GR_VID : <<include>>
UC_V2W ..> UC_GR_TEXT : <<include>>
UC_V2W ..> UC_GR_VID : <<include>>
UC_GEN3C_IMG ..> UC_GR_TEXT : <<include>>
UC_GEN3C_IMG ..> UC_GR_VID : <<include>>
UC_AR ..> UC_GR_TEXT : <<include>>
UC_AR ..> UC_GR_VID : <<include>>

' Extend relationships
UC_GR_VID ..> UC_FACE_BLUR : <<extend>>
UC_GR_TEXT ..> UC_BLOCKLIST : <<extend>>

' Dependencies
UC_T2W ..> UC_LOAD : <<include>>
UC_V2W ..> UC_LOAD : <<include>>
UC_AR ..> UC_LOAD : <<include>>
UC_GEN3C_IMG ..> UC_LOAD : <<include>>

note bottom of UC_AR
  Lyra-2: Autoregressive
  spatial video generation
  with camera control
end note

note bottom of UC_GEN3C_DYN
  Generates 3D-consistent video
  from dynamic input (depth +
  camera trajectory)
end note

@enduml
```

---

```plantuml
@startuml lyra_use_case_training

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam usecase {
    BackgroundColor #F8F9FA
    BorderColor #495057
    FontColor #212529
    ArrowColor #495057
}
skinparam actor {
    BorderColor #495057
    FontColor #212529
    BackgroundColor #DEE2E6
}
skinparam package {
    BackgroundColor #E9ECEF
    BorderColor #6C757D
    FontColor #212529
}
skinparam note {
    BackgroundColor #FFF9C4
    BorderColor #F0AD4E
}

title Lyra — ML Engineering & Training Lifecycle

left to right direction

actor "ML Engineer" as mle
actor "Data\nEngineer" as de
actor "Infrastructure\n/ Cluster" as infra
actor "Checkpoint\nStorage (S3/FS)" as storage

rectangle "Lyra ML Training & Engineering System" {

    package "Experiment Configuration" {
        usecase "Define Experiment\nConfiguration" as UC_CFG
        usecase "Register Config\nComponents" as UC_REG
        usecase "Override Config\nParameters" as UC_OVRD
    }

    package "Data Preparation" {
        usecase "Load Video\nDataset" as UC_DS_VID
        usecase "Load 3D/Multi-view\nDataset" as UC_DS_3D
        usecase "Apply Data\nAugmentations" as UC_AUG
        usecase "Prepare T5 Text\nEmbeddings" as UC_T5
    }

    package "Diffusion Model Training" {
        usecase "Train Text-to-World\nDiffusion Model" as UC_TRAIN_T2W
        usecase "Train Video-to-World\nDiffusion Model" as UC_TRAIN_V2W
        usecase "Train Multi-view\nDiffusion Model" as UC_TRAIN_MV
        usecase "Train World\nInterpolator" as UC_TRAIN_INTERP
        usecase "Fine-tune with\nLoRA/PEFT" as UC_LORA
    }

    package "Autoregressive Model Training" {
        usecase "Train Autoregressive\nVideo Model" as UC_TRAIN_AR
        usecase "Train Vision-Language\nAutoregressive Model" as UC_TRAIN_VLM
    }

    package "Tokenizer Training" {
        usecase "Train Continuous\nVideo Tokenizer" as UC_TRAIN_TOK_C
        usecase "Train Discrete\nVideo Tokenizer" as UC_TRAIN_TOK_D
        usecase "Export JIT\nTokenizer" as UC_JIT
    }

    package "Distributed Training Infrastructure" {
        usecase "Configure DDP\n/ FSDP / Tensor Parallel" as UC_DIST
        usecase "Manage EMA\nModel Weights" as UC_EMA
        usecase "Control Gradient\nClipping" as UC_GRAD
        usecase "Set Up Context\nParallel (CP)" as UC_CP
    }

    package "Checkpointing" {
        usecase "Save Model\nCheckpoint" as UC_SAVE
        usecase "Resume from\nCheckpoint" as UC_RESUME
        usecase "Save/Load via\nDCP (Distributed)" as UC_DCP
        usecase "Save EMA\nCheckpoint" as UC_EMA_CKPT
    }

    package "Evaluation & Metrics" {
        usecase "Compute PSNR /\nSSIM / LPIPS" as UC_METRICS
        usecase "Generate Validation\nSamples" as UC_VAL_SAMPLES
        usecase "Run Environment\nSanity Check" as UC_ENV_CHECK
    }
}

' ML Engineer interactions
mle --> UC_CFG
mle --> UC_TRAIN_T2W
mle --> UC_TRAIN_V2W
mle --> UC_TRAIN_MV
mle --> UC_TRAIN_INTERP
mle --> UC_LORA
mle --> UC_TRAIN_AR
mle --> UC_TRAIN_VLM
mle --> UC_TRAIN_TOK_C
mle --> UC_TRAIN_TOK_D
mle --> UC_JIT
mle --> UC_METRICS
mle --> UC_VAL_SAMPLES
mle --> UC_ENV_CHECK

' Data Engineer interactions
de --> UC_DS_VID
de --> UC_DS_3D
de --> UC_T5

' Infrastructure interactions
infra --> UC_DIST
infra --> UC_CP

' Storage interactions
storage --> UC_SAVE
storage --> UC_RESUME
storage --> UC_DCP

' Include relationships
UC_CFG ..> UC_REG : <<include>>
UC_CFG ..> UC_OVRD : <<include>>

UC_TRAIN_T2W ..> UC_DS_VID : <<include>>
UC_TRAIN_T2W ..> UC_AUG : <<include>>
UC_TRAIN_T2W ..> UC_DIST : <<include>>
UC_TRAIN_T2W ..> UC_GRAD : <<include>>
UC_TRAIN_T2W ..> UC_EMA : <<include>>
UC_TRAIN_T2W ..> UC_SAVE : <<include>>
UC_TRAIN_T2W ..> UC_CFG : <<include>>

UC_TRAIN_V2W ..> UC_DS_VID : <<include>>
UC_TRAIN_V2W ..> UC_DIST : <<include>>
UC_TRAIN_V2W ..> UC_EMA : <<include>>
UC_TRAIN_V2W ..> UC_SAVE : <<include>>

UC_TRAIN_AR ..> UC_DS_VID : <<include>>
UC_TRAIN_AR ..> UC_DIST : <<include>>
UC_TRAIN_AR ..> UC_T5 : <<include>>

UC_TRAIN_TOK_C ..> UC_DS_VID : <<include>>
UC_TRAIN_TOK_C ..> UC_JIT : <<extend>>

UC_LORA ..> UC_TRAIN_T2W : <<extend>>
UC_LORA ..> UC_TRAIN_V2W : <<extend>>

UC_SAVE ..> UC_EMA_CKPT : <<extend>>
UC_DCP ..> UC_SAVE : <<extend>>

UC_RESUME ..> UC_DCP : <<include>>

note right of UC_LORA
  LoRA training applies to both
  Lyra-1 diffusion models and
  Lyra-2 WAN-based models
end note

note right of UC_DCP
  Distributed Checkpoint Protocol
  supports async saving to S3
  or local filesystem
end note

note bottom of UC_TRAIN_AR
  Includes Lyra-2 WAN
  autoregressive training
  with camera conditioning
end note

@enduml
```

---

**Summary of diagrams:**

1. **Generation Use Case Diagram** — covers the end-user-facing capabilities:
   - Text-to-World and Video-to-World generation (Lyra-1 diffusion pipelines)
   - 3D-consistent generation (Gen3C) with single-image or dynamic video input
   - Autoregressive world generation with camera trajectory control (Lyra-2)
   - Content safety guardrails (Aegis, LlamaGuard3, video safety, face blur, blocklist)
   - Tokenization (encode/decode images and videos)
   - Checkpoint management

2. **ML Engineering Use Case Diagram** — covers the training/dev lifecycle:
   - Experiment configuration and registration (LazyConfig/Hydra system)
   - Dataset preparation (video, 3D/multi-view, T5 embeddings)
   - Training for diffusion models, autoregressive models, and tokenizers
   - Distributed infrastructure (DDP, FSDP, Tensor Parallel, Context Parallel)
   - Checkpointing (DCP async saves to S3, EMA model saving)
   - Evaluation metrics (PSNR, SSIM, LPIPS)
