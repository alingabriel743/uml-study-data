Looking at the IR for the **lyra** project, I can identify four key runtime flows spanning both Lyra-1 (cosmos_predict1) and Lyra-2 sub-systems. I'll produce focused sequence diagrams for each major pipeline.

---

```plantuml
@startuml lyra1_diffusion_t2w_pipeline

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceArrowThickness 2
skinparam SequenceBoxBackgroundColor #E9ECEF
skinparam SequenceBoxBorderColor #6C757D
skinparam ParticipantBackgroundColor #F8F9FA
skinparam ParticipantBorderColor #495057
skinparam ActorBorderColor #495057

title Cosmos Diffusion Text-to-World Generation Pipeline (Lyra-1)

actor User
participant "DiffusionText2World\nGenerationPipeline" as pipeline
participant "GuardrailRunner\n(Text)" as text_gr
participant "AutoRegressiveModel\n(PromptUpsampler)" as upsampler
participant "CosmosT5TextEncoder" as t5
participant "DiffusionT2WModel" as model
participant "GeneralDIT" as net
participant "Sampler" as sampler
participant "VideoJITTokenizer" as tokenizer
participant "GuardrailRunner\n(Video)" as vid_gr

User -> pipeline: generate(prompt, negative_prompt,\n  word_limit_to_skip_upsampler)
activate pipeline

pipeline -> text_gr: run_safety_check(prompt)
activate text_gr
text_gr --> pipeline: (is_safe: bool, message: str)
deactivate text_gr

alt prompt is unsafe
    pipeline --> User: None  (content blocked)
end

opt word_limit not exceeded
    pipeline -> upsampler: run_chat_completion(model, prompt)
    activate upsampler
    upsampler --> pipeline: enhanced_prompt: str
    deactivate upsampler
end

pipeline -> t5: __call__(enhanced_prompt)
activate t5
t5 --> pipeline: prompt_embedding, prompt_mask
deactivate t5

pipeline -> model: generate_samples_from_batch(\n  data_batch, guidance, seed, state_shape, num_steps)
activate model

model -> model: _get_conditions(data_batch,\n  is_negative_prompt=False)
model -> model: get_x0_fn_from_batch(data_batch, guidance)

loop denoising steps (num_steps)
    model -> net: forward(x, timesteps, crossattn_emb,\n  fps, image_size, padding_mask, data_type)
    activate net
    net --> model: denoised_tensor
    deactivate net
end

model -> sampler: differential_equation_solver(x0_fn, sigmas, solver_cfg)
activate sampler
sampler --> model: samples: Tensor
deactivate sampler

model --> pipeline: generated_latents: Tensor
deactivate model

pipeline -> tokenizer: decode(generated_latents)
activate tokenizer
tokenizer --> pipeline: video_frames: np.ndarray
deactivate tokenizer

pipeline -> vid_gr: postprocess(video_frames)
activate vid_gr
vid_gr --> pipeline: processed_frames: np.ndarray
deactivate vid_gr

pipeline --> User: (video_frames, enhanced_prompt)
deactivate pipeline

@enduml
```

---

