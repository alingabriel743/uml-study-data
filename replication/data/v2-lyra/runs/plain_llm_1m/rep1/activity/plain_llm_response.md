Looking at the IR for the **lyra** project, I can identify several major workflows:

1. Diffusion world generation pipeline (text/video → world)
2. Autoregressive video generation pipeline
3. Model training loop with callbacks/checkpointing
4. Lyra-2 autoregressive spatial inference

Here are four focused activity diagrams:

---

```plantuml
@startuml diffusion_world_generation
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam activity {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam swimlane {
    BorderColor #6C757D
    TitleBackgroundColor #E9ECEF
}

title Cosmos Diffusion World Generation Pipeline\n(DiffusionText2WorldGenerationPipeline / DiffusionVideo2WorldGenerationPipeline)

|User|
start
:Provide text prompt\n(+ optional image/video path for V2W);

|Guardrail System|
if (Guardrail enabled?) then (yes)
    :Run text safety check\n(Aegis + LlamaGuard3 + Blocklist);
    if (Prompt is safe?) then (yes)
    else (no)
        :Return None\n— unsafe prompt blocked;
        stop
    endif
endif

|Prompt Upsampler|
if (Prompt upsampler enabled?) then (yes)
    :Load Pixtral / LLM upsampler model;
    if (Video2World mode?) then (yes)
        :Analyze input image/video\nwith VLM (Pixtral);
        :Generate enriched prompt description;
    else (no)
        :Expand text prompt\nwith additional detail;
    endif
    :Offload upsampler model from GPU;
endif

|Text Encoder|
:Run T5 text embedding\non (upsampled) prompt;
:Produce prompt embedding tensor\n+ attention mask;

|Video Tokenizer|
if (Video2World mode?) then (yes)
    :Load input image/video frames;
    :Encode with VideoJITTokenizer\n(spatial ×16, temporal ×8 compression);
    :Produce condition latent tensor;
endif

|Diffusion Model (DiT)|
:Load GeneralDIT /\nVideoExtendGeneralDIT network;
:Prepare data batch\n(embeddings, FPS, image_size, padding_mask);
if (Video2World mode?) then (yes)
    :Inject video conditioning:\nadd_condition_video_indicator_and_video_input_mask;
    :Augment condition frames\nwith noise (condition_augment_sigma);
endif
:Run ODE solver (res_sampler)\n— multi-step denoising loop;
:Adams-Bashforth or Runge-Kutta steps\nacross noise schedule;
:Produce clean latent tensor;

|Video Tokenizer|
:Decode latent → pixel space\nwith VideoJITTokenizer decoder;
:Convert tensor → numpy video array\n(T×H×W×C, uint8);

|Guardrail System|
if (Video guardrail enabled?) then (yes)
    :Run VideoContentSafetyFilter\n(SigLIP encoder + SafetyClassifier);
    if (Video is safe?) then (yes)
    else (no)
        :Return None — unsafe content;
        stop
    endif
    :Apply RetinaFaceFilter\n(detect + blur faces);
endif

|User|
:Return (video_array, upsampled_prompt);
stop

@enduml
```

---

