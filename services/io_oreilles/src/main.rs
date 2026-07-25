use anyhow::{Context, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Sample;
use rubato::{Resampler, SincFixedIn, SincInterpolationType, SincInterpolationParameters, WindowFunction};
use serde::Serialize;
use tokio::sync::mpsc;
use tracing::{info, error};

use std::path::PathBuf;

mod segmenter;
use segmenter::{FrameOutcome, VadSegmenter};

mod discord_audio;
use discord_audio::{stereo_i16_to_mono_f32, DiscordVoiceFrame, SpeakerPipeline};

#[derive(Serialize)]
struct TranscriptionEvent {
    text: String,
    /// Nom du locuteur Discord ; absent pour le micro local (pas d'identité attachée).
    #[serde(skip_serializing_if = "Option::is_none")]
    speaker: Option<String>,
}

/// Le mode brut (audio non transcrit sur `io.user.speak.raw`) ne dépend que de
/// `RAW_AUDIO`. `--discord` ne l'implique plus : l'audio brut d'un salon vocal
/// finissait dans l'historique Postgres et faisait dépasser le `max_payload`
/// NATS sur `hippocampe.context.ready`, bloquant tout le pipeline.
fn is_raw_mode(raw_audio_env: Option<&str>) -> bool {
    matches!(raw_audio_env, Some("true") | Some("1"))
}

/// GPU quand le binaire est compilé avec `--features cuda`, CPU sinon. Le choix est
/// figé à la compilation : `Device::CUDA` n'existe que si CTranslate2 a été bâti
/// avec CUDA, un fallback à l'exécution n'aurait rien à quoi retomber.
fn stt_device() -> ct2rs::Device {
    #[cfg(feature = "cuda")]
    {
        ct2rs::Device::CUDA
    }
    #[cfg(not(feature = "cuda"))]
    {
        ct2rs::Device::CPU
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_mode_only_when_env_set() {
        assert!(is_raw_mode(Some("1")));
        assert!(is_raw_mode(Some("true")));
    }

    #[test]
    fn discord_mode_transcribes_by_default() {
        // Le drapeau --discord n'entre plus dans la décision : sans RAW_AUDIO, on transcrit.
        assert!(!is_raw_mode(None));
        assert!(!is_raw_mode(Some("0")));
        assert!(!is_raw_mode(Some("false")));
    }

    #[test]
    fn transcription_event_omits_speaker_for_local_mic() {
        let json = serde_json::to_string(&TranscriptionEvent {
            text: "bonjour".to_string(),
            speaker: None,
        })
        .unwrap();
        assert_eq!(json, r#"{"text":"bonjour"}"#);
    }

    #[test]
    fn transcription_event_carries_discord_speaker() {
        let json = serde_json::to_string(&TranscriptionEvent {
            text: "bonjour".to_string(),
            speaker: Some("Milo".to_string()),
        })
        .unwrap();
        assert_eq!(json, r#"{"text":"bonjour","speaker":"Milo"}"#);
    }
}

fn create_wav_data(samples: &[f32]) -> Vec<u8> {
    let sample_rate = 16000u32;
    let num_channels = 1u16;
    let bits_per_sample = 16u16;
    
    let num_samples = samples.len();
    let data_size = num_samples * 2; // 2 bytes per sample (16-bit)
    let file_size = 36 + data_size;
    
    let mut wav = Vec::with_capacity(44 + data_size);
    
    // RIFF header
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(file_size as u32).to_le_bytes());
    wav.extend_from_slice(b"WAVE");
    
    // fmt subchunk
    wav.extend_from_slice(b"fmt ");
    wav.extend_from_slice(&16u32.to_le_bytes()); // subchunk1 size
    wav.extend_from_slice(&1u16.to_le_bytes());  // audio format (1 = PCM)
    wav.extend_from_slice(&num_channels.to_le_bytes());
    wav.extend_from_slice(&sample_rate.to_le_bytes());
    let byte_rate = sample_rate * (num_channels as u32) * (bits_per_sample as u32) / 8;
    wav.extend_from_slice(&byte_rate.to_le_bytes());
    let block_align = num_channels * bits_per_sample / 8;
    wav.extend_from_slice(&block_align.to_le_bytes());
    wav.extend_from_slice(&bits_per_sample.to_le_bytes());
    
    // data subchunk
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&(data_size as u32).to_le_bytes());
    
    // Write PCM samples (convert f32 to i16)
    for &sample in samples {
        let clamped = sample.clamp(-1.0, 1.0);
        let s = if clamped >= 0.0 {
            (clamped * 32767.0) as i16
        } else {
            (clamped * 32768.0) as i16
        };
        wav.extend_from_slice(&s.to_le_bytes());
    }
    
    wav
}


/// Try to locate libonnxruntime.so for the `ort` crate's `load-dynamic` feature.
fn find_ort_dylib() -> Option<PathBuf> {
    // 1. Try asking Python where onnxruntime is installed
    if let Ok(output) = std::process::Command::new("python3")
        .args(["-c", "import onnxruntime; import os; print(os.path.join(os.path.dirname(onnxruntime.__file__), 'capi'))"])
        .output()
    {
        if output.status.success() {
            let capi_dir = String::from_utf8_lossy(&output.stdout).trim().to_string();
            // Find any libonnxruntime.so* in that directory
            if let Ok(entries) = std::fs::read_dir(&capi_dir) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let name_str = name.to_string_lossy();
                    if name_str.starts_with("libonnxruntime.so") && name_str != "libonnxruntime_providers_shared.so" {
                        return Some(entry.path());
                    }
                }
            }
        }
    }

    // 2. Check alongside the executable
    if let Ok(exe) = std::env::current_exe() {
        let candidate = exe.parent().unwrap().join("libonnxruntime.so");
        if candidate.exists() {
            return Some(candidate);
        }
    }

    None
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info,ort=warn")),
        )
        .init();
    info!("Starting io_oreilles service");

    // Auto-detect ORT_DYLIB_PATH if not already set (required for ort load-dynamic)
    if std::env::var("ORT_DYLIB_PATH").is_err() {
        if let Some(path) = find_ort_dylib() {
            info!("Auto-detected ORT_DYLIB_PATH: {}", path.display());
            // SAFETY: called before any threads are spawned
            unsafe { std::env::set_var("ORT_DYLIB_PATH", &path) };
        } else {
            tracing::warn!("ORT_DYLIB_PATH not set and libonnxruntime.so not found. Install onnxruntime via pip or set ORT_DYLIB_PATH manually.");
        }
    }

    // 1. Setup NATS connection
    let nats_url = std::env::var("NATS_URL").unwrap_or_else(|_| "nats://localhost:4222".to_string());
    let client = async_nats::connect(&nats_url).await.context("Failed to connect to NATS")?;
    info!("Connected to NATS");

    // --discord disables local mic capture for this run in favour of per-speaker
    // audio streamed over NATS from io_discord (see discord_audio.rs).
    let discord_mode = std::env::args().any(|a| a == "--discord");

    // 2. Setup Thread 2 -> Thread 3 Queue. The identity rides along so downstream
    // (cortex) can attribute the segment to a Discord speaker; local mic audio has none.
    let (tx_speech, mut rx_speech) = mpsc::channel::<(Vec<f32>, Option<String>)>(32);

    if !discord_mode {
    // 3. Setup Ringbuf for Thread 1 -> Thread 2
    // Arbitrary size: 160_000 samples is 10s at 16kHz, roughly 3.3s at 48kHz.
    let rb = ringbuf::HeapRb::<f32>::new(160_000);
    let (mut prod, mut cons) = rb.split();

    // 4. Setup Thread 1 (OS Audio Callback via CPAL)
    let host = cpal::default_host();
    let device = host.default_input_device().context("No default input device available")?;
    info!("Input device: {}", device.name()?);

    let config = device.default_input_config()?;
    let sample_rate = config.sample_rate().0;
    let channels = config.channels();
    info!("Input config: {} Hz, {} channels", sample_rate, channels);

    let err_fn = |err| error!("an error occurred on stream: {}", err);

    let stream = match config.sample_format() {
        cpal::SampleFormat::F32 => {
            device.build_input_stream(
                &config.into(),
                move |data: &[f32], _: &cpal::InputCallbackInfo| {
                    let mono_data: Vec<f32> = data.iter().step_by(channels as usize).copied().collect();
                    let _ = prod.push_slice(&mono_data);
                },
                err_fn,
                None, // optional timeout
            )?
        }
        cpal::SampleFormat::I16 => {
            device.build_input_stream(
                &config.into(),
                move |data: &[i16], _: &cpal::InputCallbackInfo| {
                    let mono_data: Vec<f32> = data.iter().step_by(channels as usize).map(|&s| s.to_sample::<f32>()).collect();
                    prod.push_slice(&mono_data);
                },
                err_fn,
                None,
            )?
        }
        cpal::SampleFormat::U16 => {
             device.build_input_stream(
                &config.into(),
                move |data: &[u16], _: &cpal::InputCallbackInfo| {
                    let mono_data: Vec<f32> = data.iter().step_by(channels as usize).map(|&s| s.to_sample::<f32>()).collect();
                    prod.push_slice(&mono_data);
                },
                err_fn,
                None,
            )?
        }
        _ => anyhow::bail!("Unsupported sample format"),
    };

    stream.play()?;

    info!("Initializing Silero VAD Model...");
    use silero::{Session, StreamState, SampleRate as SileroSampleRate};
    let mut session = Session::bundled().context("Failed to load Silero VAD model. Is ORT_DYLIB_PATH set? See README.")?;
    let mut stream_state = StreamState::new(SileroSampleRate::Rate16k);
    info!("Silero VAD Model loaded.");

    // 5. Setup Thread 2 (VAD Watcher)
    std::thread::spawn(move || {
        info!("Started VAD Thread (Thread 2)");

        let target_sr = 16_000;
        let mut resampler = if sample_rate != target_sr {
            let params = SincInterpolationParameters {
                sinc_len: 256,
                f_cutoff: 0.95,
                interpolation: SincInterpolationType::Linear,
                oversampling_factor: 256,
                window: WindowFunction::BlackmanHarris2,
            };
            Some(SincFixedIn::<f32>::new(
                target_sr as f64 / sample_rate as f64,
                2.0,
                params,
                1024,
                1
            ).expect("Failed to init resampler"))
        } else {
            None
        };

        let mut segmenter = VadSegmenter::new();
        let mut internal_buf = Vec::new();
        let mut vad_buf = Vec::new();

        // Diagnostic counters
        let mut diag_samples_read: u64 = 0;
        let mut diag_samples_16k: u64 = 0;
        let mut diag_vad_frames: u64 = 0;
        let mut diag_max_prob: f32 = 0.0;
        let mut diag_max_amp: f32 = 0.0;
        let mut diag_last = std::time::Instant::now();

        loop {
            // Read from ringbuf
            let mut chunk = vec![0.0; 4096];
            let read = cons.pop_slice(&mut chunk);
            if read == 0 {
                std::thread::sleep(std::time::Duration::from_millis(5));
                // Still print diagnostics even when idle
                if diag_last.elapsed() > std::time::Duration::from_secs(2) {
                    info!(
                        "[DIAG] ringbuf_read={}, resampled_16k={}, vad_frames={}, max_prob={:.3}, max_amp={:.5}",
                        diag_samples_read, diag_samples_16k, diag_vad_frames, diag_max_prob, diag_max_amp
                    );
                    diag_samples_read = 0;
                    diag_samples_16k = 0;
                    diag_vad_frames = 0;
                    diag_max_prob = 0.0;
                    diag_max_amp = 0.0;
                    diag_last = std::time::Instant::now();
                }
                continue;
            }
            chunk.truncate(read);
            diag_samples_read += read as u64;
            let amp = chunk.iter().fold(0.0_f32, |m, x| m.max(x.abs()));
            if amp > diag_max_amp { diag_max_amp = amp; }
            internal_buf.extend(&chunk);

            // Resample logic
            let chunk_16k = if let Some(r) = &mut resampler {
                let mut required_in = r.input_frames_next();
                let mut out_16k = Vec::new();
                while internal_buf.len() >= required_in {
                    let to_process = internal_buf.drain(0..required_in).collect::<Vec<_>>();
                    let waves_in = vec![to_process];
                    match r.process(&waves_in, None) {
                        Ok(mut out) => out_16k.extend(out.pop().unwrap()),
                        Err(e) => error!("Resampling error: {}", e),
                    }
                    required_in = r.input_frames_next();
                }
                out_16k
            } else {
                let out = internal_buf.clone();
                internal_buf.clear();
                out
            };

            diag_samples_16k += chunk_16k.len() as u64;
            vad_buf.extend(chunk_16k);

            // Silero expects chunks of length 512 (or 1536).
            while vad_buf.len() >= 512 {
                let frame: Vec<f32> = vad_buf.drain(0..512).collect();
                
                match session.infer_chunk(&mut stream_state, &frame) {
                    Ok(prob) => {
                        diag_vad_frames += 1;
                        if prob > diag_max_prob { diag_max_prob = prob; }
                        match segmenter.push_frame(&frame, prob) {
                            FrameOutcome::SpeechStarted => info!("Speech started (prob: {:.2})", prob),
                            FrameOutcome::SpeechEnded(segment) => {
                                info!("Speech ended. Captured {} samples.", segment.len());
                                tx_speech.blocking_send((segment, None)).unwrap();
                            }
                            FrameOutcome::Speaking | FrameOutcome::Idle => {}
                        }
                    }
                    Err(e) => error!("VAD error: {:?}", e),
                }
            }

            // Periodic diagnostic output
            if diag_last.elapsed() > std::time::Duration::from_secs(2) {
                info!(
                    "[DIAG] ringbuf_read={}, resampled_16k={}, vad_frames={}, max_prob={:.3}, max_amp={:.5}",
                    diag_samples_read, diag_samples_16k, diag_vad_frames, diag_max_prob, diag_max_amp
                );
                diag_samples_read = 0;
                diag_samples_16k = 0;
                diag_vad_frames = 0;
                diag_max_prob = 0.0;
                diag_max_amp = 0.0;
                diag_last = std::time::Instant::now();
            }
        }
    });
    } else {
        // Discord mode: audio arrives per-speaker over NATS instead of from a
        // local device. One VAD thread services every active speaker, each with
        // its own resample/segmenter state so speakers don't interrupt each other.
        info!("Running in DISCORD mode: local microphone capture disabled.");

        let (tx_frame, rx_frame) = std::sync::mpsc::channel::<(String, String, Vec<f32>)>();

        let nats_discord = client.clone();
        tokio::spawn(async move {
            use futures::StreamExt;
            let mut sub = match nats_discord.subscribe("io.discord.voice.frame").await {
                Ok(s) => s,
                Err(e) => {
                    error!("Failed to subscribe to io.discord.voice.frame: {:?}", e);
                    return;
                }
            };
            info!("👂 Listening for Discord voice frames on 'io.discord.voice.frame'...");
            while let Some(msg) = sub.next().await {
                let frame: DiscordVoiceFrame = match serde_json::from_slice(&msg.payload) {
                    Ok(f) => f,
                    Err(e) => {
                        error!("Failed to parse Discord voice frame: {:?}", e);
                        continue;
                    }
                };
                use base64::Engine;
                let pcm_bytes = match base64::engine::general_purpose::STANDARD.decode(&frame.pcm) {
                    Ok(b) => b,
                    Err(e) => {
                        error!("Failed to decode Discord voice frame PCM: {:?}", e);
                        continue;
                    }
                };
                let mono = stereo_i16_to_mono_f32(&pcm_bytes);
                if tx_frame.send((frame.speaker_id, frame.speaker_name, mono)).is_err() {
                    break;
                }
            }
        });

        let tx_speech_discord = tx_speech.clone();
        std::thread::spawn(move || {
            info!("Started Discord per-speaker VAD thread");
            use silero::Session;
            let mut session = Session::bundled()
                .expect("Failed to load Silero VAD model. Is ORT_DYLIB_PATH set? See README.");
            let mut pipelines: std::collections::HashMap<String, (String, SpeakerPipeline)> =
                std::collections::HashMap::new();

            while let Ok((speaker_id, speaker_name, mono_48k)) = rx_frame.recv() {
                let entry = pipelines
                    .entry(speaker_id)
                    .or_insert_with(|| (speaker_name.clone(), SpeakerPipeline::new()));
                entry.0 = speaker_name;
                let (name, pipeline) = entry;

                for segment in pipeline.push_mono_48k(&mut session, &mono_48k) {
                    info!("Speech ended for {}. Captured {} samples.", name, segment.len());
                    if tx_speech_discord.blocking_send((segment, Some(name.clone()))).is_err() {
                        return;
                    }
                }
            }
        });
    }

    let raw_mode = is_raw_mode(std::env::var("RAW_AUDIO").ok().as_deref());

    if raw_mode {
        info!("Running in RAW AUDIO mode. Whisper model will NOT be loaded.");
        while let Some((speech, speaker)) = rx_speech.recv().await {
            info!("Received speech chunk of len {} (raw mode)", speech.len());
            let start = std::time::Instant::now();
            let wav_data = create_wav_data(&speech);
            use base64::Engine;
            let b64_wav = base64::engine::general_purpose::STANDARD.encode(&wav_data);

            let mut event = serde_json::json!({
                "audio": b64_wav,
                "format": "wav"
            });
            if let Some(name) = &speaker {
                event["speaker"] = serde_json::json!(name);
            }
            match serde_json::to_vec(&event) {
                Ok(payload) => {
                    if let Err(e) = client.publish("io.user.speak.raw".to_string(), payload.into()).await {
                        error!("Failed to publish raw audio: {:?}", e);
                    } else {
                        info!("Published raw audio to NATS in {:?}", start.elapsed());
                    }
                }
                Err(e) => {
                    error!("Failed to serialize raw audio event: {:?}", e);
                }
            }
        }
    } else {
        // 6. Thread 3 (STT) inside Tokio Runtime
        info!("Loading STT model...");
        // Initialisation propre d'une structure avec mise à jour des champs qui nous intéressent
        let whisper_config = ct2rs::Config {
            // 1. Le Métal (GPU si compilé avec --features cuda, cf. stt_device)
            device: stt_device(),

            // 2. Le Moteur Mathématique
            compute_type: ct2rs::ComputeType::AUTO,

            // 3. Le Cerveau (L'ex-intra_threads)
            num_threads_per_replica: 16,

            // 4. Parallélisme Tensoriel
            tensor_parallel: false,

            // 5. Indexation Matérielle
            device_indices: vec![0],

            // 6. Gestion Asynchrone Interne
            max_queued_batches: 0,

            // 7. Affinité CPU (Pinning)
            cpu_core_offset: -1,
        };
        let model_path = std::env::var("STT_MODEL_PATH")
            .unwrap_or_else(|_| "model/whisper-small-ct2".to_string());
        let whisper = ct2rs::Whisper::new(&model_path, whisper_config)
            .with_context(|| format!("Failed to load Whisper model from {model_path}. See README."))?;
        let whisper_options = ct2rs::WhisperOptions::default();
        let stt_language: Option<String> = std::env::var("STT_LANGUAGE").ok();

        // `whisper.generate` est du calcul synchrone (CPU ou GPU) : le laisser sur la boucle
        // Tokio bloquait tout le runtime, y compris les publications NATS. En mode Discord,
        // un VAD par locuteur alimente ce même canal, donc plusieurs segments s'enchaînent.
        // On le sort donc sur un thread dédié — un seul, la sérialisation reste voulue
        // (une seule réplique CTranslate2 ; les segments d'un même tour restent ordonnés).
        // ponytail: thread simple plutôt que pool. Plafond assumé : un locuteur B attend la
        // fin de la transcription du locuteur A. Passer à N répliques si ça se voit.
        let (tx_text, mut rx_text) = mpsc::channel::<(String, Option<String>)>(32);
        std::thread::spawn(move || {
            info!("STT ready (language: {}). Waiting for speech events...",
                stt_language.as_deref().unwrap_or("auto-detect"));
            while let Some((speech, speaker)) = rx_speech.blocking_recv() {
                info!("Received speech chunk of len {}", speech.len());
                let start = std::time::Instant::now();
                let lang = stt_language.as_deref();
                match whisper.generate(&speech, lang, false, &whisper_options) {
                    Ok(result) => {
                        let text = result.join(" ");
                        info!("Transcribed in {:?}: {}", start.elapsed(), text);
                        if text.trim().is_empty() {
                            continue;
                        }
                        if tx_text.blocking_send((text, speaker)).is_err() {
                            return;
                        }
                    }
                    Err(e) => {
                        error!("Whisper transcription failed: {}. Try setting STT_LANGUAGE=fr (or en, etc.)", e);
                    }
                }
            }
        });

        while let Some((text, speaker)) = rx_text.recv().await {
            let event = TranscriptionEvent { text, speaker };
            match serde_json::to_vec(&event) {
                Ok(payload) => {
                    if let Err(e) = client.publish("io.user.speak".to_string(), payload.into()).await {
                        error!("Failed to publish transcription: {:?}", e);
                    }
                }
                Err(e) => error!("Failed to serialize transcription event: {:?}", e),
            }
        }
    }

    Ok(())
}