```plantuml
@startuml lyra1_ar_video2world_pipeline

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceArrowThickness 2
skinparam SequenceBoxBackgroundColor #E9ECEF
skinparam SequenceBoxBorderColor #6C757D
skinparam ParticipantBackgroundColor #F8F9FA
skinparam ParticipantBorderColor #495057
skinparam ActorBorderColor #495057

title Cosmos Autoregressive Video-to-World Generation Pipeline (Lyra-1)

actor User
participant "ARVideo2World\nGenerationPipeline" as pipeline
participant "GuardrailRunner\n(Text)" as text_gr
participant "CosmosT5TextEncoder" as t5
participant "DiscreteVideoFSQ\nStateDictTokenizer" as vid_tok
participant "AutoRegressiveModel" as ar_model
participant "Transformer" as transformer
participant "LatentDiffusionDecoderModel" as diff_dec
participant "GuardrailRunner\n(Video)" as vid_gr

User -> pipeline: generate(inp_prompt, inp_vid,\n  num_input_frames, seed, sampling_config)
activate pipeline

pipeline -> text_gr: run_safety_check(inp_prompt)
activate text_gr
text_gr --> pipeline: (is_safe, message)
deactivate text_gr

pipeline -> t5: __call__([inp_prompt])
activate t5
t5 --> pipeline: prompt_embedding: Tensor\nprompt_mask: Tensor
deactivate t5

pipeline -> vid_tok: encode(inp_vid)
activate vid_tok
vid_tok --> pipeline: video_tokens: Tensor
deactivate vid_tok

pipeline -> ar_model: generate_video_from_tokens(\n  prompt_tokens, latent_shape, video_start_boundary,\n  max_gen_len, sampling_config)
activate ar_model

ar_model -> ar_model: embed_vision_language_features(\n  input_ids, images)

loop autoregressive generation (max_gen_len tokens)
    ar_model -> transformer: forward(tokens, input_pos,\n  context, context_mask)
    activate transformer
    transformer --> ar_model: logits: Tensor
    deactivate transformer

    ar_model -> ar_model: sample_top_p(logits, temperature, top_p)
    note right: Sample next discrete token
end

ar_model --> pipeline: (out_videos, indices_tensor, t5_emb)
deactivate ar_model

pipeline -> diff_dec: generate_samples_from_batch(\n  data_batch, guidance, seed, num_steps)
activate diff_dec
diff_dec -> diff_dec: _add_latent_conditions_to_data_batch(data_batch)
diff_dec -> diff_dec: _get_conditions(data_batch)
diff_dec --> pipeline: decoded_video: Tensor
deactivate diff_dec

pipeline -> vid_tok: decode(decoded_video)
activate vid_tok
vid_tok --> pipeline: pixel_frames: np.ndarray
deactivate vid_tok

pipeline -> vid_gr: postprocess(pixel_frames)
activate vid_gr
vid_gr --> pipeline: safe_frames: np.ndarray
deactivate vid_gr

pipeline --> User: video_frames: np.ndarray
deactivate pipeline

@enduml
```

---

```plantuml
@startuml lyra2_training_step

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceArrowThickness 2
skinparam SequenceBoxBackgroundColor #E9ECEF
skinparam SequenceBoxBorderColor #6C757D
skinparam ParticipantBackgroundColor #F8F9FA
skinparam ParticipantBorderColor #495057
skinparam ActorBorderColor #495057

title Lyra-2 WANDiffusionModel Training Step

participant "ImaginaireTrainer" as trainer
participant "WANDiffusionModel\n(Lyra2Model)" as model
participant "Wan2pt1VAEInterface\n(tokenizer)" as vae
participant "GeneralConditioner" as conditioner
participant "Lyra2WanModel\n(network)" as net
participant "RectifiedFlow\n(scheduler)" as scheduler
participant "EMAModelTracker" as ema
participant "CallBackGroup" as callbacks

trainer -> model: training_step(data_batch, iteration)
activate model

model -> model: get_data_and_condition(data_batch)
activate model #lightblue

model -> vae: encode(video_frames)
activate vae
vae --> model: latent_state B×C×T×H×W
deactivate vae

model -> conditioner: _forward(data_batch, override_dropout_rate)
activate conditioner
conditioner --> model: condition: T2VCondition
deactivate conditioner

model --> model: x0, condition
deactivate model

model -> scheduler: sample_train_time(batch_size)
activate scheduler
scheduler --> model: u: Tensor  (timestep in [0,1])
deactivate scheduler

model -> scheduler: get_sigmas(u, tensor_kwargs)
activate scheduler
scheduler --> model: sigma: Tensor
deactivate scheduler

model -> scheduler: get_interpolation(x0, noise, sigma)
activate scheduler
scheduler --> model: xt (noisy latent)
deactivate scheduler

model -> net: forward(xt_B_C_T_H_W, timesteps_B_T, crossattn_emb,\n  y_B_C_T_H_W, padding_mask, seq_len)
activate net
net --> model: flow_prediction: Tensor
deactivate net

model -> model: _convert_flow_pred_to_x0(scheduler, flow_pred, xt, timestep)
model -> model: compute weighted MSE loss

model --> trainer: (output_batch, loss)
deactivate model

trainer -> callbacks: on_after_backward(model_ddp, iteration)
activate callbacks
callbacks --> trainer
deactivate callbacks

trainer -> model: on_before_zero_grad(optimizer, scheduler, iteration)
activate model
model --> trainer
deactivate model

trainer -> ema: update_average(src_model, tgt_model, beta)
activate ema
ema --> trainer
deactivate ema

@enduml
```