```plantuml
@startuml autoregressive_video_generation
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam activity {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam swimlane {
    BorderColor #6C757D
    TitleBackgroundColor #E9ECEF
}

title Cosmos Autoregressive Video2World Generation Pipeline\n(ARVideo2WorldGenerationPipeline)

|User|
start
:Provide input video frames\n+ text prompt\n+ SamplingConfig;

|ARVideo2WorldGenerationPipeline|
:Initialize pipeline:\n— load AR model (Transformer)\n— load tokenizer\n— load text encoder;

|Guardrail System|
if (Guardrail enabled?) then (yes)
    :Run text safety check\n(Aegis + LlamaGuard3 + Blocklist);
    if (Prompt is safe?) then (yes)
    else (no)
        :Block generation and return None;
        stop
    endif
endif

|Text Encoder|
:Encode text prompt with T5TextEncoder;
:Produce prompt_embedding\n+ prompt_mask tensors;

|Video Tokenizer|
:Load input video frames\n(num_input_frames = 9 default);
:Tokenize with DiscreteVideoFSQTokenizer\n(FSQ quantization to discrete indices);
:Get video token indices\n(3D spatial-temporal grid);

|AR Transformer|
:Assemble prompt tokens:\n  [system] + [text] + [video] tokens;
:Prefill KV-cache with context tokens;

repeat
    :decode_one_token:\n  — forward pass through Transformer\n  — sample next token\n    (temperature + top-p + top-k);
    :Append new token to sequence;
repeat while (Generated < max_gen_len\nand not stop_token?) is (yes)
-> (no);

:Collect output video token indices;
:Reshape indices → latent spatial grid\n(T × H × W);

|Diffusion Decoder|
if (Diffusion decoder enabled?) then (yes)
    :Load LatentDiffusionDecoderModel;
    :Add latent conditions to data batch;
    :Run diffusion upsampling:\n  generate_samples_from_batch\n  (35 denoising steps);
    :Produce high-quality video latents;
else (no)
    :Decode discrete tokens directly\nwith FSQTokenizer decoder;
endif

|Video Tokenizer|
:Decode latent → pixel frames\nwith causal VAE decoder;
:Return video numpy array;

|Guardrail System|
if (Video guardrail enabled?) then (yes)
    :VideoContentSafetyFilter + FaceBlurFilter;
endif

|User|
:Return generated video array;
stop

@enduml
```

---

```plantuml
@startuml model_training_pipeline
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam activity {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam swimlane {
    BorderColor #6C757D
    TitleBackgroundColor #E9ECEF
}

title Cosmos / Lyra Model Training Pipeline\n(Trainer + DiffusionModel / AutoRegressiveTrainingModel / TokenizerModel)

|Configuration|
start
:Load experiment config\n(LazyConfig / Hydra / attrs);
:Apply CLI overrides;
:Validate configuration;

|Distributed Runtime|
:Initialize distributed training\n(NCCL process group);
:Setup parallelism:\n  — DDP / FSDP / Tensor Parallel\n  — Context Parallel groups;

|Model Initialization|
:Instantiate model\n(DiffusionModel / AutoRegressiveTrainingModel\n/ TokenizerModel);
:Build network + conditioner + tokenizer\n(model.build_model());
if (EMA enabled?) then (yes)
    :Create EMAModelTracker\n(beta=0.9999) or PowerEMATracker;
endif
:Initialize optimizer (AdamW / FusedAdam)\n+ LR scheduler;

|Checkpointer|
if (Resume checkpoint exists?) then (yes)
    :Read latest checkpoint path\n(latest_checkpoint.txt);
    :Broadcast state dict from rank-0\n(load_broadcast_state_dict);
    :Restore model weights;
    :Restore optimizer + scheduler state;
    :Restore iteration counter;
else (no)
    :Start from iteration 0;
endif

:on_train_start callback\n(LowPrecisionCallback, EMAModelCallback...);

|Training Loop|
repeat

    :on_before_dataloading;
    :Fetch next data batch\nfrom DataLoader;
    :on_after_dataloading;
    :Move batch to GPU;
    
    :on_training_step_start\n(TorchCompile, ExpandLossMask...);
    :on_before_forward;
    
    :model.training_step(data_batch, iteration)\n  — normalize video batch\n  — draw sigma + epsilon (EDM SDE)\n  — compute conditions (conditioner.forward)\n  — network forward (DiT)\n  — compute EDM loss;
    
    :on_after_forward;
    :Scale loss by grad_accum_factor;
    :on_before_backward;
    :loss.backward();
    :on_after_backward\n(allreduce layernorm grads for TP);
    
    if (Gradient accumulation step?) then (yes)
        :on_before_optimizer_step\n(GradClip: clip_grad_norm_,\n monitor grad magnitude);
        :optimizer.step();
        :scheduler.step();
        :on_before_zero_grad\n(EMA update: update_average());
        :optimizer.zero_grad();
    endif
    
    :on_training_step_end\n(log metrics, IterSpeed, EveryN callbacks);
    
    if (Checkpoint interval?) then (yes)
        :on_save_checkpoint_start;
        :checkpointer.save(model, optim,\n  scheduler, scaler, iteration)\n  — async save option;
        :Write latest_checkpoint.txt;
        :on_save_checkpoint_end;
    endif
    
    if (Validation interval?) then (yes)
        :on_validation_start;
        :Run validation steps;
        :on_validation_end;
    endif

repeat while (iteration < max_iterations?) is (yes)
-> (no);

:on_train_end;
:checkpointer.finalize()\n(wait for pending async saves);
stop

@enduml
```

---

```plantuml
@startuml lyra2_ar_spatial_inference
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam activity {
    BackgroundColor #F8F9FA
    BorderColor #495057
    ArrowColor #495057
    FontColor #212529
}
skinparam swimlane {
    BorderColor #6C757D
    TitleBackgroundColor #E9ECEF
}

title Lyra-2 Autoregressive Spatial World Generation\n(Lyra2InferencePipeline + run_lyra2_sample)

|User / CLI|
start
:Provide:\n  — input image or video\n  — camera trajectory spec\n  — text prompt(s)\n  — AR chunk parameters;

|Depth & Pose Estimation|
:Load MoGe / DA3 depth model;
:Predict monocular depth\nfor first frame;
:Estimate intrinsics\n(focal length from depth model);
:Set initial world-to-camera matrix (w2c);

|Camera Trajectory Builder|
:Build full camera trajectory\n(create_spiral / create_horizontal /\n create_orbit trajectory);
:Split into AR chunks\n(chunk_size frames each);

|Text Encoder|
:Encode text prompt(s)\nwith UMT5EncoderModel;
:Produce T5 embeddings + neg embeddings;

|Lyra2InferencePipeline|
:Initialize pipeline:\n— Prime VAE encoder cache\n  with history frames\n— Initialize Sparse3DCache\n  (depth + pose + frame data)\n— Load history latents buffer;

fork
    :Prepare CLIP image embeddings\n(Wan2pt1CLIPEmbLyra2)\nfor first frame reference;
fork again
    :Prepare initial depth buffer\n(B×1×H×W tensor);
end fork

repeat : For each AR chunk

    |Camera Controls|
    :Receive next cam_w2c_chunk\n+ intrinsics_chunk;
    :Compute Plücker ray embeddings\nfor new camera positions\n(ray_condition function);
    :Select temporal history indices\nfrom latent buffer;
    :Sample spatial history frames\nfrom Sparse3DCache\n(forward warp to new viewpoint);
    
    |Lyra2Model (Diffusion)|
    :Encode conditioning frames\nwith WanVAE (chunk-wise);
    :Augment condition latents\nwith noise (augment_sigma);
    
    fork
        :Apply camera Plücker\nto model blocks\n(Lyra2AttentionBlock cam_dim);
    fork again
        :Apply spatial buffer features\n(buffer_B_C_T_H_W input);
    end fork
    
    :Run Lyra2WanModel forward:\n  — patch embedding (patchify_lyra2)\n  — 32 transformer blocks\n  — cross-attn (text + CLIP)\n  — rope 3D position embeddings\n  — head decode;
    :ODE solver step\n(RectifiedFlow + UniPC scheduler);
    :Decode new frames with WanVAE;
    
    |Depth & Cache Update|
    :Run DA3 depth estimation\non newly generated frames;
    if (VIPE enabled?) then (yes)
        :Run VIPEWrapper\nfor pose alignment;
        :Align predicted poses\nto model coordinate system;
    endif
    :Update Sparse3DCache:\n  — add new depth + pose entries\n  — store RGB values;
    :Append decoded frames\nto history buffer;
    :Update history latents;
    
    if (Snapshot requested?) then (yes)
        :Save pipeline snapshot\nfor potential revert;
    endif

repeat while (More AR chunks?) is (yes)
-> (no);

|Output Assembly|
:Collect all generated video frames\n(concatenate chunks);
:Save video (.mp4) output;
if (DA3 GS export enabled?) then (yes)
    :Build 3D Gaussian reconstruction\nvia VIPE + DA3;
    :Export .ply Gaussian file;
endif

|User / CLI|
:Return final video array\n+ camera trajectory\n+ depth maps;
stop

@enduml
```