---

```plantuml
@startuml lyra2_ar_spatial_inference

skinparam classAttributeIconSize 0
skinparam shadowing false
skinparam defaultFontName "Segoe UI"
skinparam defaultFontSize 12
skinparam backgroundColor #FEFEFE
skinparam sequenceArrowThickness 2
skinparam SequenceBoxBackgroundColor #E9ECEF
skinparam SequenceBoxBorderColor #6C757D
skinparam ParticipantBackgroundColor #F8F9FA
skinparam ParticipantBorderColor #495057
skinparam ActorBorderColor #495057

title Lyra-2 Autoregressive Spatial Video Inference

actor User
participant "run_lyra2_sample()" as runner
participant "Lyra2InferencePipeline" as pipeline
participant "Lyra2Model" as model
participant "Wan2pt1VAEInterface" as vae
participant "Sparse3DCache" as cache_3d
participant "Lyra2WanModel\n(network)" as net
participant "DepthModel\n(DA3)" as depth_model
participant "UMT5EncoderModel" as t5

User -> runner: run_lyra2_sample(model, data_batch, args,\n  da3_model, process_group)
activate runner

runner -> pipeline: new Lyra2InferencePipeline(\n  model, args, first_frame, first_depth,\n  first_cam_w2c, first_intrinsics, da3_model)
activate pipeline

pipeline -> vae: encode(init_video_segment)
activate vae
vae --> pipeline: initial_latents: Tensor
deactivate vae

pipeline -> cache_3d: add(depth_B_1_H_W, w2c_B_4_4, K_B_3_3, latent_index=0)
activate cache_3d
cache_3d --> pipeline: cache_initialized
deactivate cache_3d

loop for each camera trajectory chunk
    runner -> pipeline: autoregressive_step(\n  cam_w2c_chunk, intrinsics_chunk,\n  t5_text_embeddings, neg_t5_embeddings)
    activate pipeline

    pipeline -> cache_3d: render_cache(target_w2cs, target_intrinsics,\n  render_depth=True)
    activate cache_3d
    cache_3d --> pipeline: rendered_warp_images\nrendered_warp_masks
    deactivate cache_3d

    pipeline -> model: prepare_latent_conditon(\n  condition_state, condition_state_mask, dtype)
    activate model
    model --> pipeline: condition_latent: Tensor
    deactivate model

    pipeline -> model: get_x0_fn_from_batch(data_batch, guidance, seed)
    activate model
    model -> model: _apply_camera_controls(\n  cond_latent, selected_idx, video_indices,\n  spatial_selected_frame_ids, spatial_cache)
    model --> pipeline: x0_fn: Callable
    deactivate model

    pipeline -> model: inference_dmd(\n  history_latents, cond_latent, guidance,\n  seed, num_steps, shift)
    activate model

    model -> net: forward(x_B_C_T_H_W, timesteps_B_T,\n  crossattn_emb, y_B_C_T_H_W, ...)
    activate net
    net --> model: denoised_output: Tensor
    deactivate net

    model --> pipeline: new_latents: Tensor
    deactivate model

    pipeline -> vae: decode(new_latents)
    activate vae
    vae --> pipeline: new_frames: Tensor
    deactivate vae

    pipeline -> depth_model: predict depth & intrinsics(new_frames)
    activate depth_model
    depth_model --> pipeline: new_depths: Tensor\nintrinsics: Tensor
    deactivate depth_model

    pipeline -> pipeline: _update_depth_cache(end_px_idx)

    pipeline -> cache_3d: add(new_depths, w2c, K, latent_index)
    activate cache_3d
    cache_3d --> pipeline: cache_updated
    deactivate cache_3d

    pipeline --> runner: step_frames: Tensor
    deactivate pipeline
end

runner -> pipeline: build_outputs(da3_gs_export_stem, log_prefix)
activate pipeline
pipeline --> runner: output_videos, gaussians_ply
deactivate pipeline

runner --> User: (output_videos, point_cloud)
deactivate runner

@enduml
```
